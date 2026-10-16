#!/usr/bin/env python3
"""Capture OpenAI API POST payloads and responses before forwarding to vLLM."""
import http.client
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "8001"))
CAPTURE_PATH = os.path.join(os.path.dirname(__file__), "request_capture.jsonl")
HOP_HEADERS = {"connection", "content-length", "host", "transfer-encoding"}
SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key"}

def write_capture(record):
    with open(CAPTURE_PATH, "a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(CAPTURE_PATH, 0o600)

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.forward(capture=False)

    def do_POST(self):
        self.forward(capture=True)

    def forward(self, capture):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(size)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=600)
            connection.request(self.command, self.path, request_body, headers)
            upstream = connection.getresponse()
            response_body = upstream.read()
            self.send_response(upstream.status)
            for key, value in upstream.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            if capture:
                safe_headers = {k: v for k, v in self.headers.items() if k.lower() not in SENSITIVE_HEADERS}
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": self.command, "path": self.path,
                    "request_headers": safe_headers,
                    "request": decode_json(request_body),
                    "response_status": upstream.status,
                    "response": decode_json(response_body),
                }
                usage = record["response"].get("usage") if isinstance(record["response"], dict) else None
                if usage:
                    record["usage"] = usage
                write_capture(record)
            connection.close()
        except (OSError, ValueError, http.client.HTTPException) as error:
            self.send_error(502, "vLLM upstream unavailable")
            if capture:
                write_capture({"timestamp": datetime.now(timezone.utc).isoformat(), "method": self.command, "path": self.path, "error": f"{type(error).__name__}: {error}"})

def decode_json(value):
    try:
        return json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw_body": value.decode("utf-8", errors="replace")}

if __name__ == "__main__":
    os.umask(0o077)
    ThreadingHTTPServer(("0.0.0.0", 8000), Proxy).serve_forever()
