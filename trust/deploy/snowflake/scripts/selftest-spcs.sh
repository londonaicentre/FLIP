#!/usr/bin/env bash
#
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
# Hubless self-test of a Snowflake trust, from inside the account (FLIP#1585).
#
# Runs as a job service on the data-access-api image, as the services role, with the
# trust-internal key and the AES key (what trust-api holds). It checks what only a caller
# inside the trust can see:
#
#   * every API answers /health over its SPCS service DNS;
#   * data-access-api reaches OMOP on Snowflake (/health/omop reports the backend);
#   * a cohort statistics query against the seeded OMOP returns rows;
#   * a write is refused twice: by data-access-api's query validation, and by the
#     database itself for the read-only role the service connects as.
#
# Prints one SELFTEST PASS|FAIL line per check and exits non-zero on any failure, so the
# job's status is the verdict. No secret value is printed.

set -euo pipefail
exec python - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

from data_access_api.utils.encryption import PROJECT_ID_CONTEXT, encrypt

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip(), flush=True)


def call(url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            status = response.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(errors="replace"), e.code
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw[:200]


# 1. /health on every API, over SPCS service DNS.
for service, url in json.loads(os.environ["SELFTEST_HEALTH_URLS"]).items():
    status, body = call(url)
    check(f"health:{service}", status == 200, f"-> {status}")

# 2. OMOP on Snowflake, as data-access-api sees it.
daa = os.environ["DATA_ACCESS_API_URL"]
status, body = call(f"{daa}/health/omop")
backend = body.get("backend") if isinstance(body, dict) else None
check("omop-reachable", status == 200 and isinstance(body, dict) and body.get("status") == "ok", f"backend={backend}")
check("omop-backend-is-snowflake", backend == "snowflake", f"backend={backend}")

# 3. A cohort statistics query against the seeded OMOP.
headers = {os.environ["TRUST_INTERNAL_SERVICE_KEY_HEADER"]: os.environ["TRUST_INTERNAL_SERVICE_KEY"]}
project = str(uuid.uuid4())
query = {
    "encrypted_project_id": encrypt(project, context=PROJECT_ID_CONTEXT),
    "query_id": "selftest",
    "query_name": "FLIP#1585 self-test",
    "query": "SELECT person_id, gender_concept_id FROM omop.person LIMIT 1000",
    "trust_id": "selftest",
}
status, body = call(f"{daa}/cohort", query, headers)
count = body.get("record_count") if isinstance(body, dict) else None
check("cohort-statistics", status == 200 and isinstance(count, int) and count > 0, f"-> {status}, record_count={count}")

# 4a. A write is refused by query validation.
status, _ = call(f"{daa}/cohort", {**query, "query": "INSERT INTO omop.person (person_id) VALUES (1)"}, headers)
check("write-refused-by-validation", 400 <= status < 500, f"-> {status}")

# 4b. ...and by the database, for the read-only role data-access-api connects as.
import snowflake.connector  # noqa: E402  (lazy: only the database check needs it)

conn = snowflake.connector.connect(
    host=os.environ["SNOWFLAKE_HOST"],
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    authenticator="oauth",
    token=open("/snowflake/session/token").read(),
    database=os.environ["SNOWFLAKE_DATABASE"],
    schema="OMOP",
    warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    role=os.environ["SNOWFLAKE_ROLE"],
)
cursor = conn.cursor()
for name, statement in (
    ("db-refuses-delete", "DELETE FROM PERSON WHERE 1 = 0"),
    ("db-refuses-create", "CREATE TABLE SELFTEST_SHOULD_NOT_EXIST (a INT)"),
):
    try:
        cursor.execute(statement)
        check(name, False, "it was allowed")
    except snowflake.connector.errors.ProgrammingError as e:
        check(name, True, f"as {os.environ['SNOWFLAKE_ROLE']}: {str(e).splitlines()[-1][:100]}")

print(f"SELFTEST {'ALL PASS' if failures == 0 else f'{failures} FAILED'}", flush=True)
sys.exit(1 if failures else 0)
PY
