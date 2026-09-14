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

"""Ordering and templating invariants that a rendered chart cannot self-report.

Both guards here cover defects whose failure mode is a *plausible-looking* deploy
rather than an error:

* The xnat-init Job activates the site before gating on plugin routes. Reversed,
  every plugin route 302s to /setup, the gate can never see a 2xx, and the Job
  burns its whole budget before failing with "plugin routes did not register" —
  blaming the wait rather than the ordering that caused it. That is exactly how
  the ordering bug went unnoticed the first time, and the inline script is long
  and will be edited again.
* The omop-db probe fields are templated from values rather than hardcoded. A
  half-templated block renders cleanly, silently ignores an operator's override
  and — for a field values.yaml never declares — renders empty, taking the
  Kubernetes default. Two of the four fields have slipped through this block
  twice already.
* The omop-db postStart hook provisions ``data_analyst_reader`` from the SQL the
  image ships rather than an inline copy. The inline copy it replaced granted
  ``pg_read_all_data`` — SELECT on every schema — where Compose grants USAGE on
  ``omop`` alone, and nothing about the render said so (FLIP#904). A hook that
  hand-writes the role again would render just as cleanly.

These parse the templates as text for the same reason ``test_chart_secrets.py``
does: ``helm template`` only reaches the branches its values enable (XNAT is
disabled in the kind CI values), so a rendered-output check would silently skip
most of the chart.
"""

import re
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
VALUES_FILE = CHART_DIR / "values.yaml"
XNAT_INIT_JOB = TEMPLATES_DIR / "xnat-init-job.yaml"
OMOP_DB_TEMPLATE = TEMPLATES_DIR / "omop-db.yaml"
REPO_ROOT = CHART_DIR.parents[2]  # deploy/providers/kubernetes -> repo root
CONFIGURE_XNAT = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "configure-xnat.sh"
OMOP_DB_DOCKERFILE = REPO_ROOT / "trust" / "omop-db" / "Dockerfile"

# Where the omop-db image ships the one definition of the data_analyst_reader grants
# (trust/omop-db/files/create_readonly_users.sql). The Compose trust runs it from
# /docker-entrypoint-initdb.d at first initdb; the chart's postStart hook runs this
# copy on every start, because a PVC restored by the seed-data initContainer skips
# initdb.d altogether. Both halves of that contract are asserted below: the hook
# reads the path, and the Dockerfile puts the file there. What the file grants is
# asserted beside the file, in trust/omop-db/tests/unit — this workflow is
# path-filtered to the chart and would not run for an edit to the SQL.
SHARED_READONLY_SQL = "/flip/omop/create_readonly_users.sql"
GRANT_READ_ALL = re.compile(r"\bGRANT\s+pg_read_all_data\b")

# The activation POST, and the gate that must not precede it. Matched on the calls
# rather than the surrounding prose — the comments name both endpoints in both orders.
#
# These live in configure-xnat.sh, not in the Job template: since FLIP#993 the initContainer
# runs that script instead of carrying its own inline copy, so the ordering is asserted at its
# one remaining source. The Job is checked separately for the delegation that makes it apply.
ACTIVATION_CALL = 'xnat_curl -X POST "$XNAT_URL/xapi/siteConfig"'
PLUGIN_GATE_POLL = "bash wait-for-xnat-plugins.sh"

PROBE_FIELDS = ("initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold")


def _init_container_script() -> str:
    """Return the xnat-init Job's ``initContainers`` block with comment lines dropped.

    The same file carries a second, unrelated activation script further down (the
    manual-recovery ConfigMap), so the slice is bounded by ``containers:``.

    Returns:
        str: The initContainers block, comments removed.
    """
    text = XNAT_INIT_JOB.read_text()
    start = text.index("\n      initContainers:")
    end = text.index("\n      containers:", start)
    body = text[start:end]

    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


def _omop_db_post_start_script() -> str:
    """Return the omop-db StatefulSet's ``postStart`` hook body with comment lines dropped.

    Bounded by the ``volumeMounts:`` that follows the lifecycle block, so the
    seed-data initContainer's own script further up is never in the slice.

    Returns:
        str: The postStart hook script, comments removed.
    """
    text = OMOP_DB_TEMPLATE.read_text()
    start = text.index("\n          lifecycle:")
    end = text.index("\n          volumeMounts:", start)
    body = text[start:end]

    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


def _uncommented(path: Path) -> str:
    """Return a file's text with ``#`` and ``--`` comment lines dropped.

    Args:
        path (Path): Template or SQL file to read.

    Returns:
        str: The file's non-comment lines.
    """
    return "\n".join(line for line in path.read_text().splitlines() if not line.strip().startswith(("#", "--")))


def _probe_fields(template: Path, probe: str) -> dict[str, str]:
    """Collect the scalar fields of one probe block in a template.

    Args:
        template (Path): Chart template to scan.
        probe (str): Probe key, e.g. ``livenessProbe``.

    Returns:
        dict[str, str]: ``{field name: value as written}`` for every field in PROBE_FIELDS.
    """
    fields: dict[str, str] = {}
    inside = False
    probe_indent = 0

    for line in template.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if stripped == f"{probe}:":
            inside = True
            probe_indent = indent
            continue
        if inside:
            if indent <= probe_indent:
                break
            key, _, value = stripped.partition(":")
            if key in PROBE_FIELDS:
                fields[key] = value.strip()

    return fields


def _values_declares(dotted_path: str) -> bool:
    """Report whether values.yaml declares a dotted key path.

    Walks by indentation rather than parsing: the CI job installs pytest alone, so
    no YAML parser is available.

    Args:
        dotted_path (str): Key path, e.g. ``omopDb.probes.liveness.periodSeconds``.

    Returns:
        bool: True if every segment is nested under the one before it.
    """
    segments = dotted_path.split(".")
    depth = 0
    parent_indent = -1

    for line in VALUES_FILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if depth and indent <= parent_indent:
            return False
        if stripped.split(":")[0] == segments[depth] and (depth == 0 or indent > parent_indent):
            depth += 1
            parent_indent = indent
            if depth == len(segments):
                return True

    return False


def test_the_init_container_delegates_to_configure_xnat() -> None:
    """The ordering invariant below is only binding if the Job actually runs that script."""
    script = _init_container_script()

    assert "bash configure-xnat.sh" in script, (
        "the xnat-init initContainer no longer runs configure-xnat.sh — if it has gone back to an "
        "inline copy of the configuration, assert the activation ordering against that copy too, "
        "or this suite guards a script the cluster never executes"
    )


def test_the_xnat_site_is_activated_before_the_plugin_route_gate() -> None:
    """Reversed, the gate can never see a 2xx and blames itself for the ordering."""
    script = CONFIGURE_XNAT.read_text()

    assert ACTIVATION_CALL in script, f"no site-activation call {ACTIVATION_CALL!r} in configure-xnat.sh"
    assert PLUGIN_GATE_POLL in script, f"no plugin-route gate {PLUGIN_GATE_POLL!r} in configure-xnat.sh"
    assert script.index(ACTIVATION_CALL) < script.index(PLUGIN_GATE_POLL), (
        "the plugin-route gate runs before the site is activated: every plugin route 302s to "
        "/setup until activation, so the gate burns its whole budget and then reports "
        "'plugin routes did not register' rather than the ordering that caused it"
    )


@pytest.mark.parametrize("probe", ["livenessProbe", "readinessProbe"])
def test_every_omop_db_probe_field_is_templated_from_values(probe: str) -> None:
    """A hardcoded field ignores an operator's override without rendering any differently."""
    fields = _probe_fields(OMOP_DB_TEMPLATE, probe)
    values_key = probe[: -len("Probe")]

    assert set(fields) == set(PROBE_FIELDS), (
        f"omop-db {probe} declares {sorted(fields)}, expected all of {sorted(PROBE_FIELDS)} — "
        "the vocabulary-load hook needs every one of them raised above the Kubernetes defaults"
    )
    for field, written in fields.items():
        expected = f"{{{{ .Values.omopDb.probes.{values_key}.{field} }}}}"
        assert written == expected, f"omop-db {probe}.{field} is {written!r}, expected {expected!r}"


@pytest.mark.parametrize("probe", ["liveness", "readiness"])
def test_values_declares_every_omop_db_probe_field(probe: str) -> None:
    """A field the template reads but values.yaml omits renders empty, not as an error."""
    for field in PROBE_FIELDS:
        path = f"omopDb.probes.{probe}.{field}"
        assert _values_declares(path), f"values.yaml does not declare {path}; the rendered probe field would be empty"


def test_the_omop_db_hook_provisions_the_reader_from_the_image_shipped_sql() -> None:
    """The grants are defined once, in the file both deployment paths run — not re-typed in the chart.

    Both halves of the contract: the hook ``\\i``-includes the path, and the omop-db
    Dockerfile is what puts the file there. Either half alone renders cleanly and
    fails only on the first pod start of a real trust.
    """
    script = _omop_db_post_start_script()
    dockerfile = _uncommented(OMOP_DB_DOCKERFILE)

    assert f"\\i {SHARED_READONLY_SQL}" in script, (
        "the omop-db postStart hook no longer runs the image's create_readonly_users.sql — if it has gone "
        "back to an inline definition of data_analyst_reader, that is a second copy of the grants that "
        "Compose applies, free to drift wider again (FLIP#904)"
    )
    ships_it = re.compile(rf"^COPY\s+\./files/create_readonly_users\.sql\s+{re.escape(SHARED_READONLY_SQL)}\s*$", re.M)
    assert ships_it.search(dockerfile), (
        f"trust/omop-db/Dockerfile no longer ships create_readonly_users.sql at {SHARED_READONLY_SQL}; the "
        "hook's \\i would fail on every pod start"
    )


def test_no_chart_template_grants_pg_read_all_data() -> None:
    """``pg_read_all_data`` is SELECT on every schema, present and future; the reader is scoped to ``omop``."""
    offenders = {t.name for t in sorted(TEMPLATES_DIR.glob("*.yaml")) if GRANT_READ_ALL.search(_uncommented(t))}

    assert not offenders, (
        f"{sorted(offenders)} grant pg_read_all_data — that widens a role to every schema in the cluster, "
        "which is what FLIP#904 removed from the omop-db hook; scope grants to the omop schema via "
        "omop_readonly_base instead"
    )
    # Positive control: the block the guard is about still provisions the role at all.
    assert "data_analyst_reader" in _omop_db_post_start_script()


def test_the_omop_db_hook_gates_on_tcp_and_fails_on_a_sql_error() -> None:
    """A socket ``pg_isready`` goes green on the init-time server, while initdb.d may still be running.

    The hook now runs the same file initdb.d is running at that moment; a failure
    there is killed by the kubelet mid-initdb. TCP readiness means the final server.
    And with psql outside the ``if`` (or without ON_ERROR_STOP) a failed statement
    exits 0 and the pod goes Ready on a role that never got its grants.
    """
    script = _omop_db_post_start_script()

    assert "pg_isready -h 127.0.0.1" in script, "the omop-db hook polls the socket, not TCP — it races initdb.d"
    assert "-v ON_ERROR_STOP=1" in script, "the omop-db hook runs psql without ON_ERROR_STOP — SQL errors exit 0"
    assert re.search(r"^\s*if\s+!\s+psql\b", script, re.M), (
        "psql is not the hook's `if` condition — its exit status is dropped"
    )
    assert "${DATA_ACCESS_POSTGRES_PASSWORD}" not in script, (
        "the password is shell-interpolated into the SQL again — pass it as a psql variable (\\set) so it stays "
        "off argv and cannot break the statement's quoting"
    )
