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
"""Run the trust's authoritative validate_query over SQL strings (FLIP#1556 benchmark).

Runs in trust/data-access-api's venv, as its contract test does. Reads a JSON list of SQL strings on
stdin and prints a JSON list of error messages (null where the query passes).
"""

import json
import os
import sys

# Dummy settings, as tests/conftest.py sets them: validate_query never connects to a database.
for key, value in {
    "DATA_ACCESS_POSTGRES_USER": "bench",
    "DATA_ACCESS_POSTGRES_PASSWORD": "bench",  # pragma: allowlist secret
    "OMOP_POSTGRES_DB": "bench",
    "AES_KEY_BASE64": "QgZ+TBA0lUxcuCiRPLneFe/JjMaUEUJWHACHHGz2gGA=",  # pragma: allowlist secret
}.items():
    os.environ.setdefault(key, value)

from data_access_api.services.cohort import validate_query  # noqa: E402


def check(sql: str) -> str | None:
    try:
        validate_query(sql)
    except Exception as e:  # the validator raises HTTPException / ValueError with a reason
        return getattr(e, "detail", None) or str(e) or type(e).__name__
    return None


if __name__ == "__main__":
    print(json.dumps([check(sql) for sql in json.load(sys.stdin)]))
