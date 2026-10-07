"""Tests for outline.py. Run: python3 -m unittest discover -s src/akshay/itr-4/tests -p 'test_*.py'"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), 'skills', 'file-outline', 'scripts', 'outline.py')

SAMPLE = textwrap.dedent('''\
    import os


    CONST = 1


    @decorator
    def top(a, b=2):
        return a


    class Foo(Base):
        """Doc."""

        x = 1

        def __init__(self):
            pass

        @property
        def bar(self) -> int:
            return 1

        class Inner:
            def deep(self):
                pass


    async def runner(*args, **kw):
        def nested():
            pass
        return nested
    ''')


def run(args, cwd=None):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True,
                          text=True, cwd=cwd)


class OutlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'sample.py')
        with open(self.path, 'w') as f:
            f.write(SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_definitions_with_line_ranges(self):
        r = run([self.path])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn('32 lines', out.splitlines()[0])
        self.assertIn('L7-9 def top(a, b=2)', out)          # decorator line included
        self.assertIn('L12-26 class Foo(Base)', out)
        self.assertIn('L17-18 def Foo.__init__(self)', out)
        self.assertIn('L20-22 def Foo.bar(self) -> int', out)
        self.assertIn('L24-26 class Foo.Inner', out)
        self.assertIn('L25-26 def Foo.Inner.deep(self)', out)
        self.assertIn('L29-32 async def runner(*args, **kw)', out)

    def test_skips_functions_nested_in_functions(self):
        out = run([self.path]).stdout
        self.assertNotIn('nested', out)

    def test_symbol_prints_only_that_range(self):
        r = run([self.path, '--symbol', 'Foo.bar'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('L20-22', r.stdout)
        self.assertNotIn('__init__', r.stdout)

    def test_symbol_matches_bare_name_suffix(self):
        r = run([self.path, '--symbol', 'deep'])
        self.assertIn('L25-26 def Foo.Inner.deep(self)', r.stdout)

    def test_symbol_not_found_suggests_outline(self):
        r = run([self.path, '--symbol', 'missing'])
        self.assertEqual(r.returncode, 0)
        self.assertIn('not found', r.stdout)

    def test_relative_path_resolves_against_workspace_root(self):
        r = run(['sample.py', '--root', self.tmp.name], cwd=HERE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('class Foo(Base)', r.stdout)

    def test_missing_file_is_one_line_message(self):
        r = run([os.path.join(self.tmp.name, 'nope.py')])
        self.assertEqual(r.returncode, 0)
        self.assertIn('not found', r.stdout)

    def test_non_python_file_advises_read_file_range(self):
        p = os.path.join(self.tmp.name, 'notes.md')
        with open(p, 'w') as f:
            f.write('x\n')
        r = run([p])
        self.assertIn('not a python file', r.stdout)
        self.assertIn('read_file', r.stdout)

    def test_syntax_error_falls_back_to_regex_outline(self):
        p = os.path.join(self.tmp.name, 'broken.py')
        with open(p, 'w') as f:
            f.write('def ok():\n    pass\n\nclass Bad(:\n    def m(self):\n        pass\n')
        r = run([p])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('L1 def ok()', r.stdout)
        self.assertIn('L5 def m(self)', r.stdout)

    def test_output_is_capped(self):
        p = os.path.join(self.tmp.name, 'big.py')
        with open(p, 'w') as f:
            f.write(''.join(f'def f{i}():\n    pass\n' for i in range(400)))
        lines = run([p]).stdout.splitlines()
        self.assertLessEqual(len(lines), 302)
        self.assertIn('more definitions', lines[-1])

    def test_many_definitions_drop_signatures_but_keep_whole_file(self):
        p = os.path.join(self.tmp.name, 'mid.py')
        with open(p, 'w') as f:
            f.write(''.join(f'def f{i}(alpha, beta, gamma):\n    pass\n' for i in range(150)))
        out = run([p]).stdout
        self.assertIn('L299-300 def f149', out)       # last definition still shown
        self.assertNotIn('alpha', out)                # signatures dropped
        self.assertNotIn('more definitions', out)

    def test_long_signatures_are_shortened(self):
        p = os.path.join(self.tmp.name, 'long.py')
        args = ', '.join(f'argument_number_{i}: int = {i}' for i in range(20))
        with open(p, 'w') as f:
            f.write(f'def wide({args}):\n    pass\n')
        line = [l for l in run([p]).stdout.splitlines() if 'wide' in l][0]
        self.assertLessEqual(len(line), 100)
        self.assertTrue(line.endswith('...'))


if __name__ == '__main__':
    unittest.main()
