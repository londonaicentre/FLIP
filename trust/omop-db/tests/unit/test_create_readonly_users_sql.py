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
"""Guards on what ``files/create_readonly_users.sql`` grants, and on its re-run behaviour.

The file is the ONE definition of ``data_analyst_reader``'s privileges for both
deployment paths: Compose runs it at first initdb, and the Kubernetes trust chart
runs the copy the image ships at ``/flip/omop/create_readonly_users.sql`` from its
omop-db postStart hook on every pod start (FLIP#904 — the chart used to carry an
inline copy that granted ``pg_read_all_data``, SELECT on every schema). Two
properties keep that arrangement honest, and neither is something Postgres would
report as an error:

* Read scope is the ``omop`` schema alone, through ``omop_readonly_base``. A
  grant on another schema, or of ``pg_read_all_data``, is exactly the drift the
  chart just stopped carrying.
* Re-running the file against a cluster that already holds the role converges
  it — membership, connection limit, statement_timeout — instead of skipping it.
  A role created by the pre-#904 chart hook had ``pg_read_all_data`` and no base
  membership; if the membership grant sat inside the create-only branch, the
  REVOKE below would leave that role unable to read anything at all, and the
  upgrade would take cohort queries down rather than narrow them.

These read the SQL as text (the suite runs with no database) and match on the
statements, not the prose around them.
"""

import re
from pathlib import Path

SQL = Path(__file__).resolve().parents[2] / "files" / "create_readonly_users.sql"


def _statements() -> str:
    """Return the file with ``--`` comment lines dropped.

    Returns:
        str: Non-comment lines of create_readonly_users.sql.
    """
    return "\n".join(line for line in SQL.read_text().splitlines() if not line.strip().startswith("--"))


def test_read_scope_is_the_omop_schema_through_the_base_role() -> None:
    """USAGE on ``omop`` and SELECT on its tables, on the base role the reader inherits — and nothing wider."""
    sql = _statements()

    assert "GRANT USAGE ON SCHEMA omop TO omop_readonly_base" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA omop TO omop_readonly_base" in sql
    assert re.search(r"^GRANT omop_readonly_base TO data_analyst_reader;", sql, re.M), (
        "data_analyst_reader is no longer a member of omop_readonly_base — it gets its read scope from nowhere else"
    )

    schemas_named = set(re.findall(r"\bSCHEMA\s+(\w+)", sql))
    assert schemas_named == {"omop"}, f"the file names schemas other than omop: {sorted(schemas_named - {'omop'})}"
    assert not re.search(r"\bGRANT\s+pg_read_all_data\b", sql), (
        "the file grants pg_read_all_data — SELECT on every schema, present and future, which is what "
        "FLIP#904 removed from the Kubernetes chart; scope reads to omop via omop_readonly_base"
    )


def test_membership_and_limits_are_applied_outside_the_create_only_branch() -> None:
    """A role that already exists must converge, or the pg_read_all_data REVOKE strands it (FLIP#904)."""
    sql = _statements()
    create_branch_end = sql.index("\\endif")

    for statement in (
        "GRANT omop_readonly_base TO data_analyst_reader;",
        "ALTER ROLE data_analyst_reader CONNECTION LIMIT 5;",
        "ALTER ROLE data_analyst_reader SET statement_timeout = '300s';",
    ):
        assert statement in sql, f"missing: {statement}"
        assert sql.index(statement) > create_branch_end, (
            f"{statement!r} sits inside the \\if/\\else create-only branch, so a re-run against an existing "
            "data_analyst_reader skips it — a role provisioned by the pre-#904 chart hook (pg_read_all_data, "
            "no base membership) would be revoked its only read path and never granted another"
        )


def test_the_revoke_pass_drops_pg_read_all_data() -> None:
    """Upgrading over a role the old chart hook widened must narrow it, not merely stop widening it."""
    sql = _statements()

    assert "REVOKE pg_read_all_data FROM %I" in sql, (
        "the defence-in-depth revoke loop no longer strips pg_read_all_data"
    )
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA omop FROM %I" in sql
