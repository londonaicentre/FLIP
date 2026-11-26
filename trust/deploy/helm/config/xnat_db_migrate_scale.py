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
"""Step 2 of the chart's pre-upgrade xnat-db dump hook (FLIP#2071): stop xnat-web, then put it back
if the dump fails.

The dump has to be taken with nothing writing to the database, and the sibling container that takes
it runs in the xnat-db image, which has no kubectl and no curl. So the Kubernetes half lives here,
in FLIP's mirrored uv image, and talks to the API server directly over HTTPS with the pod's
projected ServiceAccount token. Standard library only: the image carries no Kubernetes client, and
the hook must not fetch one at 03:00 on a trust with no internet egress.

The contract with the dump container, through the shared ``--plan-dir`` emptyDir:

* ``plan`` — written by the init container before either of us starts: ``dump`` or ``skip``.
* ``go`` — written here once the xnat-web Deployment is at 0 replicas and no xnat-web pod is left.
* ``abort`` — written here when it could not be stopped, so the dump container fails fast instead
  of waiting out its timeout.
* ``dump-result`` — written by the dump container: ``ok`` or ``failed``.

The guarantee this ordering buys: xnat-web is only down while the dump runs, and **every** path out
of here that stopped it either leaves a dump the new server will restore (``ok``) or scales it back
to the replica count it had. A failure then fails the Job, the pre-upgrade hook, and with it the
``helm upgrade`` — the site stays on the old release, serving.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
#: Poll interval for the two waits. Both are bounded by ``--timeout-seconds``.
POLL_SECONDS = 2.0


class ApiError(RuntimeError):
    """The API server refused a call, or could not be reached."""


class KubeApi:
    """The few calls this hook makes, against the in-cluster API server."""

    def __init__(self, base_url: str, token: str, ca_file: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.context: ssl.SSLContext | None = None
        if self.base_url.startswith("https://"):
            self.context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()

    def request(self, method: str, path: str, body: dict | None = None, content_type: str | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:  # pragma: no cover - exercised through read_replicas
            detail = error.read()[:400].decode(errors="replace")
            raise ApiError(f"{method} {path} -> HTTP {error.code}: {detail}") from error
        except OSError as error:
            raise ApiError(f"{method} {path} -> {error}") from error

    def read_replicas(self, namespace: str, deployment: str) -> int:
        """The Deployment's current ``spec.replicas`` (an absent field means 1, as Kubernetes does)."""
        doc = self.request("GET", f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}")
        replicas = doc.get("spec", {}).get("replicas")
        return 1 if replicas is None else int(replicas)

    def scale(self, namespace: str, deployment: str, replicas: int) -> None:
        self.request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}",
            {"spec": {"replicas": replicas}},
            content_type="application/merge-patch+json",
        )

    def pod_names(self, namespace: str, selector: str) -> list[str]:
        query = urllib.parse.quote(selector, safe="=,!")
        doc = self.request("GET", f"/api/v1/namespaces/{namespace}/pods?labelSelector={query}")
        return [item["metadata"]["name"] for item in doc.get("items", [])]


def wait_for_pods_gone(api: KubeApi, namespace: str, selector: str, deadline: float) -> None:
    """Block until no pod matches ``selector``. Terminating pods still match, which is the point:
    xnat-web holds the archive volume and an open connection to the database until it is really gone.
    """
    while True:
        names = api.pod_names(namespace, selector)
        if not names:
            return
        if time.monotonic() >= deadline:
            raise ApiError(f"xnat-web pods still running after the timeout: {', '.join(sorted(names))}")
        time.sleep(POLL_SECONDS)


def wait_for_result(plan_dir: str, deadline: float) -> str:
    """Wait for the dump container's verdict. A timeout counts as a failure, so xnat-web comes back."""
    path = os.path.join(plan_dir, "dump-result")
    while True:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(POLL_SECONDS)


def _write(plan_dir: str, name: str, content: str = "") -> None:
    with open(os.path.join(plan_dir, name), "w", encoding="utf-8") as handle:
        handle.write(content)


def _read_plan(plan_dir: str) -> str:
    with open(os.path.join(plan_dir, "plan"), encoding="utf-8") as handle:
        return handle.read().strip()


def run(api: KubeApi, args: argparse.Namespace) -> int:
    if _read_plan(args.plan_dir) != "dump":
        print("xnat-db migrate (stop-xnat-web): nothing to dump, xnat-web is left running.")
        return 0
    deadline = time.monotonic() + args.timeout_seconds
    try:
        previous = api.read_replicas(args.namespace, args.deployment)
    except ApiError as error:
        print(f"ERROR: could not read {args.deployment}: {error}", file=sys.stderr)
        _write(args.plan_dir, "abort")
        return 1
    print(f"xnat-db migrate (stop-xnat-web): scaling {args.deployment} from {previous} to 0 for the dump.")
    try:
        api.scale(args.namespace, args.deployment, 0)
        wait_for_pods_gone(api, args.namespace, args.selector, deadline)
    except ApiError as error:
        print(f"ERROR: could not stop xnat-web: {error}", file=sys.stderr)
        _write(args.plan_dir, "abort")
        _restore(api, args, previous)
        return 1
    _write(args.plan_dir, "go")
    result = wait_for_result(args.plan_dir, deadline)
    if result == "ok":
        # Left at 0 on purpose: the helm upgrade that this hook runs inside applies xnat-web's
        # `replicas: 1` (templates/xnat-web.yaml) minutes later, onto the restored database.
        print("xnat-db migrate (stop-xnat-web): dump taken; the upgrade brings xnat-web back.")
        return 0
    print(f"ERROR: the dump did not succeed ({result}); starting xnat-web again on the old server.", file=sys.stderr)
    _restore(api, args, previous)
    return 1


def _restore(api: KubeApi, args: argparse.Namespace, replicas: int) -> None:
    """Put xnat-web back where it was. Best effort, loudly: the Job fails either way, and an
    operator who has to scale it by hand must be told so rather than find a site that is down.
    """
    try:
        api.scale(args.namespace, args.deployment, replicas)
        print(f"xnat-db migrate (stop-xnat-web): {args.deployment} is back at {replicas} replica(s).")
    except ApiError as error:
        print(
            f"ERROR: could not scale {args.deployment} back to {replicas}: {error}. "
            f"XNAT is DOWN until you run: kubectl -n {args.namespace} scale deployment "
            f"{args.deployment} --replicas={replicas}",
            file=sys.stderr,
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True, help="the xnat-web Deployment to stop")
    parser.add_argument("--selector", required=True, help="label selector for the xnat-web pods")
    parser.add_argument("--plan-dir", default="/plan")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--api-server", default=os.environ.get("KUBE_API_SERVER", "https://kubernetes.default.svc"))
    parser.add_argument("--token-file", default=os.environ.get("KUBE_TOKEN_FILE", f"{SA_DIR}/token"))
    parser.add_argument("--ca-file", default=os.environ.get("KUBE_CA_FILE", f"{SA_DIR}/ca.crt"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    token = ""
    if os.path.exists(args.token_file):
        with open(args.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
    ca_file = args.ca_file if os.path.exists(args.ca_file) else None
    return run(KubeApi(args.api_server, token, ca_file), args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
