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
"""Run FLIP's authoritative trust-side validate_query on stdin; print JSON. Runs in data-access-api's venv."""

import json
import sys
from pathlib import Path

import omop_schema
import sqlglot
from data_access_api.services.cohort import validate_query
from fastapi import HTTPException
from sqlglot import exp

sql = sys.stdin.read()
try:
    emitted = validate_query(sql)
except HTTPException as e:
    print(json.dumps({"ok": False, "stage": "validator", "error": str(e.detail)}))
    sys.exit()

# Snapshot creation refuses a cohort exposing neither person_id nor accession_id (subjects are counted on them).
outer = sqlglot.parse_one(emitted, read="postgres")
while not isinstance(outer, exp.Select) and outer.args.get("this") is not None:
    outer = outer.this  # unwrap set operations / subqueries to the first SELECT
names = {n.lower() for n in getattr(outer, "named_selects", [])}
star = any(
    isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
    for e in getattr(outer, "expressions", [])
)
# Run from trust/data-access-api (cwd): every table and column must exist in the OMOP CDM DDL.
try:
    schema_error = omop_schema.check(emitted, omop_schema.load(Path("../omop-db/files/OMOPCDM_postgresql_5.4_ddl.sql")))
except Exception as e:  # a sqlglot gap in the checker must not block a query the trust would run
    schema_error = None
    print(f"schema check skipped: {e!r}", file=sys.stderr)
if schema_error:
    print(json.dumps({"ok": False, "stage": "schema", "error": schema_error}))
elif not (star or names & {"person_id", "accession_id"}):
    print(
        json.dumps(
            {
                "ok": False,
                "stage": "subject",
                "error": "Cohort must expose a person_id or accession_id column "
                "(the trust counts distinct subjects on it).",
            }
        )
    )
else:
    print(json.dumps({"ok": True, "emitted": emitted}))
