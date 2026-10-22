import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oec_filters as f  # noqa: E402


def call(name, **arguments):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'x', 'type': 'function', 'function': {'name': name, 'arguments': arguments}}]}


class RepetitionTests(unittest.TestCase):
    def test_three_identical_consecutive_actions(self):
        messages = [call('run_command', command='pytest')] * 3
        self.assertIn('identical', f.repetition_reason(messages))

    def test_two_identical_actions_are_fine(self):
        self.assertIsNone(f.repetition_reason([call('run_command', command='pytest')] * 2))

    def test_different_arguments_are_not_identical(self):
        messages = [call('read_file', filepath=f'a{i % 2}.py') for i in range(4)]
        self.assertIsNone(f.repetition_reason(messages))

    def test_twenty_consecutive_reads_across_both_read_forms(self):
        messages = []
        for i in range(20):
            messages.append(call('read_file', filepath=f'f{i}.py') if i % 2
                            else call('run_command', command=f'cat f{i}.py'))
        self.assertIn('file-reading', f.repetition_reason(messages))

    def test_an_edit_resets_the_read_run(self):
        messages = [call('read_file', filepath=f'f{i}.py') for i in range(19)]
        messages.append(call('edit_file', filepath='f.py', old_string='a', new_string='b'))
        messages += [call('read_file', filepath=f'g{i}.py') for i in range(19)]
        self.assertIsNone(f.repetition_reason(messages))

    def test_tool_calls_of_ignores_non_assistant_messages(self):
        messages = [{'role': 'tool', 'content': 'x'}, call('read_file', filepath='a')]
        self.assertEqual(len(f.tool_calls_of(messages)), 1)


class HandoffTests(unittest.TestCase):
    GOOD = {'teacher_calls': 3, 'summary_calls_before_handoff': 0}

    def test_valid(self):
        self.assertIsNone(f.handoff_problem(self.GOOD, {'resolved': True}))

    def test_rejections(self):
        cases = [
            ({**self.GOOD}, {'resolved': False}, 'not resolved'),
            ({**self.GOOD}, {'resolved': True, 'error': 'boom'}, 'error'),
            ({**self.GOOD, 'teacher_calls': 0}, {'resolved': True}, 'teacher'),
            ({**self.GOOD, 'summary_calls_before_handoff': 1}, {'resolved': True}, 'compaction'),
        ]
        for handoff, result, word in cases:
            self.assertIn(word, f.handoff_problem(handoff, result))

    def test_student_submit_patch_is_rejected(self):
        student = {**call('submit_patch'), '_turn': 0}
        self.assertEqual(f.handoff_problem(self.GOOD, {'resolved': True}, [student]),
                         'student submitted before the handoff')

    def test_teacher_submit_patch_is_fine(self):
        teacher = {**call('submit_patch'), '_turn': 1}
        self.assertIsNone(f.handoff_problem(self.GOOD, {'resolved': True}, [teacher]))


if __name__ == '__main__':
    unittest.main()
