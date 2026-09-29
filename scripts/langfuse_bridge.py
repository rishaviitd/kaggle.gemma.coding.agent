"""Run the offline Langfuse importer after local or Kaggle evaluations."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANGFUSE_KEYS = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
)


def _env_values(env_file: Path) -> dict[str, str]:
    values = {key: os.getenv(key, "") for key in LANGFUSE_KEYS}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in values and not values[key]:
                values[key] = value.strip().strip("\"'")
    return values


def langfuse_is_configured(env_file: Path = ROOT / ".env") -> bool:
    values = _env_values(env_file)
    return all(values.values()) and not any("your-" in value.lower() for value in values.values())


def push_to_langfuse(
    *,
    artifact_dir: Path | None = None,
    kaggle_kernel: str | None = None,
    task_ids: list[str] | None = None,
    session_id: str | None = None,
    source_platform: str,
    env_file: Path = ROOT / ".env",
) -> bool:
    """Invoke the isolated Langfuse uploader when credentials are configured."""
    if (artifact_dir is None) == (kaggle_kernel is None):
        raise ValueError("Provide exactly one of artifact_dir or kaggle_kernel")
    if not langfuse_is_configured(env_file):
        print(
            "Langfuse upload skipped: configure LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL in .env.",
            flush=True,
        )
        return False

    command = [
        "uv", "run", "--with", "langfuse>=4,<5", "--python", "3.12",
        "python", str(ROOT / "scripts/stitch_langfuse_trace.py"),
        "--env-file", str(env_file),
        "--source-platform", source_platform,
    ]
    if artifact_dir is not None:
        command.extend(["--artifact-dir", str(artifact_dir.resolve())])
    else:
        command.extend(["--kaggle-kernel", str(kaggle_kernel)])
    if session_id:
        command.extend(["--session-id", session_id])
    for task_id in task_ids or []:
        command.extend(["--task-id", task_id])

    print("Uploading completed agent trace to Langfuse...", flush=True)
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        location = str(artifact_dir.resolve()) if artifact_dir is not None else str(kaggle_kernel)
        print(
            f"Langfuse upload failed; the completed run remains available at {location}. "
            f"Retry with scripts/stitch_langfuse_trace.py. Error: {exc}",
            flush=True,
        )
        return False
    return True
