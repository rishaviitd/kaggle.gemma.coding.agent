import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
import handoff_snapshot as snap  # noqa: E402
from handoff_llm import config_fingerprint  # noqa: E402


def write_agent(root: Path) -> Path:
    agent = root / 'agent'
    agent.mkdir()
    (agent / 'agent.yaml').write_text('model: gemma4\n')
    return agent


def sample(agent: Path, sandbox: str = 'subprocess') -> dict:
    return dict(
        handoff={'switch_after': 3, 'agent_calls': 4, 'student_calls': 3, 'teacher_calls': 0},
        student_responses=[{'content': {'role': 'model', 'parts': [{'text': 'hi'}]}}],
        session_events=[{'author': 'user'}],
        vllm_exchanges=[{'request': {}, 'response': {}}],
        manifest=config_fingerprint(agent, sandbox),
    )


class SnapshotTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            data = sample(agent)
            snap.write_snapshot(out, **data)
            loaded = snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')
            self.assertEqual(loaded['handoff'], data['handoff'])
            self.assertEqual(loaded['student_responses'], data['student_responses'])

    def test_refuses_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent))
            (out / 'session_events.json').unlink()
            with self.assertRaisesRegex(ValueError, 'session_events.json'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')

    def test_refuses_docker_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent, sandbox='docker'))
            with self.assertRaisesRegex(ValueError, 'subprocess'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')

    def test_refuses_config_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = write_agent(Path(tmp))
            out = Path(tmp) / 'snapshot'
            snap.write_snapshot(out, **sample(agent))
            (agent / 'agent.yaml').write_text('model: gemma4\ntools: []\n')
            with self.assertRaisesRegex(ValueError, 'config'):
                snap.read_snapshot(out, agent_dir=agent, sandbox='subprocess')


if __name__ == '__main__':
    unittest.main()
