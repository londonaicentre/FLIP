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
#

"""A miniature XNAT behind a ``curl`` stand-in, for the dcm2niix configure tests (FLIP#1451).

It keeps commands, wrapper enablement and Event Service subscriptions in one JSON state file, changes
them the way container-service 3.8.1 and xnat-web 1.10.0 do (read from their source), and logs every
request:

* ``POST /xapi/commands`` gives the command and each wrapper fresh ids; ``POST /xapi/commands/{id}``
  keeps the command id and the id of every wrapper it matches by name.
* A subscription is valid while its action key names a wrapper that exists and is enabled site-wide
  and for each of its projects.
* ``GET /xapi/events/subscriptions`` is null while the Event Service is off.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ACTION_KEY_PREFIX = "org.nrg.containers.services.CommandActionProvider:"

VALUE_FLAGS = {
    "-X",
    "--request",
    "-d",
    "--data",
    "--data-binary",
    "-o",
    "--output",
    "-w",
    "--write-out",
    "-u",
    "--user",
    "-H",
    "--header",
    "--connect-timeout",
    "--max-time",
}


class Request:
    """One parsed ``curl`` invocation."""

    def __init__(self, argv: list[str]) -> None:
        self.method = ""
        self.url = ""
        self.data: str | None = None
        self.output: str | None = None
        self.write_out: str | None = None
        self.fail = False
        self.head = False
        i = 0
        while i < len(argv):
            arg = argv[i]
            if arg in VALUE_FLAGS:
                value = argv[i + 1]
                i += 2
                if arg in ("-X", "--request"):
                    self.method = value
                elif arg in ("-d", "--data", "--data-binary"):
                    self.data = value
                elif arg in ("-o", "--output"):
                    self.output = value
                elif arg in ("-w", "--write-out"):
                    self.write_out = value
                continue
            if arg.startswith("http"):
                self.url = arg
            elif arg == "--fail" or (arg.startswith("-") and not arg.startswith("--") and "f" in arg):
                self.fail = True
            elif arg == "--head":
                self.head = True
            i += 1
        if not self.method:
            self.method = "HEAD" if self.head else ("POST" if self.data is not None else "GET")
        parts = urlsplit(self.url)
        self.path = parts.path
        self.query = {k: v[0] for k, v in parse_qs(parts.query).items()}

    def json_body(self) -> Any:
        return json.loads(self.data) if self.data else None


def _wrapper(state: dict[str, Any], wrapper_id: int) -> dict[str, Any] | None:
    for command in state["commands"]:
        for wrapper in command.get("xnat", []):
            if wrapper.get("id") == wrapper_id:
                return wrapper
    return None


def _command(state: dict[str, Any], command_id: int) -> dict[str, Any] | None:
    return next((c for c in state["commands"] if c["id"] == command_id), None)


def _wrapper_of(state: dict[str, Any], command_id: int, name: str) -> dict[str, Any] | None:
    command = _command(state, command_id)
    if command is None:
        return None
    return next((w for w in command.get("xnat", []) if w.get("name") == name), None)


def validation_error(state: dict[str, Any], subscription: dict[str, Any]) -> str | None:
    """Why XNAT would call this subscription invalid, or None when it is valid."""
    key = subscription.get("action-key") or ""
    message = f"Could not validate Action: {key}"
    if not key.startswith(ACTION_KEY_PREFIX):
        return None  # another provider; not modelled, never invalid here
    try:
        wrapper_id = int(key.removeprefix(ACTION_KEY_PREFIX))
    except ValueError:
        return message
    if _wrapper(state, wrapper_id) is None or wrapper_id not in state["site_enabled"]:
        return message
    for project in (subscription.get("event-filter") or {}).get("project-ids") or []:
        if wrapper_id not in state["project_enabled"].get(project, []):
            return message
    return None


def with_validity(state: dict[str, Any], subscription: dict[str, Any]) -> dict[str, Any]:
    error = validation_error(state, subscription)
    return {**subscription, "valid": error is None, "validation-message": error}


def _new_id(state: dict[str, Any], sequence: str) -> int:
    state[sequence] += 1
    return int(state[sequence])


def handle(state: dict[str, Any], req: Request) -> tuple[int, str]:
    path, method = req.path, req.method
    ok_empty = (200, "")

    if path.endswith("/app/template/Login.vm"):
        return ok_empty
    if re.fullmatch(r"/xapi/users/[^/]+/roles/?", path):
        roles = ["Administrator"] if state["faults"].get("no_container_manager") else ["ContainerManager"]
        return 200, json.dumps(roles)
    if path == "/xapi/docker/server":
        if method == "POST":
            # As XNAT does: every POST stores the server again, under a new id.
            state.setdefault("backend_seq", 0)
            state["backend"] = {**req.json_body(), "id": _new_id(state, "backend_seq")}
            return 200, json.dumps(state["backend"])
        if state.get("backend") is None:
            return 404, '{"error":"no server"}'
        return 200, json.dumps(state["backend"])
    if path == "/xapi/docker/server/ping":
        return 200, "OK"
    if path == "/xapi/docker/hubs":
        if method == "POST":
            hub = req.json_body()
            hub["id"] = len(state["hubs"]) + 2
            state["hubs"].append(hub)
            return 200, json.dumps(hub)
        return 200, json.dumps(state["hubs"])

    if path == "/xapi/commands":
        if method == "POST":
            body = req.json_body()
            if any(c["name"] == body["name"] and c.get("version") == body.get("version") for c in state["commands"]):
                return 400, '{"error":"command already exists"}'
            body["id"] = _new_id(state, "last_command_id")
            for wrapper in body.get("xnat", []):
                wrapper["id"] = _new_id(state, "last_wrapper_id")
            state["commands"].append(body)
            return 201, str(body["id"])
        name = req.query.get("name")
        return 200, json.dumps([c for c in state["commands"] if name is None or c["name"] == name])

    match = re.fullmatch(r"/xapi/commands/(\d+)", path)
    if match:
        command_id = int(match.group(1))
        command = _command(state, command_id)
        if command is None:
            return 404, '{"error":"no command"}'
        if method == "GET":
            return 200, json.dumps(command)
        if method == "DELETE":
            state["commands"].remove(command)
            return 204, ""
        if method == "POST":
            body = req.json_body()
            old_wrappers = {w["name"]: w["id"] for w in command.get("xnat", [])}
            updated = {**body, "id": command_id}
            if state["faults"].get("drop_image_on_update"):
                updated["image"] = command["image"]
            for wrapper in updated.get("xnat", []):
                wrapper["id"] = old_wrappers.get(wrapper["name"]) or _new_id(state, "last_wrapper_id")
            state["commands"][state["commands"].index(command)] = updated
            return 200, ""

    match = re.fullmatch(r"/xapi/commands/(\d+)/wrappers/([^/]+)/enabled", path)
    if match:
        wrapper = _wrapper_of(state, int(match.group(1)), match.group(2))
        if wrapper is None:
            return 404, '{"error":"no wrapper"}'
        if method == "PUT":
            if wrapper["id"] not in state["site_enabled"]:
                state["site_enabled"].append(wrapper["id"])
            return ok_empty
        return 200, json.dumps(wrapper["id"] in state["site_enabled"])

    if path == "/xapi/events/prefs":
        if method == "PUT":
            state["events_enabled"] = bool(req.json_body()["enabled"])
            return ok_empty
        return 200, json.dumps({"enabled": state["events_enabled"]})

    if path == "/xapi/events/subscriptions":
        if not state["events_enabled"]:
            return 200, "null"
        return 200, json.dumps([with_validity(state, s) for s in state["subscriptions"]])

    return 404, f'{{"error":"fake XNAT does not model {method} {path}"}}'


def main(argv: list[str]) -> int:
    state_file = Path(os.environ["FAKE_XNAT_STATE"])
    req = Request(argv)
    state = json.loads(state_file.read_text())
    status, body = handle(state, req)
    state_file.write_text(json.dumps(state, indent=1))
    with Path(os.environ["FAKE_XNAT_LOG"]).open("a") as log:
        log.write(json.dumps({"method": req.method, "path": req.path, "query": req.query, "body": req.data}) + "\n")

    if req.fail and status >= 400:
        return 22
    if not req.output and not req.head:
        sys.stdout.write(body)
    if req.write_out:
        sys.stdout.write(req.write_out.replace("\\n", "\n").replace("%{http_code}", str(status)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
