import ast
import unittest
from pathlib import Path


class HaikuConfigTests(unittest.TestCase):
    def test_removes_incompatible_fields_without_changing_tools(self):
        # Load this pure function without importing the heavyweight evaluator.
        source = Path(__file__).parents[1] / 'runs' / 'task_pipeline.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == 'haiku_request_rewriter')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        body = {'temperature': 0.2, 'top_p': 0.95, 'top_k': 10,
                'thinking': {'type': 'enabled', 'budget_tokens': 4096},
                'thinking_token_budget': 4096, 'chat_template_kwargs': {},
                'extra_body': {'chat_template_kwargs': {'enable_thinking': True},
                               'thinking_token_budget': 4096},
                'tools': [{'name': 'read_file'}], 'max_tokens': 8192}
        rewritten = namespace['haiku_request_rewriter'](body)
        self.assertEqual(rewritten, {
            'thinking': {'type': 'adaptive', 'display': 'summarized'},
            'tools': body['tools'], 'max_tokens': 8192})
        self.assertEqual(body['temperature'], 0.2)
