# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""AWS-free storage smoke: a model file round-trips through the dev object store (FLIP#1291).

Run by .github/workflows/local_auth_smoke.yml after the login smoke, against a hub booted
with the RustFS ``object-store`` service and no AWS credentials at all. It does what the UI
does — asks flip-api for a presigned POST policy and posts the file straight to the store,
tells flip-api the file landed, waits for the malware scan to promote it (a conditional copy
and delete against the store), fetches it back through a presigned GET and deletes it. Every
step is the production code path; only the store differs. Standard library only, on
purpose: the runner needs no virtualenv to run it.

Two invocations, because a model can only be created on an APPROVED project and every
approval path runs through a trust's cohort reply — which a hub-only stack has no trust to
give. ``--create-project`` creates the project and prints its id; the caller approves that
row in the database, together with the trust it registers — a model also needs an APPROVED
trust on the project (the workflow does both with psql on flip-db); the round trip then runs
against ``--project-id``:

    python3 tests/local_storage_smoke.py --create-project --api-url … --username … --password …
    # update projects set status='APPROVED' where id='<project_id>';
    # insert into project_trust_intersect (id, project_id, trust_id, status, decided_at, decided_as)
    #   values (gen_random_uuid(), '<project_id>', '<trust_id>', 'APPROVED', now(), 'HUB');
    python3 tests/local_storage_smoke.py --project-id <project_id> --api-url … --username … --password …

Each ``--create-project`` registers a trust, which takes an FL kit slot from the hub's pool (two on a
fresh dev hub), so on a long-lived hub rerun the round trip with ``--project-id`` of a project it
already approved rather than creating another.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_auth_smoke import SmokeFailure, _expect, _request, _token_endpoint  # noqa: E402

FILE_NAME = "trainer.py"
FILE_BODY = b'print("hello from the dev object store")\n'
SCAN_TIMEOUT_S = 180


def _json_request(method: str, url: str, *, bearer: dict[str, str], body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {**bearer, "Content-Type": "application/json"} if data is not None else dict(bearer)
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"{method} {url}: could not connect — {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def _expect_created(status: int, what: str, body: Any) -> None:
    """A create answers 200 or 201 depending on the router; either is the contract here."""
    if status not in (200, 201):
        raise SmokeFailure(f"{what}: expected HTTP 200/201, got {status}: {body}")


def _post_multipart(url: str, fields: dict[str, str], file_name: str, content: bytes, origin: str) -> int:
    """POST a presigned policy's fields plus the file, the file last, as the browser's FormData does.

    Sent with the UI's ``Origin`` like a browser would, so the store's CORS answer is checked on the way.
    """
    boundary = f"----flip-smoke-{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: text/x-python\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": origin},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            _expect_cors(response.headers.get("Access-Control-Allow-Origin"), origin, "presigned POST")
            return response.status
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(errors="replace")[:500])
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"POST {url}: could not connect (is OBJECT_STORE_PORT published there?) — {exc}") from exc


def _expect_cors(allow_origin: str | None, origin: str, what: str) -> None:
    """The browser is the only consumer that sends a preflight, and it is not in CI: check the header here."""
    if allow_origin != origin:
        raise SmokeFailure(
            f"{what}: Access-Control-Allow-Origin is {allow_origin!r}, expected {origin!r} — "
            "the store's RUSTFS_CORS_ALLOWED_ORIGINS does not carry the UI origin"
        )


def _preflight(url: str, origin: str, method: str) -> str | None:
    """A browser's CORS preflight for ``method`` on ``url``; returns the allow-origin header (None when refused)."""
    request = urllib.request.Request(
        url, method="OPTIONS", headers={"Origin": origin, "Access-Control-Request-Method": method}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as exc:
        return exc.headers.get("Access-Control-Allow-Origin")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"OPTIONS {url}: could not connect — {exc}") from exc


def _fetch(url: str, origin: str) -> tuple[int, bytes, str | None]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Origin": origin}), timeout=60) as response:
            return response.status, response.read(), response.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Access-Control-Allow-Origin")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"GET {url}: could not connect — {exc}") from exc


def _file_status(api: str, bearer: dict[str, str], model_id: str) -> str | None:
    """The file's scan status, or None while the hub has no row for it yet; any non-200 is a failure, not a wait."""
    status, step = _json_request("POST", f"{api}/step/model/{model_id}", bearer=bearer, body={})
    if status != 200:
        raise SmokeFailure(f"POST /step/model/{{id}} while waiting for the scan: HTTP {status}: {step}")
    return {f["name"]: f.get("status") for f in step.get("files", [])}.get(FILE_NAME)


def run(args: argparse.Namespace) -> None:
    api = args.api_url.rstrip("/")

    print("🔐 Password grant against the public Keycloak URL")
    status, tokens = _request(
        "POST",
        _token_endpoint(args.keycloak_url, args.realm),
        form={
            "grant_type": "password",
            "client_id": args.client_id,
            "username": args.username,
            "password": args.password,
            "scope": "openid",
        },
    )
    _expect(status, 200, "password grant", tokens)
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}

    if args.create_project:
        print("🏗️  A project and a trust for the smoke (approve them in the database, then run with --project-id)")
        tag = uuid.uuid4().hex[:8]
        status, project = _json_request(
            "POST",
            f"{api}/projects",
            bearer=bearer,
            body={"name": f"storage-smoke-{tag}", "description": "local storage smoke", "users": []},
        )
        _expect_created(status, "POST /projects", project)
        # A model also needs an APPROVED trust on the project; register one through the admin API (it
        # takes an FL kit slot from the pool, which is what the dev pool is for) for the caller's row.
        status, trust = _json_request(
            "POST", f"{api}/admin/trusts", bearer=bearer, body={"name": f"storage-smoke-{tag}", "code": f"SMK{tag[:5]}"}
        )
        _expect_created(status, "POST /admin/trusts", trust)
        print(f"project_id={project['id']}")
        print(f"trust_id={trust['id']}")
        return

    print("🤖 A model on the approved project to hang the file on")
    status, model = _json_request(
        "POST",
        f"{api}/model",
        bearer=bearer,
        body={"name": "storage-smoke", "description": "local storage smoke", "projectId": args.project_id},
    )
    _expect_created(status, "POST /model", model)
    model_id = model["id"]
    print(f"  ✅ model_id={model_id}")

    print("📤 Presigned POST straight to the object store, signed for the published port")
    status, policy = _json_request(
        "POST",
        f"{api}/files/preSignedUrl/model/{model_id}",
        bearer=bearer,
        body={"fileName": FILE_NAME, "contentType": "text/x-python"},
    )
    _expect(status, 200, "POST /files/preSignedUrl", policy)
    if not policy["url"].startswith(args.public_store_url):
        raise SmokeFailure(f"upload URL {policy['url']!r} is not signed for {args.public_store_url!r}")
    print("  🌐 The store answers the browser's CORS preflight for the UI origin only")
    _expect_cors(_preflight(policy["url"], args.ui_origin, "POST"), args.ui_origin, "preflight")
    if _preflight(policy["url"], "http://evil.example", "POST") == "http://evil.example":
        raise SmokeFailure("the store reflects a foreign Origin: RUSTFS_CORS_ALLOWED_ORIGINS is too wide")
    upload_status = _post_multipart(policy["url"], policy["fields"], FILE_NAME, FILE_BODY, args.ui_origin)
    if upload_status != 204:
        raise SmokeFailure(f"presigned POST: expected HTTP 204 from the store, got {upload_status}")
    print(f"  ✅ stored at {policy['url']}")

    print("🔬 The malware scan promotes the file (conditional copy + delete against the store)")
    status, body = _json_request(
        "POST", f"{api}/files/process-scanned-file/{model_id}/{FILE_NAME}", bearer=bearer, body={}
    )
    _expect(status, 200, "POST /files/process-scanned-file", body)
    deadline = time.monotonic() + SCAN_TIMEOUT_S
    file_status = None
    while time.monotonic() < deadline:
        file_status = _file_status(api, bearer, model_id)
        if file_status in ("INFECTED", "ERROR"):
            raise SmokeFailure(f"scan ended in {file_status}")
        if file_status == "COMPLETED":
            break
        time.sleep(3)
    if file_status != "COMPLETED":
        raise SmokeFailure(
            f"file did not reach COMPLETED within {SCAN_TIMEOUT_S}s (last: {file_status!r}) — a promotion that "
            "failed against the store stays SCANNING until the reconcile sweep; check `docker compose logs flip-api`"
        )
    print("  ✅ COMPLETED")

    print("📥 Presigned GET returns the bytes that were uploaded")
    status, download = _json_request("GET", f"{api}/files/model/{model_id}/{FILE_NAME}", bearer=bearer)
    _expect(status, 200, "GET /files/model/{id}/{file}", download)
    if not download["url"].startswith(args.public_store_url):
        raise SmokeFailure(f"download URL {download['url']!r} is not signed for {args.public_store_url!r}")
    status, content, allow_origin = _fetch(download["url"], args.ui_origin)
    if status != 200 or content != FILE_BODY:
        raise SmokeFailure(f"presigned GET: HTTP {status}, {len(content)} bytes (expected {len(FILE_BODY)})")
    _expect_cors(allow_origin, args.ui_origin, "presigned GET")
    print("  ✅ bytes match, CORS answered for the UI origin")

    print("🗑️  Delete removes it from the store")
    status, body = _json_request("DELETE", f"{api}/files/model/{model_id}/{FILE_NAME}", bearer=bearer)
    _expect(status, 200, "DELETE /files/model/{id}/{file}", body)
    status, _content, _allow_origin = _fetch(download["url"], args.ui_origin)
    if status != 404:
        raise SmokeFailure(f"object still served after delete: HTTP {status}")
    print("  ✅ gone")

    print("\n🎉 Local storage smoke passed: uploads, scanning and downloads need no AWS account.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://localhost:8080/api")
    parser.add_argument("--keycloak-url", default="http://localhost:8180", help="the PUBLIC URL (token iss)")
    parser.add_argument("--realm", default="flip")
    parser.add_argument("--client-id", default="flip-ui")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--create-project", action="store_true", help="create the smoke project, print its id, exit")
    phase.add_argument("--project-id", help="an APPROVED project to run the file round trip on")
    parser.add_argument(
        "--ui-origin",
        default="http://localhost:443",
        help="the UI's browser origin (http://localhost:<UI_PORT>): the store must answer CORS for it, and only it",
    )
    parser.add_argument(
        "--public-store-url",
        default="http://localhost:9000",
        help="flip-api's S3_PUBLIC_ENDPOINT_URL: every browser-bound presigned URL must start with it",
    )
    try:
        run(parser.parse_args(argv))
    except SmokeFailure as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
