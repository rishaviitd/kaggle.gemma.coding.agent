#!/usr/bin/env python3
"""Deterministic issue->code locator. Stdlib only. No LLM.

Usage: locate.py --issue "<issue text>" [--root /workspace]    (read-only; never writes files)

Pipeline (all generic):
 1. Parse issue: strip boilerplate, pull out concrete tokens (backticked/code
    identifiers, dotted/CamelCase/snake_case names, paths, traceback frames,
    quoted literals) and plain prose words.
 2. Index every .py file with ast into def/class chunks (+ module chunk).
 3. Score chunks: BM25 over identifier-split terms (identifier parts weighted
    more when they came from a concrete token), + definition-name match,
    + literal-string match, + traceback frame hit, + path-token match.
    Tests/docs/examples dirs and generated files are down-weighted.
 4. Emit a compact ranked list with line ranges, signature, reason.
"""
import argparse, ast, math, os, re, sys, json
from collections import Counter, defaultdict

SKIP_DIRS = {'.git', '.hg', '.tox', '.nox', 'node_modules', 'venv', '.venv', 'env',
             'build', 'dist', '__pycache__', 'site-packages', '.eggs', '.mypy_cache',
             '.pytest_cache', 'htmlcov'}
LOW_DIRS = {'test', 'tests', 'testing', 'doc', 'docs', 'docs_src', 'example', 'examples',
            'benchmark', 'benchmarks', 'bench', 'scripts', 'tools', 'script', 'demo',
            'demos', 'sample', 'samples', 'tutorial', 'tutorials', 'vendor', 'vendored',
            '_vendor', 'third_party'}
MAX_FILE = 600_000

STOP = set("""a an the and or but if then else for to of in on at by with from as is are was were be been
being it its this that these those there here we you i he she they them our your their my me do does did
done not no yes can could should would will may might must have has had having so such than too very just
also into out up down over under about after before when while where which who whom what why how all any
each some more most other only own same both few new now get got use used using one two
pr pull request fixes fix fixed close closes closed resolve resolves issue issues please thanks thank
check checklist description describe summary template tests test add added adding update updated change
changes changed make makes made currently current see like want need needs allow allows support
bug feature enhancement docs doc documentation type version python pip install code example examples
expected actual behavior behaviour error result results instead currently however because since
github com https http www org md py html""".split())

# ------------------------------------------------------------------ text utils
_split1 = re.compile(r'([a-z0-9])([A-Z])')
_split2 = re.compile(r'([A-Z]+)([A-Z][a-z])')


def stem(w):
    for suf, rep in (('ies', 'y'), ('ing', ''), ('ed', ''), ('es', ''), ('s', '')):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if suf == 'es' and not w[:-2].endswith(('s', 'x', 'z', 'ch', 'sh')):
                continue
            return w[:-len(suf)] + rep
    return w


def split_ident(s):
    s = _split2.sub(r'\1_\2', _split1.sub(r'\1_\2', s))
    return [p.lower() for p in re.split(r'[^A-Za-z0-9]+', s) if p]


def parts(ident):
    """Whole-identifier term (if compound) + its stemmed word parts."""
    ps = split_ident(ident)
    out = [stem(p) for p in ps if len(p) > 1 or p.isdigit()]
    if len(ps) > 1:
        out.append('=' + ''.join(ps))  # compound key, matches exact identifier
    return out


IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def body_terms(text):
    c = Counter()
    for m in IDENT.finditer(text):
        for p in parts(m.group()):
            c[p] += 1
    return c


# ------------------------------------------------------------------ issue parsing
def clean_issue(text):
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.S)
    text = re.sub(r'https?://\S+', ' ', text)
    text = text.replace('\r', '')
    # drop PR/issue template checkbox lines ("- [x] ...") and bare template headings
    text = re.sub(r'(?m)^\s*[-*]\s*\[[ xX]\].*$', ' ', text)
    text = re.sub(r'(?m)^#{1,6}\s*(type of changes?|checklist|pull request|description|summary)\s*$', ' ', text, flags=re.I)
    return text


def parse_issue(text):
    text = clean_issue(text)
    q = {'ids': Counter(), 'words': Counter(), 'paths': [], 'frames': [], 'lits': [], 'raw_ids': set()}
    # traceback frames
    for m in re.finditer(r'File "([^"]+)", line (\d+)(?:, in (\w+))?', text):
        q['frames'].append((m.group(1), int(m.group(2)), m.group(3)))
    # paths
    for m in re.finditer(r'[\w./-]+\.py\b(?::(\d+))?', text):
        q['paths'].append(m.group().split(':')[0])
    # quoted literals / error messages (in quotes or backticks w/ spaces)
    for m in re.finditer(r'"([^"\n]{6,120})"|\'([^\'\n]{6,120})\'|`([^`\n]{6,120})`', text):
        s = (m.group(1) or m.group(2) or m.group(3)).strip()
        if ' ' in s and not re.fullmatch(r'[\w.()\[\], =]+', s):
            q['lits'].append(s)
        elif ' ' in s and len(s.split()) >= 3:
            q['lits'].append(s)
    # exception lines "SomeError: message"
    for m in re.finditer(r'\b\w*(?:Error|Exception|Warning)\b: ([^\n]{8,120})', text):
        q['lits'].append(m.group(1).strip())
    # concrete identifiers
    concrete = set()
    for m in re.finditer(r'`([^`\n]+)`', text):
        concrete.update(IDENT.findall(m.group(1)))
    for m in re.finditer(r'```.*?```', text, flags=re.S):
        concrete.update(IDENT.findall(m.group()))
    for m in IDENT.finditer(text):
        w = m.group()
        if '_' in w.strip('_') or re.search(r'[a-z][A-Z]', w) or re.fullmatch(r'[A-Z][a-z]+[A-Z]\w*', w):
            concrete.add(w)
    for m in re.finditer(r'\b(\w+)\(\)', text):
        concrete.add(m.group(1))
    # dotted names a.b.c -> each component
    for m in re.finditer(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', text):
        if not re.search(r'\.(py|md|com|org|io|txt|json|yml|yaml|toml|html)$', m.group()):
            concrete.update(m.group().split('.'))
    # drop language keywords / noise
    concrete = {c for c in concrete if len(c) > 2 and c.lower() not in STOP
                and c not in ('self', 'cls', 'None', 'True', 'False', 'def', 'class', 'return',
                              'import', 'from', 'print', 'str', 'int', 'dict', 'list', 'bool',
                              'assert', 'with', 'async', 'await', 'pass', 'lambda', 'type', 'yield')}
    q['raw_ids'] = concrete
    for c in concrete:
        for p in parts(c):
            q['ids'][p] += 1
    # prose words (everything, incl. in code blocks, minus stopwords)
    for m in IDENT.finditer(text):
        for p in split_ident(m.group()):
            if len(p) > 2 and p not in STOP:
                q['words'][stem(p)] += 1
    title = next((l for l in text.split('\n') if l.strip()), '')
    q['title'] = Counter(stem(p) for m in IDENT.finditer(title) for p in split_ident(m.group())
                         if len(p) > 2 and p not in STOP)
    q['n_concrete'] = len(concrete) + len(q['frames']) + len(q['paths']) + len(q['lits'])
    return q


# ------------------------------------------------------------------ repo indexing
class Chunk:
    __slots__ = ('file', 'qual', 'name', 'kind', 'start', 'end', 'sig', 'tf', 'len', 'ntf',
                 'strs', 'penal', 'lines_own')


def file_penalty(rel):
    segs = rel.lower().split('/')
    base = segs[-1]
    p = 1.0
    if any(s in LOW_DIRS for s in segs[:-1]):
        p = 0.35
    if base.startswith('test_') or base.endswith('_test.py') or base == 'conftest.py':
        p = min(p, 0.3)
    if base in ('setup.py', 'conf.py', 'noxfile.py') or base.endswith(('_pb2.py', '_generated.py')) or 'generated' in base:
        p = min(p, 0.3)
    if base == '__init__.py':
        p *= 0.8
    return p


def walk(repo):
    for dp, dn, fn in os.walk(repo):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.endswith('.egg-info'))
        for f in sorted(fn):
            if f.endswith('.py'):
                p = os.path.join(dp, f)
                try:
                    if os.path.getsize(p) <= MAX_FILE:
                        yield p
                except OSError:
                    pass


def sig_of(node, lines):
    s = node.lineno
    out = []
    for i in range(s - 1, min(s + 5, len(lines))):
        out.append(lines[i].strip())
        if lines[i].rstrip().endswith(':') or lines[i].rstrip().endswith(':  #'):
            break
    t = out[0] if out else ''
    return t if len(t) <= 80 else t[:77] + '...'


def index_repo(repo):
    chunks = []
    for path in walk(repo):
        rel = os.path.relpath(path, repo).replace(os.sep, '/')
        try:
            src = open(path, encoding='utf-8', errors='replace').read()
            tree = ast.parse(src)
        except (SyntaxError, ValueError, RecursionError):
            continue
        lines = src.split('\n')
        pen = file_penalty(rel)
        ptoks = Counter()
        for seg in re.split(r'[/]', rel[:-3]):
            for p in parts(seg):
                ptoks[p] += 1

        def mk(node, qual, name, kind, own_ranges):
            c = Chunk()
            c.file, c.qual, c.name, c.kind = rel, qual, name, kind
            c.start = node.lineno if hasattr(node, 'lineno') else 1
            c.end = getattr(node, 'end_lineno', len(lines))
            if kind == 'module':
                c.start, c.end = 1, len(lines)
            # text = own lines only (exclude nested defs) so classes are small chunks
            txt = []
            for (a, b) in own_ranges:
                txt.append('\n'.join(lines[a - 1:b]))
            text = '\n'.join(txt)
            c.sig = sig_of(node, lines) if kind != 'module' else ''
            tf = Counter()
            # name gets extra weight: add name parts x4, path parts x1
            for p in parts(name):
                tf[p] += 4
            for p in parts(qual.split('.')[0]) if '.' in qual else []:
                tf[p] += 2
            tf.update(ptoks)
            tf.update(body_terms(text))
            c.tf = tf
            c.len = sum(tf.values())
            c.strs = set()
            c.penal = pen
            c.lines_own = own_ranges
            return c

        def own_ranges_of(node, nested):
            s, e = node.lineno, node.end_lineno
            if not nested:
                return [(s, e)]
            res, cur = [], s
            for n in sorted(nested, key=lambda n: n.lineno):
                st = (n.decorator_list[0].lineno if getattr(n, 'decorator_list', None) else n.lineno)
                if st > cur:
                    res.append((cur, st - 1))
                cur = n.end_lineno + 1
            if cur <= e:
                res.append((cur, e))
            return res

        def visit(body, prefix, parent_kind):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qual = prefix + node.name
                    kind = 'class' if isinstance(node, ast.ClassDef) else 'def'
                    nested = [n for n in ast.walk(node) if n is not node and isinstance(
                        n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                    # only direct nested defs (not grand-children) for exclusion
                    direct = [n for n in ast.iter_child_nodes(node)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                    if kind == 'def':
                        # include inner functions' text in def; keep inner defs out of list
                        # unless small? keep simple: functions own everything incl. nested
                        direct_ex = []
                    else:
                        direct_ex = direct
                    c = mk(node, qual, node.name, kind, own_ranges_of(node, direct_ex))
                    # add string constants for literal matching (own lines only)
                    chunks.append(c)
                    if kind == 'class':
                        visit(node.body, qual + '.', 'class')
                elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    for fld in ('body', 'orelse', 'finalbody', 'handlers'):
                        sub = getattr(node, fld, None)
                        if sub:
                            subs = []
                            for s in sub:
                                subs.extend(s.body if isinstance(s, ast.ExceptHandler) else [s])
                            visit(subs, prefix, parent_kind)
        before = len(chunks)
        visit(tree.body, '', 'module')
        # module chunk = top-level lines not covered by defs
        covered = set()
        for c in chunks[before:]:
            if c.kind == 'def' or True:
                pass
        defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        rng, cur = [], 1
        for n in sorted(defs, key=lambda n: n.lineno):
            st = n.decorator_list[0].lineno if n.decorator_list else n.lineno
            if st > cur:
                rng.append((cur, st - 1))
            cur = n.end_lineno + 1
        if cur <= len(lines):
            rng.append((cur, len(lines)))
        m = ast.Module(body=[], type_ignores=[])
        m.lineno = 1
        m.end_lineno = len(lines)
        c = mk(m, '<module>', '', 'module', rng)
        chunks.append(c)
    return chunks


# string-literal index (separate pass over source text per file, lazily)
_src_cache = {}


def chunk_text(c, repo):
    key = c.file
    if key not in _src_cache:
        _src_cache[key] = open(os.path.join(repo, c.file), encoding='utf-8', errors='replace').read().split('\n')
    L = _src_cache[key]
    return '\n'.join('\n'.join(L[a - 1:b]) for a, b in c.lines_own)


# ------------------------------------------------------------------ scoring
def bm25_scores(chunks, qterms, k1=1.2, b=0.6):
    N = len(chunks)
    df = Counter()
    for c in chunks:
        for t in c.tf:
            df[t] += 1
    avg = sum(c.len for c in chunks) / max(N, 1)
    scores = [0.0] * N
    why = [dict() for _ in range(N)]
    for t, qw in qterms.items():
        d = df.get(t, 0)
        if not d:
            continue
        idf = math.log(1 + (N - d + 0.5) / (d + 0.5))
        for i, c in enumerate(chunks):
            f = c.tf.get(t)
            if not f:
                continue
            s = idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * c.len / avg))
            s *= qw
            scores[i] += s
            why[i][t] = s
    return scores, why


def score(chunks, q, repo, variant='hybrid'):
    if variant == 'grep':
        return score_grep(chunks, q, repo)
    if variant == 'bm25':
        qt = Counter()
        for w, n in q['words'].items():
            qt[w] += 1
        sc, why = bm25_scores(chunks, qt)
        sc = [s * (1.0) for s in sc]
        return sc, why, {}
    # hybrid
    qt = Counter()
    for w, n in q['words'].items():
        qt[w] += 1.0
    for t in q['title']:
        qt[t] += 0.5
    for t, n in q['ids'].items():
        qt[t] += 1.5 if t.startswith('=') else 1.0   # concrete identifier parts count extra
    sc, why = bm25_scores(chunks, qt)
    extra = {}
    raw = q['raw_ids']
    rawl = {r.lower() for r in raw}
    # definition-name exact matches (strong): chunk name or qual component equals identifier
    # idf-like damping: a name defined in many places (e.g. __init__) is weak evidence
    namecount = Counter(c.name for c in chunks)
    for i, c in enumerate(chunks):
        if c.kind == 'module':
            continue
        bonus = 0.0
        if c.name in raw and c.name not in ('__init__',):
            bonus += 12.0 / math.sqrt(namecount[c.name])
            extra.setdefault(i, []).append('defines `%s`' % c.name)
        elif c.qual in raw:
            bonus += 12.0
        else:
            comps = c.qual.split('.')
            if len(comps) > 1 and comps[0] in raw:
                bonus += 2.0
        if bonus:
            sc[i] += bonus
    # path tokens named in the issue
    for pth in q['paths']:
        pth = pth.lstrip('./')
        for i, c in enumerate(chunks):
            if c.file.endswith(pth) or pth.endswith(c.file):
                sc[i] += 4.0 if c.kind != 'module' else 1.0
                extra.setdefault(i, []).append('path named in issue')
    # traceback frames: chunk enclosing the line
    for (fp, ln, fn) in q['frames']:
        fp = fp.replace('\\', '/')
        for i, c in enumerate(chunks):
            if c.kind == 'module':
                continue
            if (fp.endswith('/' + c.file) or fp == c.file) and c.start <= ln <= c.end:
                sc[i] += 25.0 / (1 + 0.0 * (c.end - c.start))
                extra.setdefault(i, []).append('traceback frame line %d' % ln)
    # literals present in source
    if q['lits']:
        lits = [l for l in q['lits']][:8]
        for i, c in enumerate(chunks):
            if c.kind == 'module':
                continue
            txt = chunk_text(c, repo)
            for l in lits:
                core = l[:60]
                if core in txt or (len(l) > 25 and l[:25] in txt):
                    sc[i] += 8.0
                    extra.setdefault(i, []).append('contains literal "%s"' % l[:30])
                    break
    return sc, why, extra


def score_grep(chunks, q, repo):
    """Baseline: count exact word-boundary occurrences of concrete identifiers."""
    raw = q['raw_ids']
    sc = [0.0] * len(chunks)
    if not raw:
        return sc, [dict() for _ in chunks], {}
    pat = re.compile(r'\b(?:' + '|'.join(re.escape(r) for r in raw) + r')\b')
    why = [dict() for _ in chunks]
    for i, c in enumerate(chunks):
        n = len(pat.findall(chunk_text(c, repo)))
        sc[i] = n * (c.penal if False else 1.0)
        if c.name in raw:
            sc[i] += 3
    return sc, why, {}


# ------------------------------------------------------------------ ranking / output
def rank(chunks, q, repo, variant='hybrid'):
    sc, why, extra = score(chunks, q, repo, variant)
    if variant != 'grep':
        sc = [s * c.penal for s, c in zip(sc, chunks)]
    else:
        sc = [s * (c.penal if c.penal < 1 else 1.0) for s, c in zip(sc, chunks)]
    order = sorted(range(len(chunks)), key=lambda i: (-sc[i], chunks[i].file, chunks[i].start))
    # file ranking: best chunk + 0.25*second-best
    byfile = defaultdict(list)
    for i in order:
        if sc[i] > 0:
            byfile[chunks[i].file].append(i)
    fscore = {}
    for f, idxs in byfile.items():
        s = [sc[i] for i in idxs[:3]]
        fscore[f] = s[0] + (0.25 * s[1] if len(s) > 1 else 0)
    return sc, why, extra, order, fscore


def pick(chunks, sc, order, fscore, k_files=5, per_file=2):
    files = sorted(fscore, key=lambda f: (-fscore[f], f))[:k_files]
    items = []
    for f in files:
        cs = [i for i in order if chunks[i].file == f and sc[i] > 0 and chunks[i].kind != 'module'][:per_file]
        if not cs:
            cs = [i for i in order if chunks[i].file == f and sc[i] > 0][:1]
        # drop 2nd chunk if far weaker
        cs = [i for j, i in enumerate(cs) if j == 0 or sc[i] >= 0.5 * sc[cs[0]]]
        for i in cs:
            items.append(i)
    return items


def siblings(chunks, i, repo):
    c = chunks[i]
    if c.kind == 'module':
        return []
    out = []
    t0 = set(c.tf)
    for j, d in enumerate(chunks):
        if j == i or d.kind == 'module':
            continue
        same_name = d.name == c.name and d.file != c.file
        twin = d.file == c.file and d.name != c.name and d.kind == c.kind
        if not (same_name or twin):
            continue
        t1 = set(d.tf)
        jac = len(t0 & t1) / max(1, len(t0 | t1))
        if (same_name and jac > 0.4) or (twin and jac > 0.75 and len(t0) > 12):
            out.append((jac, d))
    out.sort(key=lambda x: -x[0])
    return [d for _, d in out[:2]]


def reason(chunks, i, why, extra, q):
    r = []
    r.extend(extra.get(i, [])[:2])
    w = why[i]
    top = sorted((t for t in w if not t.startswith('=')), key=lambda t: -w[t])[:3]
    if top:
        r.append('terms: ' + ','.join(top))
    return '; '.join(r) if r else 'weak lexical match'


def locate(issue, repo, variant='hybrid', chunks=None, k_files=5):
    q = parse_issue(issue)
    if chunks is None:
        chunks = index_repo(repo)
    sc, why, extra, order, fscore = rank(chunks, q, repo, variant)
    items = pick(chunks, sc, order, fscore, k_files)
    top = sc[items[0]] if items else 0
    strong = any(('defines' in e or 'traceback' in e or 'literal' in e or 'path named' in e)
                 for i in items[:2] for e in extra.get(i, []))
    if q['n_concrete'] == 0:
        conf = 'low'
    elif strong:
        conf = 'high'
    else:
        conf = 'medium'
    return q, chunks, sc, why, extra, items, conf


def render(q, chunks, sc, why, extra, items, conf, repo, max_chars=2000):
    out = []
    if conf == 'low':
        out.append('confidence: low (issue has no concrete identifiers/paths/tracebacks; '
                   'ranking is keyword-only, verify with your own grep)')
    else:
        out.append('confidence: %s' % conf)
    seen_sib = set()
    for n, i in enumerate(items, 1):
        c = chunks[i]
        loc = '%s:%d-%d' % (c.file, c.start, c.end)
        label = c.qual if c.kind != 'module' else '(module-level)'
        out.append('%d. %s  %s' % (n, loc, label))
        sg = c.sig if c.kind != 'module' else ''
        r = reason(chunks, i, why, extra, q)
        out.append('   %s%s' % (('`' + sg + '` -- ') if sg else '', r))
        for d in siblings(chunks, i, repo):
            key = (d.file, d.qual)
            if key in seen_sib or any(chunks[x] is d for x in items):
                continue
            seen_sib.add(key)
            out.append('   also check twin: %s:%d-%d %s' % (d.file, d.start, d.end, d.qual))
    s = '\n'.join(out)
    # keep within budget by trimming whole trailing lines
    lines = s.split('\n')
    while len('\n'.join(lines)) > max_chars and len(lines) > 3:
        lines.pop()
    return '\n'.join(lines[:40])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--issue', default='')
    ap.add_argument('--root', default='/workspace')
    a, _ = ap.parse_known_args()
    if not a.issue.strip():
        print('confidence: low\nno issue text given; pass the full issue as --issue')
        return
    res = locate(a.issue, a.root)
    print(render(*res, a.root, max_chars=1300))


if __name__ == '__main__':
    main()
