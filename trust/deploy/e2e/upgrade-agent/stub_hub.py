# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A stand-in for the hub's trust-facing protocol, for a kind run of the upgrade CronJob (FLIP#1644, FLIP#1997).

Answers what trust-api and the upgrade agent call:
  POST /api/trust/heartbeat        -> hub_version + aes_key_fingerprint (so trust-api reports hub_key_match)
  GET  /api/tasks/pending          -> no tasks
  GET  /api/trust/upgrade-policy   -> the policy below, with a lease the way the hub keeps one
  POST /api/trust/upgrade-status   -> STARTING claims the lease (409 if held), SUCCEEDED/FAILED release it
Test controls (not part of the hub protocol):
  GET  /_state                     -> the policy and every status report received
  POST /_policy                    -> merge a JSON object into the policy
Every request is logged to stdout with the auth header's presence (never its value).
"""

import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AES_KEY = base64.b64decode(os.environ["AES_KEY_BASE64"])
FINGERPRINT = hashlib.sha256(AES_KEY).hexdigest()[:12]
API_KEY = os.environ["TRUST_API_KEY"]
KEY_HEADER = os.environ.get("TRUST_API_KEY_HEADER", "Authorization")

POLICY = {
    "enabled": True,
    "target_release": os.environ.get("TARGET_RELEASE", "v0.99.1"),
    "lease": None,
    "failed_release": None,
    "maintenance_window": None,
    "timezone": None,
    "is_busy": False,
}
REPORTS: list[dict] = []


class Hub(BaseHTTPRequestHandler):
    def _send(self, code: int, body: object | None = None) -> None:
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self) -> bool:
        return self.headers.get(KEY_HEADER) == API_KEY

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, fmt, *args):  # noqa: D102
        sys.stdout.write(
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {self.command} {self.path} "
            f"auth={'ok' if self._authed() else 'MISSING/WRONG'} -> {args[1] if len(args) > 1 else ''}\n"
        )
        sys.stdout.flush()

    def do_GET(self):  # noqa: N802
        if self.path == "/_state":
            return self._send(200, {"policy": POLICY, "reports": REPORTS})
        if not self._authed():
            return self._send(401, {"detail": "API key is missing"})
        if self.path.startswith("/api/tasks/pending"):
            return self._send(200, {"tasks": []})
        if self.path == "/api/trust/upgrade-policy":
            return self._send(200, POLICY)
        return self._send(404, {"detail": "not in the stub"})

    def do_POST(self):  # noqa: N802
        if self.path == "/_policy":
            POLICY.update(self._body())
            return self._send(200, POLICY)
        if not self._authed():
            return self._send(401, {"detail": "API key is missing"})
        if self.path == "/api/trust/heartbeat":
            self._body()
            return self._send(200, {"hub_version": POLICY["target_release"], "aes_key_fingerprint": FINGERPRINT})
        if self.path == "/api/trust/upgrade-status":
            body = self._body()
            body["received_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            REPORTS.append(body)
            print(f"STATUS REPORT: {json.dumps(body)[:2000]}", flush=True)
            state = body.get("state")
            if state == "STARTING":
                if POLICY["lease"] and POLICY["lease"] != body.get("release"):
                    return self._send(409)
                POLICY["lease"] = body.get("release")
            elif state in ("SUCCEEDED", "FAILED"):
                POLICY["lease"] = None
                if state == "FAILED":
                    POLICY["failed_release"] = body.get("release")
            return self._send(200, {})
        return self._send(404, {"detail": "not in the stub"})


if __name__ == "__main__":
    print(f"stub hub on :80, target {POLICY['target_release']}, key fingerprint {FINGERPRINT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 80), Hub).serve_forever()
