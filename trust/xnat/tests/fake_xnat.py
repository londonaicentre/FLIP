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

``test_dcm2niix_registration.py`` puts a ``curl`` on ``PATH`` that runs this module. It keeps the
Container Service commands, the wrapper enablement and the Event Service subscriptions in one JSON
state file, mutates it the way XNAT does, and logs every request, so the tests assert what XNAT
would end up holding rather than which strings the scripts contain.

What it models, from the container-service 3.8.1 and xnat-web 1.10.0 sources:

* ``POST /xapi/commands`` gives the command and each wrapper a fresh id from separate sequences.
* ``POST /xapi/commands/{id}`` keeps the command id and matches wrappers by name, keeping a matched
  wrapper's id (CommandEntity.java:201-222, CommandWrapperEntity.java:41-44); the image and every
  other field come from the body (CommandEntity.java:99-130).
* A subscription's action key names a wrapper id. It is valid while that wrapper exists, is enabled
  site-wide and is enabled for every project the subscription filters on
  (CommandActionProvider.java:476-489, ContainerConfigServiceImpl.java:135-143), recomputed on every
  read (EventSubscriptionEntityServiceImpl.java:430-438).
* ``PUT /xapi/events/subscription/{id}`` validates before it saves (EventServiceImpl.java:122-142)
  and defaults an absent act-as-event-user to false and absent attributes to {}
  (SubscriptionUpdate.java).
* ``GET /xapi/events/subscriptions`` is null while the Event Service is off
  (EventServiceImpl.java:153-157).

That the real XNAT behaves this way is read from source, not proven here; the PR lists what still
needs a live check.
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
                    self.data = Path(value[1:]).read_text() if value.startswith("@") else value
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


def _with_validity(state: dict[str, Any], subscription: dict[str, Any]) -> dict[str, Any]:
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
    if path == "/xapi/siteConfig":
        return 200, json.dumps({"initialized": True})
    if re.fullmatch(r"/xapi/users/[^/]+/roles/?", path):
        roles = ["Administrator"] if state["faults"].get("no_container_manager") else ["ContainerManager"]
        return 200, json.dumps(roles)
    if path == "/xapi/docker/server":
        if method == "POST":
            state["backend"] = req.json_body()
        return 200, "{}"
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
        found = state["commands"]
        for field in ("name", "image"):
            if field in req.query:
                found = [c for c in found if c.get(field) == req.query[field]]
        return 200, json.dumps(found)

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

    match = re.fullmatch(r"/xapi/projects/([^/]+)/commands/(\d+)/wrappers/([^/]+)/enabled", path)
    if match and method == "PUT":
        project = match.group(1)
        if project not in state["projects"]:
            return 404, '{"error":"no project"}'
        wrapper = _wrapper_of(state, int(match.group(2)), match.group(3))
        if wrapper is None:
            return 404, '{"error":"no wrapper"}'
        enabled = state["project_enabled"].setdefault(project, [])
        if wrapper["id"] not in enabled:
            enabled.append(wrapper["id"])
        return ok_empty

    if path == "/xapi/events/prefs":
        if method == "PUT":
            state["events_enabled"] = bool(req.json_body()["enabled"])
            return ok_empty
        return 200, json.dumps({"enabled": state["events_enabled"]})

    if path == "/xapi/events/allactions":
        # One action per wrapper of every command, keyed "<provider class>:<wrapper id>" and named
        # after the wrapper (CommandActionProvider.java:389-403, MultiActionProvider.java:31-32).
        actions = [
            {"action-key": f"{ACTION_KEY_PREFIX}{w['id']}", "display-name": w["name"], "description": None}
            for c in state["commands"]
            for w in c.get("xnat", [])
        ]
        return 200, json.dumps(actions)

    if path == "/xapi/events/subscriptions":
        if not state["events_enabled"]:
            return 200, "null"
        return 200, json.dumps([_with_validity(state, s) for s in state["subscriptions"]])

    match = re.fullmatch(r"/xapi/events/subscription/(\d+)", path)
    if match:
        subscription_id = int(match.group(1))
        stored = next((s for s in state["subscriptions"] if s["id"] == subscription_id), None)
        if stored is None:
            return 404, '{"error":"no subscription"}'
        if method == "DELETE":
            state["subscriptions"].remove(stored)
            return 204, ""
        if method == "PUT":
            if not state["events_enabled"]:
                return 500, '{"error":"event service disabled"}'
            update = req.json_body()
            merged = dict(stored)
            merged["action-key"] = update["action-key"]
            if update.get("active") is not None:
                merged["active"] = update["active"]
            merged["attributes"] = update.get("attributes") or {}
            merged["act-as-event-user"] = bool(update.get("act-as-event-user") or False)
            error = validation_error(state, merged)
            if error is not None:
                return 400, json.dumps({"error": error})
            state["subscriptions"][state["subscriptions"].index(stored)] = merged
            return ok_empty

    if path == "/data/projects":
        rows = [{"ID": pid, "secondary_ID": secondary} for pid, secondary in state["projects"].items()]
        return 200, json.dumps({"ResultSet": {"Result": rows}})
    match = re.fullmatch(r"/data/projects/([^/]+)", path)
    if match:
        return (200, "{}") if match.group(1) in state["projects"] else (404, "not found")

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
    if req.output and req.output != "/dev/null":
        Path(req.output).write_text(body)
    elif not req.output and not req.head:
        sys.stdout.write(body)
    if req.write_out:
        sys.stdout.write(req.write_out.replace("\\n", "\n").replace("%{http_code}", str(status)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
