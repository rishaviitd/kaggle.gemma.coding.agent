"""One read-only Codex CLI request per trace digest."""

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .digest import SYSTEM_PROMPT
from .schema import SCHEMA

DEFAULT_MODEL = 'gpt-6.1-sol'


@dataclass(frozen=True)
class ReviewResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class CodexCLIClient:
    def __init__(self, executable=None, timeout=240):
        self.executable = executable or shutil.which('codex')
        if not self.executable:
            raise RuntimeError('Codex CLI is not installed')
        self.timeout = timeout

    def authenticated(self):
        result = subprocess.run([self.executable, 'login', 'status'],
                                capture_output=True, timeout=10)
        return result.returncode == 0

    def review(self, *, model, digest):
        prompt = (SYSTEM_PROMPT + '\n\nReview only this JSON digest. Do not use tools '
                  'or inspect local files. Return only the schema JSON.\n\n'
                  + json.dumps(digest, ensure_ascii=False, separators=(',', ':')))
        with TemporaryDirectory(prefix='gemma-review-') as directory:
            root = Path(directory)
            schema_file, answer_file = root / 'schema.json', root / 'answer.json'
            schema_file.write_text(json.dumps(SCHEMA))
            cmd = [self.executable, 'exec', '--model', model,
                   '--sandbox', 'read-only', '--skip-git-repo-check',
                   '--ephemeral', '--ignore-user-config', '--ignore-rules',
                   '--config', 'model_reasoning_effort=low',
                   '--output-schema', str(schema_file),
                   '--output-last-message', str(answer_file), '--json', '-']
            result = subprocess.run(cmd, input=prompt, text=True,
                                    capture_output=True, cwd=root, timeout=self.timeout)
            if result.returncode:
                raise RuntimeError(f'Codex CLI exited with status {result.returncode}')
            if not answer_file.is_file():
                raise RuntimeError('Codex CLI did not produce a review response')
            usage = {}
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('type') == 'turn.completed':
                    usage = event.get('usage') or {}
            return ReviewResponse(answer_file.read_text(), usage.get('input_tokens'),
                                  usage.get('output_tokens'))
