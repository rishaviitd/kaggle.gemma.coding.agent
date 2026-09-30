"""Add matching Python 3.13 Linux wheels without deleting existing task caches.

Run inside the same linux/amd64 Python 3.13 image used by the sandbox.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import parse_wheel_filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', action='append')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 13) or platform.system() != 'Linux' or platform.machine() != 'x86_64':
        parser.error('Run in docker with --platform linux/amd64 and python:3.13-slim')
    root = Path(__file__).resolve().parents[1] / 'data/assets'
    task_root = root / 'task_wheels'
    task_root.mkdir(parents=True, exist_ok=True)
    directories = ([task_root / task for task in args.task_id] if args.task_id
                   else sorted(path for path in task_root.iterdir() if path.is_dir()))
    supported = set(sys_tags())
    missing = {}
    for directory in directories:
        if not directory.is_dir():
            parser.error(f'Task wheel directory does not exist: {directory}')
        versions, compatible = set(), set()
        for wheel in directory.glob('*.whl'):
            name, version, _, tags = parse_wheel_filename(wheel.name)
            key = (name, str(version))
            versions.add(key)
            if tags & supported:
                compatible.add(key)
        for key in versions - compatible:
            missing.setdefault(key, []).append(directory)
    cache = root / '.py313-wheel-cache'
    cache.mkdir(exist_ok=True)
    def repair(item):
        (name, version), destinations = item
        requirement = f'{name}=={version}'
        print(f'Repairing {requirement} for {len(destinations)} task caches', flush=True)
        target = cache / f'{name}-{version}'
        target.mkdir(exist_ok=True)
        replacements = [w for w in target.glob('*.whl')
                        if parse_wheel_filename(w.name)[3] & supported]
        if not replacements:
            result = subprocess.run([
                sys.executable, '-m', 'pip', 'download', '--disable-pip-version-check',
                '--no-cache-dir', '--only-binary=:all:', '--no-deps',
                '--dest', str(target), requirement,
            ], capture_output=True, text=True)
            if result.returncode:
                print(f'{requirement}: {result.stderr[-1000:]}', flush=True)
                return 0, requirement
            replacements = [w for w in target.glob('*.whl')
                            if parse_wheel_filename(w.name)[3] & supported]
        if not replacements:
            return 0, requirement
        copied = 0
        for directory in destinations:
            for wheel in replacements:
                staged = directory / (wheel.name + '.part')
                shutil.copyfile(wheel, staged)
                os.replace(staged, directory / wheel.name)
                copied += 1
        return copied, None
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(repair, sorted(missing.items())))
    copied = sum(count for count, _ in results)
    failures = [failure for _, failure in results if failure]
    print(f'Added {copied} compatible wheel copies; {len(failures)} unavailable versions.', flush=True)
    if failures:
        raise SystemExit('Could not repair: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
