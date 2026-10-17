#!/usr/bin/env python3
"""Log request timing and token usage while forwarding to the SSH tunnel."""

import http.client
import ipaddress
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


UPSTREAM = ("127.0.0.1", 18000)
HOP_HEADERS = {"connection", "content-length", "host", "transfer-encoding"}
CLIENT_IP_HEADERS = {
    "forwarded", "x-forwarded", "x-real-ip", "x-client-ip",
    "true-client-ip", "cf-connecting-ip", "x-envoy-external-address",
}


def forward_request_header(name):
    name = name.lower()
    return (name not in HOP_HEADERS
            and name not in CLIENT_IP_HEADERS
            and not name.startswith("x-forwarded-"))


def log(message):
    print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        request_path = urlsplit(self.path).path
        is_chat = self.command == "POST" and "/chat/completions" in request_path
        if is_chat:
            forwarded = self.headers.get("X-Forwarded-For", "")
            candidate = forwarded.rsplit(",", 1)[-1].strip()
            try:
                caller_ip = str(ipaddress.ip_address(candidate))
            except ValueError:
                caller_ip = "unknown"
            log(f"POST {request_path} received; caller_ip={caller_ip}")

        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {key: value for key, value in self.headers.items()
                       if forward_request_header(key)}
            connection = http.client.HTTPConnection(*UPSTREAM, timeout=600)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            result = response.read()

            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

            if is_chat:
                try:
                    usage = json.loads(result).get("usage") or {}
                    input_tokens = usage.get("prompt_tokens", "unknown")
                    output_tokens = usage.get("completion_tokens", "unknown")
                except (ValueError, AttributeError):
                    input_tokens = output_tokens = "unknown"
                log(f"POST {request_path} returned {response.status}; "
                    f"input_tokens={input_tokens} output_tokens={output_tokens}")
            connection.close()
        except (OSError, ValueError) as error:
            log(f"{self.command} {self.path} upstream error: {type(error).__name__}")
            self.send_error(502, "Upstream unavailable")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18001), Handler)
    log("Request logger listening on 127.0.0.1:18001")
    server.serve_forever()
