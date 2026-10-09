#!/usr/bin/env python3
"""Pre-submit reviewer: static checks on `git diff HEAD` plus a capped run of related tests.

Usage: review.py [--root /workspace] [--max-seconds 60]
Stdlib only. Never modifies the working tree: tests run with bytecode and the pytest cache
disabled, and the pristine-HEAD baseline lives in a temp dir outside the repo.
"""
import argparse, ast, builtins, os, re, shutil, signal, subprocess, sys, tempfile, time

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__path__",
                                 "__package__", "__loader__", "__builtins__", "__class__",
                                 "__debug__", "WindowsError", "reveal_type", "__annotations__"}
TEST_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_tests?\.py$|(^|/)conftest\.py$")
SCRATCH_RE = re.compile(r"(^|/)_?(repro|reproduce|scratch|tmp|temp|debug|check|verify|try|poc|demo)[a-z0-9_]*\.(py|sh|txt|json|log|md)$", re.I)
TEST_DIRS = {"tests", "test", "testing"}
SKIP_DIRS = {".git", "node_modules", "docs", "docs_src", "examples", "build", "dist", ".venv", "venv",
             "__pycache__", "site-packages"}


def git(repo, *a, check=False):
    r = subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, errors="replace")
    return r.stdout


def read(repo, p):
    try:
        with open(os.path.join(repo, p), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class Change:
    def __init__(self, path, status, old, new, added, removed, hunks):
        self.path, self.status, self.old, self.new = path, status, old, new
        self.added = added        # list[(lineno_new, text)]
        self.removed = removed    # list[(lineno_old, text)]
        self.hunks = hunks        # list[(added_n, removed_n)]


def load_changes(repo):
    names = git(repo, "diff", "HEAD", "--name-status", "--no-renames").splitlines()
    status = {}
    for l in names:
        p = l.split("\t")
        if len(p) >= 2:
            status[p[-1]] = p[0][0]
    for u in git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        if u and not u.endswith("/"):
            status[u] = "U"
    out = []
    for path, st in sorted(status.items()):
        old = None if st in "AU" else git(repo, "show", "HEAD:" + path)
        new = None if st == "D" else read(repo, path)
        if st == "U":
            lines = (new or "").splitlines()
            added = [(i + 1, t) for i, t in enumerate(lines)]
            removed, hunks = [], [(len(lines), 0)]
        else:
            d = git(repo, "diff", "HEAD", "-U0", "--no-renames", "--", path)
            added, removed, hunks = [], [], []
            on = ro = 0; cur = None
            for l in d.splitlines():
                m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", l)
                if m:
                    ro, on = int(m.group(1)), int(m.group(2)); cur = [0, 0]; hunks.append(cur); continue
                if cur is None or l.startswith(("---", "+++")) and False:
                    continue
                if l.startswith("+"):
                    added.append((on, l[1:])); on += 1; cur[0] += 1
                elif l.startswith("-"):
                    removed.append((ro, l[1:])); ro += 1; cur[1] += 1
        out.append(Change(path, st, old, new, added, removed, hunks))
    return out


def is_test(p): return bool(TEST_RE.search(p))


def is_py(p): return p.endswith(".py")


class _Scope:
    def __init__(self, kind, parent):
        self.kind, self.parent, self.names = kind, parent, set()
        self.star = False


def _targets(node, out):
    if isinstance(node, ast.Name): out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts: _targets(e, out)
    elif isinstance(node, ast.Starred): _targets(node.value, out)


def undefined_names(tree):
    """Return {name: lineno} for names loaded but bound nowhere visible (order-insensitive)."""
    scopes = {}   # id(node) -> _Scope
    loads = []    # (name, lineno, scope)

    def build(node, scope):
        for ch in ast.iter_child_nodes(node):
            visit(ch, scope)

    def bind_args(a, sc):
        for x in a.posonlyargs + a.args + a.kwonlyargs: sc.names.add(x.arg)
        if a.vararg: sc.names.add(a.vararg.arg)
        if a.kwarg: sc.names.add(a.kwarg.arg)

    def visit(n, scope):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope.names.add(n.name)
            for d in n.decorator_list: visit(d, scope)
            a = n.args
            for d in a.defaults + [k for k in a.kw_defaults if k]: visit(d, scope)
            for x in a.posonlyargs + a.args + a.kwonlyargs + [y for y in (a.vararg, a.kwarg) if y]:
                if x.annotation: visit(x.annotation, scope)
            if n.returns: visit(n.returns, scope)
            sc = _Scope("func", scope); bind_args(a, sc)
            for s in n.body: visit(s, sc)
        elif isinstance(n, ast.Lambda):
            sc = _Scope("func", scope); bind_args(n.args, sc)
            for d in n.args.defaults + [k for k in n.args.kw_defaults if k]: visit(d, scope)
            visit(n.body, sc)
        elif isinstance(n, ast.ClassDef):
            scope.names.add(n.name)
            for d in n.decorator_list + n.bases + [k.value for k in n.keywords]: visit(d, scope)
            sc = _Scope("class", scope)
            for s in n.body: visit(s, sc)
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            sc = _Scope("func", scope)
            for g in n.generators:
                _targets(g.target, sc.names)
            first = True
            for g in n.generators:
                visit(g.iter, scope if first else sc); first = False
                for c in g.ifs: visit(c, sc)
            if isinstance(n, ast.DictComp): visit(n.key, sc); visit(n.value, sc)
            else: visit(n.elt, sc)
        elif isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load): loads.append((n.id, n.lineno, scope))
            else: scope.names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                if al.name == "*": scope.star = True
                else: scope.names.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            scope.names.update(n.names)
            # a `global x` makes x a module-level binding as well
            s = scope
            while s.parent: s = s.parent
            if isinstance(n, ast.Global): s.names.update(n.names)
        elif isinstance(n, ast.ExceptHandler):
            if n.name: scope.names.add(n.name)
            build(n, scope)
        elif isinstance(n, ast.NamedExpr):
            s = scope
            while s.kind == "class" and s.parent: s = s.parent
            _targets(n.target, s.names); visit(n.value, scope)
        elif n.__class__.__name__ in ("MatchAs", "MatchStar"):
            if getattr(n, "name", None): scope.names.add(n.name)
            build(n, scope)
        elif n.__class__.__name__ == "MatchMapping":
            if getattr(n, "rest", None): scope.names.add(n.rest)
            build(n, scope)
        else:
            build(n, scope)

    mod = _Scope("module", None)
    build(tree, mod)
    bad = {}
    for name, ln, sc in loads:
        s, first, ok = sc, True, False
        while s:
            if s.star: ok = True; break
            if name in s.names and (s.kind != "class" or first): ok = True; break
            first = False; s = s.parent
        if not ok and name not in BUILTINS:
            bad.setdefault(name, ln)
    return bad


def parse(src):
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError, RecursionError):
        return None


def sh(cmd, cwd, timeout=30):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def is_test_path(p):
    parts = p.split("/")
    b = parts[-1]
    return (b.startswith("test_") or b.endswith("_test.py") or b == "conftest.py"
            or any(x in TEST_DIRS for x in parts[:-1]))


def module_names(repo, path):
    """dotted names under which `path` is importable (outermost package chain)."""
    parts = path[:-3].split("/")
    names = set()
    for i in range(len(parts)):
        # parts[i:] is importable if every dir in it has __init__.py (or it is a single top-level module)
        chain = parts[i:-1]
        ok = all(os.path.exists(os.path.join(repo, *parts[:i + j + 1], "__init__.py")) for j in range(len(chain)))
        if ok and (chain or i == len(parts) - 1):
            tail = parts[i:]
            if tail[-1] == "__init__":
                tail = tail[:-1]
            if tail:
                names.add(".".join(tail))
            break
    return names


def changed_symbols(repo):
    diff = sh(["git", "diff", "HEAD", "-U0"], repo)
    syms = set()
    for l in diff.splitlines():
        m = re.match(r"^@@.*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)", l)
        if m:
            syms.add(m.group(1))
        m = re.match(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+(\w+)", l)
        if m:
            syms.add(m.group(1))
        m = re.match(r"^\+\s*([A-Za-z_]\w{3,})\s*=", l)  # new module/class level constants (cheap)
        if m and not l.startswith("+++"):
            syms.add(m.group(1))
    return {s for s in syms if len(s) >= 4 and not (s.startswith("__") and s.endswith("__"))}


def run_capped(cmd, cwd, secs, env=None):
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True, env=env)
    timed_out = False
    try:
        out, _ = p.communicate(timeout=max(secs, 1))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
        out, _ = p.communicate()
    return p.returncode, out or "", timed_out


def parse_fail_ids(out):
    ids = []
    for l in out.splitlines():
        m = re.match(r"^(\S+::\S.*?) (FAILED|ERROR)\b", l) or re.match(r"^(FAILED|ERROR) (\S+?::\S+?)(?: - .*)?$", l)
        if m:
            tid = m.group(1) if m.group(2) in ("FAILED", "ERROR") else m.group(2)
            if tid not in ids:
                ids.append(tid)
        m2 = re.match(r"^ERROR (\S+\.py)\b", l)  # collection error
        if m2 and m2.group(1) not in ids:
            ids.append(m2.group(1))
    return ids


def tb_tail(out, tid, n=3):
    name = tid.split("::")[-1].split("[")[0]
    secs = re.split(r"\n_{3,} (.+?) _{3,}\n", out)
    # secs: [pre, name1, body1, name2, body2 ...]
    for i in range(1, len(secs) - 1, 2):
        if name in secs[i]:
            lines = [x for x in secs[i + 1].splitlines() if x.strip() and not x.startswith("=")]
            e = [x for x in lines if x.startswith("E ")]
            pick = (e[:2] + lines[-1:]) if e else lines[-n:]
            return [x.strip()[:150] for x in pick[:n]]
    return []

# ------------------------------------------------------------------ static checks
def static_checks(ch):
    out = []
    if not ch:
        return ["EMPTY: no changes vs HEAD; submit_patch would send nothing."]
    lib = [c for c in ch if is_py(c.path) and not is_test(c.path) and not SCRATCH_RE.search(c.path)]
    if not lib:
        out.append("NO-SOURCE: no non-test .py source file changed (only: %s)." % ", ".join(c.path for c in ch[:4]))
    for c in ch:
        if not (is_py(c.path) and c.new is not None):
            continue
        try:
            compile(c.new, c.path, "exec", dont_inherit=True)
        except SyntaxError as e:
            if c.old is not None and parse(c.old) is None:
                continue
            out.append("SYNTAX: %s:%s %s" % (c.path, e.lineno, e.msg))
            continue
        except (ValueError, RecursionError):
            continue
        nt = parse(c.new)
        if nt is None:
            continue
        bad = undefined_names(nt)
        ot = parse(c.old) if c.old else None
        old_bad = undefined_names(ot) if ot else {}
        fresh = [(n, l) for n, l in bad.items() if n not in old_bad]
        if fresh:
            out.append("UNDEFINED: %s: %s" % (c.path, ", ".join("%s@L%d" % x for x in sorted(fresh, key=lambda x: x[1])[:4])))
    return out


# ------------------------------------------------------------------ related tests
def untracked(root):
    return set(sh(["git", "ls-files", "-o", "--exclude-standard"], root).split("\n")) - {""}


def pick_tests(root, ch, limit=4):
    srcs = [c.path for c in ch if is_py(c.path) and not is_test(c.path) and c.new is not None]
    edited_tests = {c.path for c in ch if is_py(c.path) and is_test(c.path) and c.new is not None}
    if not srcs:
        return [], []
    files = sh(["git", "ls-files", "-co", "--exclude-standard", "*.py"], root).split()
    texts = {}
    for f in files:
        if is_test_path(f) and not f.endswith("conftest.py") and not any(x in SKIP_DIRS for x in f.split("/")[:-1]):
            try:
                t = open(os.path.join(root, f), errors="replace").read()
            except OSError:
                continue
            if re.search(r"def test|class Test|unittest", t):
                texts[f] = t
    mods = {}
    for s in srcs:
        for n in module_names(root, s):
            mods[n] = s
    n_t = max(len(texts), 1)
    syms = set()
    for s in changed_symbols(root):
        r = re.compile(r"\b%s\b" % re.escape(s))
        if sum(1 for x in texts.values() if r.search(x)) <= max(3, 0.35 * n_t):
            syms.add(s)
    stems = {os.path.basename(s)[:-3] for s in srcs}
    scored = []
    for t, txt in texts.items():
        sc = 0
        core = re.sub(r"^test_|_test$", "", os.path.basename(t)[:-3])
        if core in stems:
            sc += 5
        for m in mods:
            parent, leaf = ".".join(m.split(".")[:-1]), m.split(".")[-1]
            if re.search(r"(?:from|import)\s+%s\b" % re.escape(m), txt) or (
                    parent and re.search(r"from\s+%s\s+import[^\n]*\b%s\b" % (re.escape(parent), re.escape(leaf)), txt)):
                sc += 4
                break
        if t in edited_tests:
            sc += 6
        hits = sum(1 for s in syms if re.search(r"\b%s\b" % re.escape(s), txt))
        sc += min(2 * hits, 8)
        if sc >= 4:
            scored.append((sc, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in scored[:limit]], sorted(mods)


def pytest_env(base, extra_path=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_path:
        env["PYTHONPATH"] = os.pathsep.join(extra_path + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def has_module(name):
    return subprocess.run([sys.executable, "-c", "import " + name], capture_output=True).returncode == 0


def baseline_failures(root, ids, secs):
    """Run ids on a pristine `git archive HEAD` copy in a temp dir. Returns set of failing ids or None."""
    tmp = tempfile.mkdtemp(prefix="rv_base_")
    try:
        subprocess.run("git -C %s archive HEAD | tar -x -C %s" % (shlex_q(root), shlex_q(tmp)), shell=True,
                       timeout=30, capture_output=True)
        env = pytest_env(None, [tmp, os.path.join(tmp, "src")])
        rc, out, to = run_capped([sys.executable, "-m", "pytest"] + ids + ["-q", "-p", "no:cacheprovider", "--color=no",
                                  "--tb=no", "-rfE"], tmp, secs, env)
        return None if to else set(parse_fail_ids(out))
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def shlex_q(s):
    import shlex
    return shlex.quote(s)


def related_tests(root, ch, deadline):
    lines = []
    tests, mods = pick_tests(root, ch)
    if not tests:
        return ["TESTS: no existing test file found for %s. Verify with your own check." % (", ".join(mods) or "edited files")]
    if not has_module("pytest"):
        return ["TESTS: pytest not available."]
    before = untracked(root)
    t0 = time.time()
    left = lambda: max(deadline - time.time(), 1)
    base = [sys.executable, "-m", "pytest"]
    common = ["-p", "no:cacheprovider", "--color=no"]
    env = pytest_env(None)
    rc, out, to = run_capped(base + tests + ["--collect-only", "-q"] + common, root, min(20, left()), env)
    col_err = parse_fail_ids(out) if rc not in (0, 5) else []
    col_err = [e for e in col_err if e.endswith(".py")] or ([] if rc in (0, 5) else ["(collection failed)"])
    fails, summary, timed_out = [], "", False
    if not col_err:
        rc, out, timed_out = run_capped(base + tests + ["-x", "-q", "--tb=short"] + common, root, left() * 0.7, env)
        fails = parse_fail_ids(out)
        m = re.findall(r"(\d+) (passed|failed|error|errors|skipped)", out[-400:])
        summary = ", ".join("%s %s" % x for x in m)
    else:
        fails = col_err
        e = [l for l in out.splitlines() if l.startswith("E ")][:2]
        summary = "collection error " + " | ".join(x.strip()[:110] for x in e)
    lines.append("TESTS: %s (%s) %.0fs%s" % (summary or "ran", ", ".join(tests)[:160], time.time() - t0,
                                           " TIMEOUT(partial)" if timed_out else ""))
    if fails:
        old = baseline_failures(root, [f for f in fails if f != "(collection failed)"][:10], left() - 1) if left() > 6 else None
        for f in fails[:3]:
            tag = "unknown" if old is None else ("PRE-EXISTING (also fails on HEAD)" if f in old else "NEW (passes on HEAD)")
            lines.append(" - %s [%s]" % (f[:110], tag))
            if old is None or f not in old:
                for x in tb_tail(out, f, 2):
                    lines.append("     " + x[:120])
    elif not timed_out and rc == 0:
        lines.append("Selected tests pass. Hidden tests cover new behaviour, so also check the issue's exact scenario.")
    # leave no trace: remove untracked files created by the test run
    for f in untracked(root) - before:
        try:
            os.remove(os.path.join(root, f))
        except OSError:
            pass
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/workspace")
    ap.add_argument("--max-seconds", type=int, default=60)
    ap.add_argument("--no-tests", action="store_true")
    a, _ = ap.parse_known_args()
    root = os.path.abspath(a.root)
    deadline = time.time() + a.max_seconds
    out = []
    try:
        ch = load_changes(root)
        issues = static_checks(ch)
        stray = sorted(untracked(root))
        if stray:
            issues.append("STRAY: untracked file(s) will be in the patch, rm them: %s" % ", ".join(stray[:5]))
        cfg = [c.path for c in ch if os.path.basename(c.path) in ("pytest.ini", "conftest.py", "setup.cfg", "tox.ini")]
        if cfg:
            issues.append("CONFIG: do not edit test config: %s" % ", ".join(cfg[:3]))
        a_n = sum(len(c.added) for c in ch)
        r_n = sum(len(c.removed) for c in ch)
        out.append("REVIEW: %d file(s) +%d/-%d" % (len(ch), a_n, r_n))
        out.extend("- " + i for i in issues)
        blocking = [i for i in issues if i.split(":")[0] in ("EMPTY", "SYNTAX", "NO-SOURCE", "STRAY", "CONFIG")]
        if not a.no_tests and ch and not any(i.startswith(("EMPTY", "SYNTAX")) for i in issues):
            out.extend(related_tests(root, ch, deadline))
    except Exception as e:
        out.append("review error: %s" % str(e)[:200])
        issues, blocking = [], []
    body = "\n".join(out)
    bad_tests = " [NEW]" in body or "collection error" in body
    if blocking or any(i.startswith("UNDEFINED") for i in issues) or bad_tests:
        verdict = "VERDICT: FIX BEFORE SUBMIT (see above)."
    else:
        verdict = "VERDICT: no blocking problems found (not proof the fix is right)."
    print((body[:1300] + "\n" + verdict))


if __name__ == "__main__":
    main()
