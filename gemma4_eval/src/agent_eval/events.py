"""Conservative command and patch classifiers; this module executes nothing."""
import re

EDIT_TOOLS = {'edit_file', 'write_file'}
GRAPH_TOOLS = {'get_code_neighbors', 'get_code_subgraph', 'search_similar_code', 'code_analyzer_agent'}

def command_category(command):
    if re.search(r'\b(?:pytest|unittest)\b|\bpython\d?(?:\.\d+)?\s+(?:[^;\n]*[/\\])?[^\s;]*repro[^\s;]*\.py|\bassert\s+', command):
        return 'test'
    if re.search(r'\b(?:rg|grep|find|git\s+grep)\b', command):
        return 'search'
    if re.search(r'\b(?:cat|sed\s+-n|head|tail)\b', command):
        return 'read'
    if re.search(r'\bgit\s+(?:diff|status)\b', command):
        return 'inspect_patch'
    return 'command'

def mutation_hint(command):
    # A shell write is only a hypothesis; never a confirmed source edit.
    return bool(re.search(r'\.write_text\s*\(|\.write_bytes\s*\(|\bsed\s+-i\b|\b(?:tee|cp|mv|rm)\s|(?<![\d>])>{1,2}\s*(?!&|/dev/null)', command))

def source_path(path):
    p = str(path or '').lower()
    return bool(p) and not (p.startswith('/tmp/') or re.search(r'(^|/)(?:tests?|docs?|\.github)/|(^|/)(?:test_|repro|notes)', p)) and p.endswith(('.py','.js','.ts','.tsx','.jsx','.go','.rs','.java','.c','.cpp','.h','.rb','.cs','.sh','.vue'))

def parse_test_counts(text):
    def count(pattern):
        hits = re.findall(pattern, text, re.I | re.M)
        return int(hits[-1]) if hits else None
    passed = count(r'\b(\d+)\s+passed\b')
    failed = count(r'\b(\d+)\s+failed\b')
    if failed is None:
        failed = count(r'FAILED\s*\([^)]*failures=(\d+)')
    if failed is None and passed is not None:
        failed = 0
    return passed, failed

def parse_patch(patch):
    """Validate unified hunk lengths, including git file metadata/binary patches."""
    lines = patch.splitlines()
    files, added, deleted, hunks, valid = [], 0, 0, 0, True
    remaining = None
    old_path = None
    for line in lines:
        if line.startswith('--- '):
            old_path=re.sub(r'^a/', '', line[4:].split('\t')[0])
        if line.startswith('+++ /dev/null') and old_path and old_path!='/dev/null':
            files.append(old_path)
        if line.startswith('+++ ') and line[4:] != '/dev/null':
            files.append(re.sub(r'^b/', '', line[4:].split('\t')[0]))
        if line.startswith('diff --git '):
            m = re.match(r'diff --git a/(.*?) b/(.*)', line)
            if m:
                files.append(m.group(2))
        m = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if m:
            if remaining is not None and remaining != [0, 0]:
                valid = False
            remaining = [int(m[2] or 1), int(m[4] or 1)]
            hunks += 1
        elif remaining is not None and line[:1] in (' ', '+', '-'):
            if line.startswith('--- ') or line.startswith('+++ '):
                continue
            if line[0] != '+':
                remaining[0] -= 1
            if line[0] != '-':
                remaining[1] -= 1
            added += line[0] == '+'
            deleted += line[0] == '-'
    if remaining is not None and remaining != [0, 0]:
        valid = False
    ok = bool(files) and valid and (hunks > 0 or any(x in patch for x in ('GIT binary patch','Binary files','rename from','new file mode','deleted file mode')))
    return dict(has_patch=bool(patch.strip()), patch_bytes=len(patch.encode()), modified_files=sorted(set(files)), added_lines=added, deleted_lines=deleted, diff_parse_ok=ok if patch.strip() else None, apply_status='not_verified')
