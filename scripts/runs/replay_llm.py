"""Replay the student's recorded responses inside a teacher run, and check live tool results."""
from __future__ import annotations

from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

TIMING_KEYS = {'duration', 'elapsed', 'time', 'timestamp'}


class ReplayLlm(BaseLlm):
    """Serves recorded student responses in order. Any call past the recording is a bug."""

    responses: list[dict]
    _index: int = 0

    def __init__(self, **data):
        super().__init__(**data)
        if any(response.get('partial') for response in self.responses):
            raise ValueError('recorded responses must be complete; found a partial (streamed) response')

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self._index >= len(self.responses):
            raise RuntimeError('replay exhausted')
        response = LlmResponse.model_validate(self.responses[self._index])
        self._index += 1
        yield response


def tool_results(events: list[dict]) -> list[dict]:
    results = []
    for event in events:
        for part in (event.get('content') or {}).get('parts') or []:
            call = part.get('function_response')
            if call:
                results.append({'name': call['name'], 'response': _strip_timing(call['response'])})
    return results


def _strip_timing(value):
    if isinstance(value, dict):
        return {k: _strip_timing(v) for k, v in value.items() if k not in TIMING_KEYS}
    if isinstance(value, list):
        return [_strip_timing(v) for v in value]
    return value


def verify_replay(recorded_events: list[dict], live_events: list[dict]) -> None:
    expected, actual = tool_results(recorded_events), tool_results(live_events)
    for index, (want, got) in enumerate(zip(expected, actual)):
        if want != got:
            raise ValueError(f'tool result {index} differs from the snapshot: {want!r} != {got!r}')
    if len(expected) != len(actual):
        raise ValueError(f'tool result count differs: snapshot {len(expected)}, live {len(actual)}')
