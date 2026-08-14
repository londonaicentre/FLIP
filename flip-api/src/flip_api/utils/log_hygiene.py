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

import hashlib


def hash_query(query: object) -> str:
    """Returns a short, stable fingerprint of a SQL query for log correlation.

    Cohort SQL encodes patient-level selection criteria, so the platform logging
    policy (``docs/source/sys-admin.rst``, "Logging policy") keeps it out of logs
    entirely. Log this fingerprint instead: the SHA-256 of the whitespace-normalised,
    lower-cased query, truncated to 12 hex chars. The normalisation matches the
    trust services' ``utils.log_hygiene.hash_query``, so hub and trust log lines
    for the same cohort query carry the same fingerprint — and since the hub
    stores every cohort query, an operator can re-hash the stored SQL to find
    the log lines it produced. (S3 keys have their own helper:
    ``utils.s3_client.hash_s3_key``, which hashes the exact bytes because keys
    are case-sensitive.)

    Args:
        query: The SQL query, as a string or anything whose ``str()`` is the SQL text.

    Returns:
        str: A 12-hex-char fingerprint of the query.
    """
    normalised = " ".join(str(query).strip().lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:12]
