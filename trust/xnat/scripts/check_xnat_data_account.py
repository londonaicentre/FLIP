#!/usr/bin/env python3
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
"""Check, on a live XNAT, what the data account can and cannot do (FLIP#2011).

imaging-api's download and upload routes run as the data account (``XNAT_DATA_USER``), a Member of FLIP projects with
no site role. This drives XNAT itself, as that account, through what those routes need and through what a flaw on
them must not reach, and prints one line per check. Exit 0 only when every check holds.

It creates two throwaway projects (one the account is a Member of, one it is not), a subject, a session and a scan,
and deletes them at the end unless ``--keep``. Run it against a test XNAT, or a site you may write two projects to:

    XNAT_URL=http://127.0.0.1:8105 XNAT_ADMIN_USER=admin XNAT_ADMIN_PASSWORD=... \\
    XNAT_DATA_USER=flip_data XNAT_DATA_PASSWORD=... python3 trust/xnat/scripts/check_xnat_data_account.py

Stdlib only, so it runs on a trust host or inside xnat-web as it is.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

REFUSED = (401, 403, 404)
# XNAT shows its public site configuration to any logged-in account; none of it may be a credential.
SECRET_WORDS = ("pass", "secret", "token", "credential", "salt", "smtp")


class Xnat:
    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def call(self, method: str, path: str, body: bytes | None = None, content_type: str = "") -> tuple[int, str]:
        request = urllib.request.Request(self.url + path, data=body, method=method)
        request.add_header("Authorization", self.auth)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the operator's own XNAT
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, what: str, ok: bool, detail: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {what}  ({detail})")
        self.failed += not ok


def project_xml(project_id: str) -> bytes:
    return (
        '<xnat:projectData xmlns:xnat="http://nrg.wustl.edu/xnat" '
        f'ID="{project_id}" secondary_ID="hub-{project_id[:8]}"><xnat:name>{project_id}</xnat:name>'
        "</xnat:projectData>"
    ).encode()


def run(admin: Xnat, data: Xnat, data_user: str, keep: bool) -> int:
    report = Report()
    member, other = str(uuid.uuid4()), str(uuid.uuid4())
    session = f"ACC{member[:6]}"
    try:
        # --- Set-up, as the site administrator: what imaging-api's provisioning account does.
        for project_id in (member, other):
            status, text = admin.call("POST", "/data/projects", project_xml(project_id), "text/xml")
            assert status in (200, 201), f"creating project {project_id}: {status} {text[:200]}"
        status, text = admin.call("PUT", f"/data/projects/{member}/users/Members/{data_user}")
        report.check("the provisioning account adds the data account as a Member", status == 200, f"HTTP {status}")
        status, _ = admin.call("PUT", f"/data/projects/{member}/subjects/S1")
        assert status in (200, 201), f"creating a subject: {status}"
        status, _ = admin.call(
            "PUT", f"/data/projects/{member}/subjects/S1/experiments/{session}?xsiType=xnat:mrSessionData"
        )
        assert status in (200, 201), f"creating a session: {status}"
        status, _ = admin.call(
            "PUT", f"/data/projects/{member}/subjects/S1/experiments/{session}/scans/1?xsiType=xnat:mrScanData"
        )
        assert status in (200, 201), f"creating a scan: {status}"
        status, _ = admin.call("PUT", f"/data/projects/{other}/subjects/S2")
        assert status in (200, 201), f"creating a subject in the other project: {status}"

        # --- The account itself.
        status, text = admin.call("GET", f"/xapi/users/{data_user}/roles")
        roles = set(json.loads(text)) if status == 200 else set()
        report.check(
            "the data account holds no site role", roles <= {"SiteUser", "non_expiring"}, f"roles {sorted(roles)}"
        )
        status, text = admin.call("GET", f"/xapi/users/{data_user}/groups")
        groups = set(json.loads(text)) if status == 200 else set()
        report.check(
            "the data account is in no all-data group",
            not any(g.startswith("ALL_DATA_") for g in groups),
            f"groups {sorted(groups)}",
        )

        # --- What the download and upload routes need: allowed.
        status, text = data.call("GET", "/data/projects?format=json")
        listed = {row["ID"] for row in json.loads(text)["ResultSet"]["Result"]} if status == 200 else set()
        report.check(
            "it lists its own project (the download route's project lookup)",
            member in listed,
            f"HTTP {status}, {len(listed)} listed",
        )
        # Not a check: XNAT lists a *protected* project (FLIP's default) to every logged-in account, by ID and name,
        # while refusing its data. That holds for every researcher account too, so it is reported, not judged here.
        print(f"INFO  a project it is not a Member of is {'listed' if other in listed else 'not listed'} (ID and name)")
        status, _ = data.call("GET", f"/data/projects/{member}/experiments/{session}?format=json")
        report.check("it reads a session of its project", status == 200, f"HTTP {status}")
        status, _ = data.call("GET", f"/data/projects/{member}/experiments/{session}/scans/1/files?format=json")
        report.check("it lists a scan's files", status == 200, f"HTTP {status}")
        status, _ = data.call(
            "PUT", f"/data/projects/{member}/subjects/S1/experiments/{session}/scans/2?xsiType=xnat:mrScanData"
        )
        report.check("it creates a scan (the upload route)", status in (200, 201), f"HTTP {status}")
        status, _ = data.call(
            "PUT",
            f"/data/projects/{member}/subjects/S1/experiments/{session}/scans/2/resources/RESULTS/files/out.txt"
            "?inbody=true",
            b"result",
            "application/octet-stream",
        )
        report.check("it uploads a file into it", status in (200, 201), f"HTTP {status}")

        # --- What a flaw on those routes must not reach: refused.
        status, _ = data.call("DELETE", f"/data/projects/{member}/subjects/S1/experiments/{session}?removeFiles=true")
        report.check("it cannot delete a session", status in REFUSED, f"HTTP {status}")
        status, _ = admin.call("GET", f"/data/projects/{member}/experiments/{session}?format=json")
        report.check("the session is still there", status == 200, f"HTTP {status}")
        status, _ = data.call("DELETE", f"/data/projects/{member}/subjects/S1/experiments/{session}/scans/1")
        report.check("it cannot delete a scan", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("DELETE", f"/data/projects/{member}")
        report.check("it cannot delete its project", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("GET", f"/data/projects/{other}/subjects?format=json")
        report.check("it cannot read a project it is not a Member of", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("PUT", f"/data/projects/{other}/subjects/S3")
        report.check("it cannot write to that project", status in REFUSED, f"HTTP {status}")
        # Judged by whether the user exists afterwards: XNAT 1.10.2.1 answers 201 to this request from a non-admin
        # and creates nobody, so the status alone would report a refusal as a success.
        probe = f"probe{member[:8]}"
        fields = {"username": probe, "password": f"P-{uuid.uuid4()}", "email": "p@example.org"}
        new_user = json.dumps({**fields, "firstName": "p", "lastName": "p"})
        status, _ = data.call("POST", "/xapi/users", new_user.encode(), "application/json")
        exists, _ = admin.call("GET", f"/xapi/users/{probe}")
        outcome = "exists" if exists == 200 else "does not exist"
        report.check("it cannot create a user", exists != 200, f"HTTP {status}, the user {outcome}")
        if exists == 200:
            admin.call("PUT", f"/xapi/users/{probe}/enabled/false")
        status, _ = data.call("PUT", f"/data/projects/{member}/users/Owners/{data_user}")
        report.check("it cannot make itself an Owner", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("PUT", f"/xapi/users/{data_user}/roles/Administrator")
        report.check("it cannot give itself a site role", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("GET", "/xapi/dqr/import/queue")
        report.check("it cannot read DQR's import queue", status in REFUSED, f"HTTP {status}")
        query = json.dumps({"studyInstanceUid": "1.2.3"}).encode()
        status, _ = data.call("POST", "/xapi/dqr/query/studies", query, "application/json")
        report.check("it cannot query the PACS (DQR)", status in REFUSED, f"HTTP {status}")
        status, _ = data.call("GET", "/xapi/notifications/smtp")
        report.check("it cannot read the mail server settings", status in REFUSED, f"HTTP {status}")
        status, text = data.call("GET", "/xapi/siteConfig")
        config = json.loads(text) if status == 200 else {}
        secret_named = [k for k in config if any(w in k.lower() for w in SECRET_WORDS)]
        report.check(
            "the site configuration it can read holds no credential", not secret_named, f"HTTP {status}, {secret_named}"
        )
    finally:
        if not keep:
            for project_id in (member, other):
                admin.call("DELETE", f"/data/projects/{project_id}?removeFiles=true")
    print(f"\n{'all checks hold' if not report.failed else f'{report.failed} check(s) FAILED'}")
    return 1 if report.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="leave the two test projects in place")
    args = parser.parse_args()
    env = os.environ
    missing = [
        k
        for k in ("XNAT_URL", "XNAT_ADMIN_PASSWORD", "XNAT_DATA_PASSWORD")
        if not env.get(k)  # values are never printed
    ]
    if missing:
        print(f"set {', '.join(missing)}", file=sys.stderr)
        return 2
    data_user = env.get("XNAT_DATA_USER", "flip_data")
    admin = Xnat(env["XNAT_URL"], env.get("XNAT_ADMIN_USER", "admin"), env["XNAT_ADMIN_PASSWORD"])
    data = Xnat(env["XNAT_URL"], data_user, env["XNAT_DATA_PASSWORD"])
    return run(admin, data, data_user, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
