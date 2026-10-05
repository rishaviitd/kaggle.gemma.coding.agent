"""Print a compact outline of a Python file: each class/function with its line range.

Usage (inside the sandbox via run_skill_script):
    outline.py <path> [--symbol NAME] [--root /workspace]

Relative paths resolve against --root (default /workspace), because skill
scripts run from a temporary directory. Output is plain text, one definition
per line, e.g. "L1215-1290 def APIRouter.add_api_route(self, path, endpoint)".
Use the line range with read_file(start_line, end_line).
"""

import argparse
import ast
import os
import re
import sys

MAX_ITEMS = 300          # hard cap on listed definitions
SIG_ITEMS = 120          # above this many definitions, list names only
MAX_LINE = 100           # max characters per outline line
DEF_RE = re.compile(r'^(\s*)(async\s+def|def|class)\s+(\w+)\s*([^:]*)')


def _sig(node):
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        bases += [ast.unparse(k) for k in node.keywords]
        return f'({", ".join(bases)})' if bases else ''
    sig = f'({ast.unparse(node.args)})'
    if node.returns is not None:
        sig += f' -> {ast.unparse(node.returns)}'
    return sig


def _kind(node):
    if isinstance(node, ast.ClassDef):
        return 'class'
    if isinstance(node, ast.AsyncFunctionDef):
        return 'async def'
    return 'def'


def _walk(body, prefix, out):
    for node in body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        name = prefix + node.name
        out.append((start, node.end_lineno, _kind(node), name, _sig(node)))
        if isinstance(node, ast.ClassDef):
            _walk(node.body, name + '.', out)


def ast_items(source):
    out = []
    _walk(ast.parse(source).body, '', out)
    return out


def regex_items(lines):
    out = []
    for i, line in enumerate(lines, 1):
        m = DEF_RE.match(line)
        if m:
            kind = 'async def' if m.group(2).startswith('async') else m.group(2)
            out.append((i, None, kind, m.group(3), m.group(4).strip()))
    return out


def fmt(item, with_sig=True):
    start, end, kind, name, sig = item
    rng = f'L{start}-{end}' if end else f'L{start}'
    line = f'{rng} {kind} {name}'
    if with_sig and sig:
        line += sig
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE - 3] + '...'
    return line


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('path')
    p.add_argument('--symbol')
    p.add_argument('--root', default='/workspace')
    a = p.parse_args(argv)

    path = a.path if os.path.isabs(a.path) else os.path.join(a.root, a.path)
    if not os.path.isfile(path):
        print(f'{a.path}: file not found (check the path with ls or git ls-files).')
        return 0
    if not path.endswith('.py'):
        print(f'{a.path}: not a python file; use read_file with start_line/end_line.')
        return 0

    with open(path, encoding='utf-8', errors='replace') as f:
        source = f.read()
    lines = source.splitlines()
    note = ''
    try:
        items = ast_items(source)
    except SyntaxError:
        items = regex_items(lines)
        note = ' (syntax error, approximate outline: start lines only)'

    if a.symbol:
        hits = [it for it in items
                if it[3] == a.symbol or it[3].endswith('.' + a.symbol)]
        if not hits:
            print(f'{a.symbol}: not found in {a.path}; run outline without --symbol.')
            return 0
        for it in hits:
            print(fmt(it))
        return 0

    with_sig = len(items) <= SIG_ITEMS
    if not with_sig:
        note += ' (names only; use --symbol NAME for a signature)'
    print(f'{a.path}: {len(lines)} lines, {len(items)} definitions{note}')
    for it in items[:MAX_ITEMS]:
        print(fmt(it, with_sig))
    if len(items) > MAX_ITEMS:
        print(f'... {len(items) - MAX_ITEMS} more definitions; use --symbol NAME or grep -n.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
