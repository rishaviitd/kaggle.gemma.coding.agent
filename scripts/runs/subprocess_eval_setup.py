"""Install offline task dependencies in swegemma's subprocess sandbox.

swegemma 0.2.11 skips its Docker dependency setup for SubprocessManager. Each
subprocess sandbox has an isolated venv, so its task wheels must be installed
and its src-layout checkout made importable before the agent or verifier runs.
"""

import os
import subprocess
from pathlib import Path

from swegemma.harness.container_setup import (
    _deduplicate_wheels,
    install_test_dependencies as _docker_install_test_dependencies,
)
from swegemma.sandbox.subprocess import SubprocessManager


def install_subprocess_dependencies(manager, sandbox_id, repo="", *, fast_path=True,
                                    config=None):
    """Prepare the task venv using only its mounted wheel cache and checkout."""
    if not isinstance(manager, SubprocessManager):
        return _docker_install_test_dependencies(
            manager, sandbox_id, repo, fast_path=fast_path, config=config,
        )

    wheels_dir = Path(config.wheels_dir) if config and config.wheels_dir else None
    if wheels_dir is None or not wheels_dir.is_dir():
        raise RuntimeError(f"offline task wheel cache missing for {repo}: {wheels_dir}")
    wheels = _deduplicate_wheels([str(path) for path in wheels_dir.glob("*.whl")])
    if not any(path.name.lower().startswith("pytest-") for path in wheels):
        raise RuntimeError(f"pytest wheel missing from task cache for {repo}: {wheels_dir}")

    paths = manager.sandboxes[sandbox_id]
    python = paths["venv"] / "bin" / "python"
    workspace = paths["workspace"]
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}

    def run(args):
        result = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(
                f"offline sandbox setup failed for {repo} (exit {result.returncode}): "
                f"{result.stderr[-2000:] or result.stdout[-2000:]}"
            )

    run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
         "--force-reinstall", *map(str, wheels)])
    # SubprocessManager sets PYTHONPATH to /workspace. Add src-layout packages
    # ahead of host site-packages without invoking an uncached build backend.
    source = workspace / "src"
    if source.is_dir():
        site_packages = list((paths["venv"] / "lib").glob("python*/site-packages"))
        if not site_packages:
            raise RuntimeError(f"sandbox site-packages missing for {repo}: {paths['venv']}")
        (site_packages[0] / "00_swegemma_source.pth").write_text(
            f"import sys; sys.path.insert(0, {str(source)!r})\n", encoding="utf-8",
        )
    run([str(python), "-c", "import pytest; print(pytest.__file__)"])
