#!/usr/bin/env python3
"""Collect dependencies using the organizer sandbox setup rules."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_sandbox_setup():
    path = ROOT / "data" / "sandbox" / "setup.py"
    spec = importlib.util.spec_from_file_location("sandbox_setup", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workspace_root(path: Path) -> Path:
    if (path / "pyproject.toml").is_file():
        return path
    children = [child for child in path.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / "pyproject.toml").is_file():
        return children[0]
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    setup = load_sandbox_setup()
    workspace = workspace_root(args.workspace)
    dependencies = setup.collect_from_pyproject(workspace / "pyproject.toml")
    dependencies.extend(setup.collect_from_requirements_files(workspace))
    args.output.write_text("\n".join(sorted(set(dependencies))) + "\n")


if __name__ == "__main__":
    main()
