"""Capture exact OpenAI-compatible requests and responses sent to vLLM."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class VllmTraceProxy:
    """Local transparent proxy that records chat-completion exchanges."""

    def __init__(self, upstream_base: str) -> None:
        self.upstream_base = upstream_base.rstrip('/')
        self.exchanges: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def api_base(self) -> str:
        assert self._server is not None
        return f'http://127.0.0.1:{self._server.server_port}/v1'

    def start(self) -> None:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get('Content-Length', '0'))
                request_body = self.rfile.read(length)
                suffix = self.path
                if proxy.upstream_base.endswith('/v1') and suffix.startswith('/v1/'):
                    suffix = suffix[3:]
                upstream_url = proxy.upstream_base + suffix
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in {'host', 'content-length', 'connection'}
                }
                request = urllib.request.Request(
                    upstream_url, data=request_body, headers=headers, method='POST'
                )
                try:
                    with urllib.request.urlopen(request, timeout=900) as response:
                        status = response.status
                        response_headers = dict(response.headers.items())
                        response_body = response.read()
                except urllib.error.HTTPError as error:
                    status = error.code
                    response_headers = dict(error.headers.items())
                    response_body = error.read()
                except Exception as error:  # pragma: no cover - network failure path
                    status = 502
                    response_headers = {'Content-Type': 'application/json'}
                    response_body = json.dumps({'error': str(error)}).encode()

                if self.path.rstrip('/').endswith('/chat/completions'):
                    with proxy._lock:
                        proxy.exchanges.append({
                            'request_raw_json': request_body.decode('utf-8', errors='replace'),
                            'request': _decode_json(request_body),
                            'response_status': status,
                            'response_raw_json': response_body.decode('utf-8', errors='replace'),
                            'response': _decode_json(response_body),
                        })

                self.send_response(status)
                for key, value in response_headers.items():
                    if key.lower() not in {'content-length', 'transfer-encoding', 'connection'}:
                        self.send_header(key, value)
                self.send_header('Content-Length', str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def write_trace(self, path: Path, *, run: dict[str, Any], final: dict[str, Any]) -> None:
        turns = []
        for index, exchange in enumerate(self.exchanges, start=1):
            response = exchange['response'] if isinstance(exchange['response'], dict) else {}
            message = ((response.get('choices') or [{}])[0].get('message') or {})
            tool_calls = _normalize_tool_calls(message.get('tool_calls', []))
            next_request = (
                self.exchanges[index]['request']
                if index < len(self.exchanges) and isinstance(self.exchanges[index]['request'], dict)
                else {}
            )
            turns.append({
                'turn': index,
                'input': {'vllm_request': exchange['request'], 'raw_json': exchange['request_raw_json']},
                'output': {
                    'vllm_response_raw': exchange['response'],
                    'raw_json': exchange['response_raw_json'],
                    'assistant_content': message.get('content'),
                    'reasoning': message.get('reasoning') or message.get('reasoning_content'),
                    'tool_calls': tool_calls,
                },
                'tool_results': _tool_results(next_request, tool_calls),
            })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 'vllm-model-trace-v1', 'run': run, 'turns': turns, 'final': final}, indent=2) + '\n')


def _decode_json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _normalize_tool_calls(calls: Any) -> list[dict[str, Any]]:
    """Keep raw function arguments and add parsed values/types for inspection."""
    normalized = []
    for call in calls if isinstance(calls, list) else []:
        function = call.get('function', {}) if isinstance(call, dict) else {}
        raw_args = function.get('arguments')
        parsed_args = _decode_json(raw_args.encode()) if isinstance(raw_args, str) else raw_args
        normalized.append({
            'id': call.get('id'),
            'type': call.get('type'),
            'name': function.get('name'),
            'arguments_raw_json': raw_args,
            'arguments': parsed_args,
            'argument_types': {
                key: type(value).__name__
                for key, value in parsed_args.items()
            } if isinstance(parsed_args, dict) else None,
        })
    return normalized


def _tool_results(request: dict[str, Any], calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract exact tool responses that vLLM received in the next request."""
    call_names = {call.get('id'): call.get('name') for call in calls}
    results = []
    for message in request.get('messages', []):
        if message.get('role') not in {'tool', 'tool_responses'}:
            continue
        raw_content = message.get('content')
        parsed_content = _decode_json(raw_content.encode()) if isinstance(raw_content, str) else raw_content
        call_id = message.get('tool_call_id')
        if call_id not in call_names:
            continue
        results.append({
            'tool_call_id': call_id,
            'name': call_names.get(call_id),
            'output_raw_json': raw_content,
            'output': parsed_content,
        })
    return results
