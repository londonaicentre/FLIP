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
"""The checks of a running trust node, written once for every platform (FLIP#1449).

Each check asks its question from inside a container the node already runs, through the small
:class:`Node` interface: find a service's container, run a command in it, read its state. Compose
answers it with ``docker`` (check_node.py), Kubernetes with ``kubectl``
(trust/deploy/helm/scripts/selftest_k8s.py). :data:`ROWS` is the list of rows both report.
"""

from __future__ import annotations

import json
import re
import shlex
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

import trust_checks
from trust_checks import Check, failed, passed, skipped, verdict

ORTHANC_URL = "http://orthanc:8042"
# XNAT as the xnat-web container reaches itself.
XNAT_URL = "http://127.0.0.1:8080"
# The throwaway project the conversion check imports into, deleted when the check ends. Its name
# marks it as the check's own, so a later run clears it only if the check created it.
SELFTEST_PROJECT = "FLIPSELFTEST"
SELFTEST_NAME = "FLIP self-test"
# How many Orthanc series the conversion check looks at to pick the smallest one to convert.
SERIES_SAMPLE = 50
API_COMPONENTS = ("trust-api", "imaging-api", "data-access-api")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


# Every platform reports these rows, by these names, in this order (the FL rows only with an FL client).
ROWS = (
    "services ready",
    "fl kit delivered",
    *(f"{component} health" for component in API_COMPONENTS),
    "omop seeded",
    "orthanc seeded",
    "orthanc requires auth",
    "xnat c-store",
    "dcm2niix conversion",
    "fl client running",
    "hub heartbeat",
    "declared endpoints reachable",
    "undeclared destinations blocked",
    "no configuration drift",
)
DRIFT_REFS = ("SATRE 5.5.01", "SATRE 2.4.03", "SATRE 2.4.04")
# Two well-known hosts no trust node has a reason to reach, tried on 443 to see what the node's network lets out.
# A sample: a node that blocks both may still allow others. test_trust_endpoints.py keeps them off the declared list.
UNDECLARED = ("example.com", "www.wikipedia.org")
EGRESS_REFS = ("SATRE 5.5.01", "SATRE 2.2.11")
# Run inside a container: says for each target what answered. A URL goes the way the container's own traffic
# does (its proxy settings included); a host:port is dialled directly with a TLS hello, which is what a firewall
# that filters by server name judges. Reads the targets as JSON on its last line and prints {target: answer}:
# "reached" (a 2xx, or a completed handshake), "answered …" (something replied, but not that: an HTTP error, a
# certificate the container does not trust, a refused hello), or the reason nothing did. `answered()` reads it.
PROBE = r"""
import json, socket, ssl, sys, urllib.error, urllib.request

UNTRUSTED = "answered with a certificate this container does not trust"

def url(url):
    try:
        urllib.request.urlopen(url, timeout=8)
    except urllib.error.HTTPError as error:
        return f"answered HTTP {error.code}"
    except Exception as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return UNTRUSTED
        return f"{type(reason).__name__}: {reason}"
    return "reached"

def tls(target):
    host, _, port = target.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=8) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                pass
    except ssl.SSLCertVerificationError:
        return UNTRUSTED
    except (ssl.SSLEOFError, ssl.SSLZeroReturnError) as error:
        return f"closed during the TLS hello: {error}"
    except ssl.SSLError as error:
        # Any other TLS error is a reply from a peer: an FL server that wants a client certificate sends one.
        return f"answered, refusing the TLS hello ({error.reason})"
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return "reached"

targets = json.loads(sys.stdin.readlines()[-1])
print(json.dumps({t: url(t) if t.startswith("http") else tls(t) for t in targets}))
"""


class Node(Protocol):
    """What the checks ask of a node. ``pod`` is the name of a service's running container, or pod."""

    def pod(self, component: str) -> str | None: ...
    def container_port(self, pod: str) -> int | None: ...
    def orthanc_credentials(self) -> str | None: ...
    def container_state(self, pod: str) -> tuple[bool, int]: ...
    def mounts(self, pod: str, container: str) -> list[dict]: ...
    def exec(self, pod: str, script: str, stdin: str | None = None) -> tuple[bool, str]: ...


def nvflare_startup_dir(node: Node, pod: str) -> str | None:
    """The fl-client's mount of the kit's startup/ folder, read from the pod (FLIP#1958).

    It is mounted at /app/startup, or on Kubernetes with job pods under the workspace the parent runs NVFLARE
    in. Reading the pod follows the chart wherever it puts the kit.
    """
    mounts = node.mounts(pod, "fl-client")
    return next((m["mountPath"] for m in mounts if m.get("subPath") == "startup"), None)


def check_fl_kit(node: Node, backend: str) -> Check:
    """The FL client sees its participant kit where the image reads it."""
    pod = node.pod("fl-client")
    if not pod:
        return failed("fl kit delivered", "no running fl-client pod")
    if backend == "nvflare":
        startup = nvflare_startup_dir(node, pod)
        if not startup:
            return failed("fl kit delivered", f"{pod} mounts no startup/ folder from the kit")
        where = f"{startup}/fed_client.json"
        ok, _ = node.exec(pod, f"test -s {shlex.quote(where)}")
    else:
        where = "/certs/ca.crt and /keys"
        ok, _ = node.exec(pod, 'test -s /certs/ca.crt && [ -n "$(ls -A /keys)" ]')
    return verdict("fl kit delivered", ok, f"{where} in {pod}" if ok else f"{pod} has no kit at {where}")


def check_health(node: Node, component: str) -> Check:
    """The API answers its own /health with status ok, asked from inside its pod."""
    name = f"{component} health"
    pod = node.pod(component)
    if not pod:
        return failed(name, f"no running {component} pod")
    port = node.container_port(pod) or 8000
    script = (
        'python3 -c "import urllib.request; '
        f"print(urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=10).read().decode())\""
    )
    ok, body = node.exec(pod, script)
    try:
        healthy = ok and json.loads(body).get("status") == "ok"
    except (json.JSONDecodeError, AttributeError):
        healthy = False
    return verdict(
        name, healthy, f"ok from :{port}/health" if healthy else f"no healthy answer from :{port}/health ({body[:120]})"
    )


def check_hub_heartbeat(
    node: Node, wait_seconds: int = 90, sleep: Callable[[float], None] = time.sleep, poll: int = 10
) -> Check:
    """trust-api's last heartbeat reached the hub, and the hub holds the same key (its /health says both).

    trust-api forgets the hub's answer whenever a heartbeat fails or is refused, so "no answer on record" after a
    wait is a failure: a wrong API key looks exactly like that. Just after bring-up it only means "not yet", hence
    the wait.
    """
    name = "hub heartbeat"
    pod = node.pod("trust-api")
    if not pod:
        return failed(name, "no running trust-api container")
    port = node.container_port(pod) or 8000
    script = (
        'python3 -c "import urllib.request; '
        f"print(urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=10).read().decode())\""
    )
    waited = 0
    while True:
        ok, body = node.exec(pod, script)
        try:
            health = json.loads(body) if ok else None
        except json.JSONDecodeError:
            health = None
        if not isinstance(health, dict):
            raise RuntimeError(f"trust-api did not answer its own /health ({body[:120]})")
        match = health.get("hub_key_match")
        if match is not None or waited >= wait_seconds:
            break
        sleep(poll)
        waited += poll
    if match is True:
        return passed(
            name, f"the hub answered (it runs {health.get('hub_version') or 'an unreported release'}), key matches"
        )
    if match is False:
        return failed(
            name, "the hub answered, but its key differs from this trust's: refresh the kit's hub-shared block"
        )
    return failed(
        name,
        f"trust-api has no accepted heartbeat on record after {waited}s: its last one failed or was refused "
        "(a wrong TRUST_API_KEY looks like this). Its log says which",
    )


def drift(differences: list[str], compared: str) -> Check:
    """The drift row: what differs between the running node and what it was deployed from, or that nothing does.

    Each platform finds the differences its own way (Helm's manifest, Compose's configuration); ``compared`` says
    what was compared, so a pass states its own reach.
    """
    name = "no configuration drift"
    if differences:
        return Check(name, trust_checks.Status.FAIL, "; ".join(differences), refs=DRIFT_REFS)
    return Check(name, trust_checks.Status.PASS, compared, refs=DRIFT_REFS)


def probe(node: Node, pod: str, targets: list[str]) -> dict[str, str]:
    """What answered from inside a container: {target: "reached" or why not}. Raises when the probe cannot run."""
    script = f"python3 -c {shlex.quote(PROBE)}"
    ok, out = node.exec(pod, script, stdin=json.dumps(targets))
    try:
        answers = json.loads(out.strip().splitlines()[-1]) if ok else None
    except (json.JSONDecodeError, IndexError):
        answers = None
    if not isinstance(answers, dict):
        raise RuntimeError(f"the probe did not run in {pod}: {out[:160]}")
    return answers


def fl_server(node: Node, pod: str, backend: str) -> str | None:
    """host:port the FL client dials, read from the client itself: its kit (NVFLARE) or its arguments (Flower)."""
    if backend == "nvflare":
        startup = nvflare_startup_dir(node, pod)
        ok, out = node.exec(pod, f"cat {shlex.quote(f'{startup}/fed_client.json')}") if startup else (False, "")
        try:
            return str(json.loads(out)["servers"][0]["service"]["target"]) if ok else None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return None
    ok, out = node.exec(pod, "tr '\\0' '\\n' < /proc/1/cmdline")
    args = out.split() if ok else []
    return args[args.index("--superlink") + 1] if "--superlink" in args[:-1] else None


def answered(answer: str) -> bool:
    """Whether a probe's answer means the far side replied at all, whatever it said."""
    return answer.startswith(("reached", "answered"))


def check_declared_reachable(node: Node, backend: str, fl_enabled: bool) -> Check:
    """The hub API answers trust-api and the FL server answers the FL client, each asked from its own container.

    The hub must answer its /health with a 2xx over a connection the container trusts: an error page, or a
    certificate a firewall put in the way, is not the hub. The FL server only has to reply: its certificate comes
    from the FL network's own CA, which the client holds and this probe does not, so the client is its judge.
    The image registry is not asked from here: the host pulls images, and the onboarding checklist probes it.
    """
    name = "declared endpoints reachable"
    api = node.pod("trust-api")
    if not api:
        return failed(name, "no running trust-api container")
    ok, hub = node.exec(api, "printenv CENTRAL_HUB_API_URL")
    health = f"{hub.strip()}/health"
    if not ok or not health.startswith("http"):
        return failed(name, "trust-api has no CENTRAL_HUB_API_URL, or it is not a URL")
    found = [f"hub API {health}"]
    problems = []
    answer = probe(node, api, [health])[health]
    if answer != "reached":
        problems.append(f"hub API {health}: {answer}")
    if fl_enabled:
        client = node.pod("fl-client")
        if not client:
            return failed(name, "no running fl-client container to ask the FL server from")
        target = fl_server(node, client, backend)
        if not target:
            return failed(name, f"could not read the FL server's address from {client}")
        answer = probe(node, client, [target])[target]
        found.append(f"FL server {target}")
        if not answered(answer):
            problems.append(f"FL server {target}: {answer}")
    if problems:
        return Check(name, trust_checks.Status.FAIL, "; ".join(problems), refs=EGRESS_REFS)
    return Check(name, trust_checks.Status.PASS, ", ".join(found) + " answered", refs=EGRESS_REFS)


def check_undeclared_blocked(node: Node, fl_enabled: bool) -> Check:
    """The node's network lets nothing out to a sample of destinations no trust needs.

    A destination that answers is a warning, never a failure: what a site's network allows is the site's decision,
    and this row tells its IT and governance teams what it found. Asked from trust-api and from the FL client,
    which runs the researcher's code; a container that should be there and is not fails the row, since nothing
    was learned about it. A pass gives the reason each attempt failed, so "blocked" can be told from "no DNS".
    """
    name = "undeclared destinations blocked"
    pods = [node.pod("trust-api"), node.pod("fl-client") if fl_enabled else "skip"]
    if not all(pods):
        return failed(name, "trust-api or the fl-client is not running, so its network could not be sampled")
    targets = [f"https://{host}/" for host in UNDECLARED]
    reached, reasons = [], []
    for pod in pods:
        if pod == "skip":
            continue
        for host, answer in zip(UNDECLARED, probe(node, pod, targets).values()):
            (reached if answered(answer) else reasons).append(
                f"{host} from {pod}" + ("" if answered(answer) else f": {answer}")
            )
    if reached:
        return Check(
            name,
            trust_checks.Status.WARN,
            "answered on 443: " + ", ".join(reached) + ". The node's network allows destinations outside FLIP's list",
            hints=("FLIP's list: docs → Deploy a trust node → Network requirements",),
            refs=EGRESS_REFS,
        )
    return Check(
        name,
        trust_checks.Status.PASS,
        "none answered (a sample, not a proof): " + "; ".join(reasons),
        refs=EGRESS_REFS,
    )


def check_omop_seeded(node: Node) -> Check:
    """omop-db holds people: the seed hook loaded this trust's slice of the dataset."""
    pod = node.pod("omop-db")
    if not pod:
        return failed("omop seeded", "no running omop-db pod")
    ok, out = node.exec(pod, 'psql -X -tAc "select count(*) from omop.person" -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
    count = out.strip()
    if ok and count.isdigit() and int(count) > 0:
        return passed("omop seeded", f"{count} rows in omop.person")
    return failed("omop seeded", f"omop.person is empty or unreadable ({count[:120]})")


def check_orthanc(node: Node) -> tuple[Check, Check, bool]:
    """Orthanc holds studies, and refuses a request without credentials; and whether it answered that it is empty.

    Both asked from the xnat-web container, which can reach Orthanc on every platform (it is the C-STORE receiver).
    The third value is true only when Orthanc itself said it holds no instance: a count that could not be read is
    a failure of this check, never an empty Orthanc.
    """
    pod = node.pod("xnat-web")
    if not pod:
        missing = "no running xnat-web container to ask Orthanc from"
        return failed("orthanc seeded", missing), failed("orthanc requires auth", missing), False

    creds = node.orthanc_credentials()
    empty = False
    if not creds:
        seeded = failed("orthanc seeded", "could not read an Orthanc user from its configuration")
    else:
        # The credential goes to curl as a config on stdin: in argv the container's process list would show it.
        ok, out = node.exec(pod, f"{CURL_AUTH}\nread -r ocreds\n{ORTHANC_CURL} {ORTHANC_URL}/statistics", stdin=creds)
        try:
            instances = int(json.loads(out)["CountInstances"]) if ok else None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            instances = None
        if instances is None:
            seeded = failed("orthanc seeded", f"Orthanc did not say what it holds ({out[:120]})")
        elif instances == 0:
            seeded, empty = failed("orthanc seeded", "Orthanc holds no instances"), True
        else:
            seeded = passed("orthanc seeded", f"{instances} instances")

    _, code = node.exec(pod, f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {ORTHANC_URL}/")
    code = code.strip()
    auth = (
        passed("orthanc requires auth", "401 without credentials")
        if code == "401"
        else failed("orthanc requires auth", f"expected 401 from {ORTHANC_URL}/, got {code or 'no answer'}")
    )
    return seeded, auth, empty


def check_cstore(run_smoke: Callable[[], tuple[int, str, str]]) -> Check:
    """A real C-STORE from Orthanc into XNAT: the platform's smoke, its verdict and its last word.

    The smoke speaks on stdout; stderr carries the tool's own chatter (kubectl's "Defaulted container ..."),
    so it is read only when the smoke said nothing at all.
    """
    code, stdout, stderr = run_smoke()

    def last_line(text: str) -> str:
        lines = [ANSI.sub("", line).strip() for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    return verdict("xnat c-store", code == 0, last_line(stdout) or last_line(stderr) or f"smoke-cstore exited {code}")


# A curl config line carrying a credential as a Basic auth header. Every call below runs in the
# xnat-web pod and hands curl its credential this way, through a pipe, so no process list in the
# pod shows it. XNAT's comes from the service user the pod already holds in its environment and
# never crosses kubectl; Orthanc's arrives on stdin. Base64 leaves nothing to quote: a credential
# holding a quote or a newline cannot end the config line early, so curl never parses part of it
# as a directive or echoes it back in a warning, which would land in the report.
CURL_AUTH = (
    r"""curl_auth() { printf 'header = "Authorization: Basic %s"\n' "$(printf '%s' "$1" | base64 | tr -d '\n')"; }"""
)
XNAT_CURL = 'curl_auth "$XNAT_SERVICE_USER:$XNAT_SERVICE_PASSWORD" | curl -sS -K - --max-time 100 -w "\\n%{http_code}"'
ORTHANC_CURL = 'curl_auth "$ocreds" | curl -fsS -K - --max-time 100'


class XnatPod:
    """XNAT's REST API and the mocked PACS, both reached from inside the xnat-web pod."""

    def __init__(self, node: Node, pod: str) -> None:
        self.node = node
        self.pod = pod

    def _xnat(self, curl_args: str, before: str = "", stdin: str | None = None) -> tuple[int, str]:
        """Run one XNAT call: (HTTP status, body), status 0 when XNAT gave no answer."""
        ok, out = self.node.exec(self.pod, f"{CURL_AUTH}\n{before}{XNAT_CURL} {curl_args}", stdin=stdin)
        if not ok:
            return 0, out
        body, _, code = out.rpartition("\n")
        return (int(code), body) if code.isdigit() else (0, out)

    def request(self, method: str, path: str, body: str | None = None, content_type: str = "") -> tuple[int, str]:
        data = f"-H {shlex.quote('Content-Type: ' + content_type)} --data-binary {shlex.quote(body)} " if body else ""
        return self._xnat(f"-X {method} {data}{shlex.quote(XNAT_URL + path)}")

    def smallest_series(self, orthanc_creds: str) -> tuple[str, int] | None:
        """The Orthanc series with the fewest instances among the first SERIES_SAMPLE, and its size."""
        query = json.dumps({"Level": "Series", "Query": {}, "Limit": SERIES_SAMPLE, "Expand": True})
        script = (
            f"{CURL_AUTH}\nread -r ocreds\n{ORTHANC_CURL} -X POST --data-binary {shlex.quote(query)} "
            f"{ORTHANC_URL}/tools/find"
        )
        ok, out = self.node.exec(self.pod, script, stdin=orthanc_creds)
        try:
            series = [(s["ID"], len(s.get("Instances", []))) for s in json.loads(out)] if ok else []
        except (json.JSONDecodeError, TypeError, KeyError):
            series = []
        series = [s for s in series if s[1] > 0]
        return min(series, key=lambda s: s[1]) if series else None

    def import_series(self, orthanc_creds: str, series_id: str, project: str) -> tuple[int, str]:
        """Copy one series from Orthanc into the project's archive, through XNAT's import service.

        ``action=commit`` with ``AA=true`` has the DICOM-zip importer build the session and archive it
        before it answers, with the archive URI as the body. Without them the session waits in the
        prearchive until XNAT's idle timer archives it, about five minutes later.
        """
        fetch = (
            "read -r ocreds\n"
            "zip=$(mktemp)\n"
            "trap 'rm -f \"$zip\"' EXIT\n"
            f'{ORTHANC_CURL} -o "$zip" {shlex.quote(f"{ORTHANC_URL}/series/{series_id}/archive")} '
            "|| { echo 'could not fetch the series from Orthanc' >&2; exit 1; }\n"
        )
        url = (
            f"{XNAT_URL}/data/services/import?import-handler=DICOM-zip&PROJECT_ID={project}"
            "&SUBJECT_ID=selftest&EXPT_LABEL=selftest&inbody=true&action=commit&AA=true"
        )
        return self._xnat(
            f"-X POST -H 'Content-Type: application/zip' --data-binary @\"$zip\" {shlex.quote(url)}",
            before=fetch,
            stdin=orthanc_creds,
        )


def _results(body: str) -> list[dict]:
    """The rows of an XNAT ResultSet answer, or none."""
    try:
        rows = json.loads(body)["ResultSet"]["Result"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _row_id(row: object) -> str:
    """A ResultSet row's ID, or empty when the row is not an object or has none."""
    return str(row.get("ID") or "") if isinstance(row, dict) else ""


def _project_xml(project: str) -> str:
    """The projectData document imaging-api posts to create a project (services/projects.py)."""
    root = ET.Element(f"{{{XNAT_URL}/data/projects}}projectData")
    ET.SubElement(root, "ID").text = project
    ET.SubElement(root, "secondary_ID").text = project
    ET.SubElement(root, "name").text = SELFTEST_NAME
    ET.SubElement(root, "description").text = "Created and deleted by the FLIP node check"
    return ET.tostring(root, encoding="unicode", short_empty_elements=False)


def check_conversion(
    xnat: XnatPod,
    orthanc_creds: str | None,
    timeout: int,
    poll: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Check:
    """dcm2niix turns a stored series into NIfTI on this node (FLIP#1448).

    A C-STORE proves XNAT received a study, not that the Container Service can convert it: on
    Kubernetes the conversion is a Job of its own, which has to be admitted, scheduled, mount the
    XNAT volume and hand its output back. So import the smallest series Orthanc holds into a
    throwaway project, launch dcm2niix on its scan the way a cohort pull's event subscription
    does, and require a Complete container and a NIfTI file on the scan. The project is deleted
    afterwards, whatever the outcome.
    """
    name = "dcm2niix conversion"
    if not orthanc_creds:
        return failed(name, "could not read an Orthanc user from its configuration")
    status, body = xnat.request("GET", "/xapi/commands?name=dcm2niix")
    try:
        command = json.loads(body)[0]
        command_id, wrapper = command["id"], command["xnat"][0]["name"]
    except (json.JSONDecodeError, LookupError, TypeError):
        return failed(name, f"no dcm2niix command is registered (HTTP {status}: {body[:120]})")
    series = xnat.smallest_series(orthanc_creds)
    if series is None:
        return failed(name, "Orthanc lists no series to convert")

    # A project left behind by an interrupted run would hold its old scan, so clear it first. Only
    # one this check created, though: the service user is an XNAT administrator, and a project
    # that merely shares the id is someone else's to remove.
    status, body = xnat.request("GET", "/data/projects?format=json")
    if status != 200:
        return failed(name, f"could not list XNAT's projects (HTTP {status}: {body[:120]})")
    existing = next((p for p in _results(body) if p.get("ID") == SELFTEST_PROJECT), None)
    if existing is not None:
        if existing.get("name") != SELFTEST_NAME:
            return failed(
                name,
                f"XNAT already holds a {SELFTEST_PROJECT} project this self-test did not create "
                f"(named {existing.get('name')!r}); remove or rename it, then rerun",
            )
        status, body = xnat.request("DELETE", f"/data/projects/{SELFTEST_PROJECT}?removeFiles=true")
        if status != 200:
            return failed(name, f"could not clear the {SELFTEST_PROJECT} an earlier run left (HTTP {status})")
    status, body = xnat.request("POST", "/data/projects", _project_xml(SELFTEST_PROJECT), "application/xml")
    if status != 200:
        return failed(name, f"could not create the {SELFTEST_PROJECT} project (HTTP {status}: {body[:120]})")
    try:
        result = _convert(xnat, orthanc_creds, series, command_id, wrapper, timeout, poll, sleep, clock)
    finally:
        cleanup, body = xnat.request("DELETE", f"/data/projects/{SELFTEST_PROJECT}?removeFiles=true")
    if cleanup != 200:
        detail = f"{result.detail}; could not delete the {SELFTEST_PROJECT} project (HTTP {cleanup}), delete it in XNAT"
        return replace(result, detail=detail)
    return result


def _convert(
    xnat: XnatPod,
    orthanc_creds: str,
    series: tuple[str, int],
    command_id: int,
    wrapper: str,
    timeout: int,
    poll: int,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Check:
    name = "dcm2niix conversion"
    project = SELFTEST_PROJECT
    series_id, instances = series
    # The import archives before it answers, so an archive XNAT refuses (a validation error, say)
    # comes back here as the import's own failure.
    status, body = xnat.import_series(orthanc_creds, series_id, project)
    if status != 200:
        return failed(name, f"XNAT did not import Orthanc series {series_id} (HTTP {status}: {body[:200]})")
    experiments = _results(xnat.request("GET", f"/data/projects/{project}/experiments?format=json")[1])
    if not experiments:
        return failed(
            name,
            f"XNAT answered the import of Orthanc series {series_id} ({body.strip()[:120]!r}) "
            f"but archived no session into {project}",
        )
    experiment = _row_id(experiments[0])
    if not experiment:
        return failed(name, f"XNAT listed a session in {project} with no ID: {experiments[0]!r:.120}")
    scans = _results(xnat.request("GET", f"/data/experiments/{experiment}/scans?format=json")[1])
    if not scans:
        return failed(name, f"session {experiment} has no scan")
    scan = _row_id(scans[0])
    if not scan:
        return failed(name, f"XNAT listed a scan of session {experiment} with no ID: {scans[0]!r:.120}")

    enable = f"/xapi/projects/{project}/commands/{command_id}/wrappers/{wrapper}/enabled"
    status, body = xnat.request("PUT", enable)
    if status != 200:
        return failed(name, f"could not enable dcm2niix on {project} (HTTP {status}: {body[:120]})")
    launch = f"/xapi/projects/{project}/commands/{command_id}/wrappers/{wrapper}/root/scan/launch"
    target = json.dumps({"scan": f"/archive/experiments/{experiment}/scans/{scan}"})
    status, body = xnat.request("POST", launch, target, "application/json")
    try:
        workflow = json.loads(body).get("workflow-id") if status == 200 else None
    except (json.JSONDecodeError, AttributeError):
        workflow = None
    if not workflow:
        return failed(name, f"the Container Service refused the launch (HTTP {status}: {body[:160]})")

    started = clock()
    last = "no container yet"
    while True:
        _, body = xnat.request("GET", f"/xapi/projects/{project}/containers")
        try:
            containers = [c for c in json.loads(body) if isinstance(c, dict)]
        except (json.JSONDecodeError, TypeError):
            containers = []
        # Only this launch's own container counts. XNAT keeps container rows after a project is deleted, so
        # the re-created FLIPSELFTEST lists earlier runs' rows, and ours appears only once its Job exists.
        mine = [c for c in containers if c.get("workflow-id") == workflow]
        if mine:
            last = mine[0].get("status") or "no status"
        if last.startswith(("Complete", "Failed", "Killed")):
            break
        if clock() - started >= timeout:
            return failed(
                name,
                f"dcm2niix had not finished after {timeout}s (last status: {last}). If other conversions "
                "are queued, wait for them (Kubernetes: `kubectl describe resourcequota` shows a full cap) and rerun",
            )
        sleep(poll)
    elapsed = int(clock() - started)
    if not last.startswith("Complete"):
        return failed(name, f"dcm2niix ended {last} after {elapsed}s on scan {scan} of session {experiment}")

    files = _results(
        xnat.request("GET", f"/data/experiments/{experiment}/scans/{scan}/resources/NIFTI/files?format=json")[1]
    )
    nifti = [f.get("Name", "") for f in files if str(f.get("Name", "")).endswith((".nii", ".nii.gz"))]
    if not nifti:
        return failed(name, f"dcm2niix completed but scan {scan} has no NIfTI file")
    return passed(name, f"a {instances}-instance series converted to {nifti[0]} in {elapsed}s")


def check_fl_client(node: Node, settle_seconds: int, sleep: Callable[[float], None] = time.sleep) -> Check:
    """The FL client stays up. One look proves nothing (a crash-looping pod is briefly Running),
    so sample twice a settle period apart: running both times, no restart in between."""
    pod = node.pod("fl-client")
    if not pod:
        return failed("fl client running", "no running fl-client pod")
    first = node.container_state(pod)
    sleep(settle_seconds)
    second = node.container_state(pod)
    if first[0] and second[0] and second[1] == first[1]:
        return passed("fl client running", f"{pod} steady over {settle_seconds}s (no FL server is needed)")
    return failed("fl client running", f"{pod} not steady: running/restarts {first} -> {second}")


def run_checks(
    node: Node,
    *,
    backend: str,
    fl_enabled: bool,
    ready: Callable[[], Check],
    smoke: Callable[[], tuple[int, str, str]],
    no_omop: str = "",
    no_orthanc: str = "",
    no_study: str = "",
    no_hub: str = "",
    hub_wait_seconds: int = 90,
    find_drift: Callable[[], Check] | None = None,
    conversion_timeout: int = 600,
    fl_settle_seconds: int = 45,
) -> list[Check]:
    """Every row of :data:`ROWS`, in order, printing each as it lands.

    ``ready`` is the platform's own answer to "is every service up". ``no_omop`` and ``no_orthanc`` say why the node
    holds no sample data, and skip the rows that need it. ``no_study`` says why no study may be sent at all (a site
    holding real data), and skips the C-STORE and the conversion whatever Orthanc holds. ``no_hub`` says why the node
    is not expected to reach a hub (a hubless self-test). ``find_drift`` is the platform's comparison of the running
    node with what it was deployed from; without one the row is skipped.
    """
    checks: list[Check] = []

    def record(check: Check) -> None:
        checks.append(check)
        is_pass = check.status is trust_checks.Status.PASS
        print(f"  ✅ {check.name}" if is_pass else f"  {check.status.glyph} {check.name}: {check.detail}")

    def attempt(name: str, check: Callable[[], Check]) -> None:
        record(trust_checks.run(name, check))

    def skip(name: str, reason: str) -> None:
        checks.append(skipped(name, reason))
        print(f"  ⏭️  {name}: skipped, {reason}")

    attempt("services ready", ready)
    if fl_enabled:
        attempt("fl kit delivered", lambda: check_fl_kit(node, backend))
    for component in API_COMPONENTS:
        attempt(f"{component} health", lambda component=component: check_health(node, component))
    if no_omop:
        skip("omop seeded", no_omop)
    else:
        attempt("omop seeded", lambda: check_omop_seeded(node))
    # Orthanc's own count, not the seed setting, decides whether there is a study to send: a node whose Orthanc
    # holds studies all the same (loaded another way) still proves its DICOM path end to end.
    try:
        orthanc_seeded, orthanc_auth, orthanc_empty = check_orthanc(node)
    except Exception as error:  # noqa: BLE001 — as trust_checks.run: neither row could be checked
        orthanc_seeded = trust_checks.could_not_run("orthanc seeded", error)
        orthanc_auth = trust_checks.could_not_run("orthanc requires auth", error)
        orthanc_empty = False
    # Skipped only when Orthanc itself said it is empty: a count that could not be read stays a failure.
    nothing_to_send = bool(no_orthanc) and orthanc_empty
    if nothing_to_send:
        skip("orthanc seeded", no_orthanc)
    else:
        record(orthanc_seeded)
    record(orthanc_auth)
    if no_study or nothing_to_send:
        why = no_study or f"{no_orthanc}, and it reports no instance to send"
        skip("xnat c-store", why)
        skip("dcm2niix conversion", why)
    else:
        attempt("xnat c-store", lambda: check_cstore(smoke))

        def conversion() -> Check:
            xnat_pod = node.pod("xnat-web")
            if not xnat_pod:
                return failed("dcm2niix conversion", "no running xnat-web container")
            return check_conversion(XnatPod(node, xnat_pod), node.orthanc_credentials(), conversion_timeout)

        attempt("dcm2niix conversion", conversion)
    if fl_enabled:
        attempt("fl client running", lambda: check_fl_client(node, fl_settle_seconds))
    if no_hub:
        skip("hub heartbeat", no_hub)
        skip("declared endpoints reachable", no_hub)
    else:
        attempt("hub heartbeat", lambda: check_hub_heartbeat(node, hub_wait_seconds))
        attempt("declared endpoints reachable", lambda: check_declared_reachable(node, backend, fl_enabled))
    attempt("undeclared destinations blocked", lambda: check_undeclared_blocked(node, fl_enabled))
    if find_drift:
        attempt("no configuration drift", find_drift)
    else:
        skip("no configuration drift", "no declared configuration was given to compare the node with")
    return checks
