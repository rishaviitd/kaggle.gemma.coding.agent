import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'runs'))
import replay_llm as r  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402


def recorded(text: str) -> dict:
    return {'content': {'role': 'model', 'parts': [{'text': text}]}}


def fn_event(name: str, response: dict, timestamp: float = 0.0) -> dict:
    return {'author': 'agent', 'timestamp': timestamp,
            'content': {'role': 'user', 'parts': [{'function_response': {
                'name': name, 'response': response}}]}}


class ReplayTests(unittest.TestCase):
    def drain(self, llm):
        async def go():
            return [x async for x in llm.generate_content_async(LlmRequest(), stream=False)]
        return asyncio.run(go())

    def test_returns_recorded_responses_in_order(self):
        llm = r.ReplayLlm(model='gemma4', responses=[recorded('a'), recorded('b')])
        first = self.drain(llm)[0]
        self.assertEqual(first.content.parts[0].text, 'a')
        self.assertEqual(self.drain(llm)[0].content.parts[0].text, 'b')

    def test_fails_when_overrun(self):
        llm = r.ReplayLlm(model='gemma4', responses=[recorded('a')])
        self.drain(llm)
        with self.assertRaisesRegex(RuntimeError, 'replay exhausted'):
            self.drain(llm)

    def test_rejects_partial_responses_at_construction(self):
        partial = {'partial': True, 'content': {'role': 'model', 'parts': [{'text': 'a'}]}}
        with self.assertRaisesRegex(ValueError, 'partial'):
            r.ReplayLlm(model='gemma4', responses=[partial])

    def test_function_call_and_thought_parts_round_trip_through_replay(self):
        response = LlmResponse(content=types.Content(role='model', parts=[
            types.Part(text='plan', thought=True),
            types.Part(function_call=types.FunctionCall(name='bash', args={'cmd': 'ls'})),
        ]))
        llm = r.ReplayLlm(model='gemma4', responses=[
            response.model_dump(mode='json', exclude_none=True)])
        replayed = self.drain(llm)[0]
        self.assertTrue(replayed.content.parts[0].thought)
        self.assertEqual(replayed.content.parts[0].text, 'plan')
        self.assertEqual(replayed.content.parts[1].function_call.name, 'bash')
        self.assertEqual(replayed.content.parts[1].function_call.args, {'cmd': 'ls'})

    def test_verify_ignores_timing(self):
        recorded_events = [fn_event('bash', {'stdout': 'x', 'duration': 1.0}, 1.0)]
        live_events = [fn_event('bash', {'stdout': 'x', 'duration': 9.0}, 9.0)]
        r.verify_replay(recorded_events, live_events)

    def test_verify_flags_planted_difference(self):
        recorded_events = [fn_event('bash', {'stdout': 'x'})]
        live_events = [fn_event('bash', {'stdout': 'y'})]
        with self.assertRaisesRegex(ValueError, 'tool result 0'):
            r.verify_replay(recorded_events, live_events)

    def test_tool_results_reads_real_adk_event_dump(self):
        event = Event(author='agent', content=types.Content(role='user', parts=[
            types.Part(function_response=types.FunctionResponse(name='bash', response={'stdout': 'x'}))]))
        dumped = event.model_dump(mode='json', exclude_none=True)
        self.assertEqual(r.tool_results([dumped]), [{'name': 'bash', 'response': {'stdout': 'x'}}])


if __name__ == '__main__':
    unittest.main()
