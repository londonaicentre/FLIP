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
"""Seed a Snowflake-hosted trust OMOP from the published canonical tables (FLIP#1580).

The Snowflake twin of ``import_tables.py``. Same source, same selection, same re-seed
semantics; a different load mechanism, because Snowflake has no row-by-row INSERT path worth
using:

1. The canonical per-project CSVs are fetched from ``aicentreflip/trust-data`` at the tag in
   ``trust/.data_version`` (``dataset.fetch_canonical``, driven by the Makefile) exactly as the
   Postgres seed does, and filtered to this trust's partition with ``dataset.split_for_trust``
   -- one selection rule for both backends, so a Snowflake trust and a Postgres trust holding
   the same ``source_trust`` hold the same rows.
2. The OMOP CDM tables are created from the repo's own Postgres DDL, translated
   (``snowflake_ddl.py``). No second schema to maintain.
3. The DICOM vocabulary goes in FIRST and is skipped when already present, matching the
   Postgres seed: ``concept_relationship`` carries no unique key, so a blind re-run would
   duplicate its rows.
4. The filtered CSVs are PUT to the ``STAGES.SEED`` internal stage and loaded with COPY INTO.
5. A marker table, ``OMOP._FLIP_SEED_MARKER``, records ``projects`` / ``source_trust`` /
   provenance -- the Snowflake equivalent of the Postgres path's ``.seeded`` file, with the
   same contract: a matching marker means nothing to do; a differing one means re-seed the
   listed projects (delete their rows by person id, reload); other projects are untouched.

Everything that talks to Snowflake goes through the ``snow`` CLI with SQL on stdin, as the
SPCS node shape does (FLIP#1582), running as ``FLIP_<CODE>_OMOP_LOADER``. ``--dry-run`` prints
the SQL and executes nothing, so the whole generator is testable with no account, no
credentials and no network -- which is also how the unit tests in
``tests/unit/test_snowflake_seed.py`` run.

Object names follow the epic #1589 naming contract (database ``FLIP_TRUST_<CODE>``, schemas
``OMOP`` and ``STAGES``, stage ``STAGES.SEED``, roles ``FLIP_<CODE>_OMOP_LOADER`` /
``FLIP_<CODE>_OMOP_READER``, warehouse ``FLIP_<CODE>_WH``). The Terraform root (FLIP#1581)
creates those objects; this module only ever writes inside them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from omop_db_tools import load_dicom_vocab
from omop_db_tools.dataset import (
    CANONICAL_TABLES,
    DEFAULT_PROJECTS,
    OPTIONAL_TABLES,
    PARTITION_MODES,
    split_for_trust,
)
from omop_db_tools.import_tables import project_person_ids, validate_data_dir
from omop_db_tools.snowflake_ddl import read_ddl, snowflake_ddl_statements

#: The seed marker table, inside the OMOP schema so it travels with the data it describes
#: (the Postgres marker lives beside the pgdata volume for the same reason). Leading
#: underscore: it is FLIP bookkeeping, not an OMOP CDM table.
MARKER_TABLE = "_FLIP_SEED_MARKER"

#: Internal stage the filtered CSVs are PUT to before COPY INTO, per the naming contract.
SEED_STAGE = "SEED"

#: The DICOM vocabulary's scaffolding concept. Its presence is the "already loaded" signal,
#: the same test the Postgres loader makes.
DICOM_VOCABULARY_CONCEPT_ID = load_dicom_vocab.DICOM_VOCABULARY_CONCEPT_ID

#: Tables the DICOM vocabulary load writes, in FK-safe order.
DICOM_VOCAB_TABLES = ("CONCEPT", "VOCABULARY", "CONCEPT_CLASS", "CONCEPT_RELATIONSHIP")

#: Chunk size for ``person_id IN (...)`` delete predicates. Snowflake's expression limit is
#: generous but not unbounded, and a project can carry tens of thousands of persons.
_DELETE_CHUNK = 1000

_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,32}$")


class SnowflakeSeedError(RuntimeError):
    """A seed could not be planned or executed."""


def normalise_code(code: str) -> str:
    """Upper-case and validate a trust CODE before it is interpolated into SQL.

    Every object name below is built by interpolation, so a CODE carrying a quote, a dot or a
    space would be either an unusable identifier or an injection point. Same rule, and same
    reason, as ``trust/deploy/snowflake/naming.py`` (FLIP#1582).

    Args:
        code (str): Trust code from the kit, e.g. ``GSTT``.

    Returns:
        str: The upper-cased code.

    Raises:
        SnowflakeSeedError: The code is not a bare SQL identifier.
    """
    stripped = (code or "").strip()
    if not _CODE_RE.match(stripped):
        raise SnowflakeSeedError(
            f"trust CODE {code!r} is not usable as a Snowflake identifier: it must start with a letter "
            "and contain only letters, digits and underscores"
        )
    return stripped.upper()


@dataclass(frozen=True)
class Names:
    """The Snowflake object names this seeder writes into, derived from the trust CODE."""

    code: str

    @property
    def database(self) -> str:
        return f"FLIP_TRUST_{self.code}"

    @property
    def omop_schema(self) -> str:
        return f"{self.database}.OMOP"

    @property
    def stages_schema(self) -> str:
        return f"{self.database}.STAGES"

    @property
    def seed_stage(self) -> str:
        return f"@{self.stages_schema}.{SEED_STAGE}"

    @property
    def marker_table(self) -> str:
        return f"{self.omop_schema}.{MARKER_TABLE}"

    @property
    def loader_role(self) -> str:
        return f"FLIP_{self.code}_OMOP_LOADER"

    @property
    def warehouse(self) -> str:
        return f"FLIP_{self.code}_WH"


def names_for(code: str) -> Names:
    """Names for a trust code (validated).

    Args:
        code (str): Trust code from the kit.

    Returns:
        Names: The object names.
    """
    return Names(normalise_code(code))


# ── marker ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Marker:
    """What a trust's OMOP currently holds: projects, partition and provenance.

    Deliberately the same three facts, in the same order, as the Postgres path's ``.seeded``
    file, so ``marker_text`` renders a byte-identical string and the two backends can be
    compared by eye during a migration.
    """

    projects: tuple[str, ...]
    source_trust: int
    provenance: str

    def text(self) -> str:
        """The marker as the Postgres ``.seeded`` file writes it.

        Returns:
            str: ``projects=<space separated>\\nsource_trust=<n>\\n<provenance>\\n``.
        """
        return f"projects={' '.join(self.projects)}\nsource_trust={self.source_trust}\n{self.provenance}\n"


def provenance_for(revision: str | None, canonical_dir: str | None) -> str:
    """The provenance line of a marker.

    Args:
        revision (str | None): Published data-version tag, when seeding from the dataset.
        canonical_dir (str | None): Local canonical tree, when seeding from one instead.

    Returns:
        str: ``source=<dir>`` for a local tree (no published revision produced those rows),
        else ``version=<tag>`` -- mirroring ``SEED_PROVENANCE`` in the omop-db Makefile.

    Raises:
        SnowflakeSeedError: When neither is given.
    """
    if canonical_dir:
        return f"source={canonical_dir}"
    if revision:
        return f"version={revision}"
    raise SnowflakeSeedError("a marker needs either a data-version revision or a canonical directory")


def projects_to_reseed(wanted: Marker, current: Marker | None) -> list[str]:
    """Which of ``wanted``'s projects must be (re-)loaded, given what is there now.

    The Postgres contract, unchanged: a marker that matches exactly means nothing to do; a
    missing marker means a fresh database; a differing one -- a data-version bump, a changed
    project list, a re-partition -- means re-seed ALL the listed projects, because the marker
    records no per-project version and a partial trust is worse than a redundant reload.

    Args:
        wanted (Marker): What this run is asked to produce.
        current (Marker | None): What the marker table says is there, if anything.

    Returns:
        list[str]: Projects to re-seed; empty when the marker already matches.
    """
    if current is not None and current.text() == wanted.text():
        return []
    return list(wanted.projects)


def sql_create_marker_table(names: Names) -> str:
    """DDL for the seed-marker table."""
    return (
        f"CREATE TABLE IF NOT EXISTS {names.marker_table} (\n"
        "    projects VARCHAR NOT NULL,\n"
        "    source_trust NUMBER(38,0) NOT NULL,\n"
        "    provenance VARCHAR NOT NULL,\n"
        "    seeded_at TIMESTAMP_NTZ NOT NULL\n"
        ");"
    )


def sql_read_marker(names: Names) -> str:
    """Query returning the current marker, newest first (one row expected)."""
    return f"SELECT projects, source_trust, provenance FROM {names.marker_table} ORDER BY seeded_at DESC LIMIT 1;"


def sql_write_marker(names: Names, marker: Marker, now: datetime | None = None) -> list[str]:
    """Replace the marker row with ``marker``.

    A marker describes the whole OMOP, not one seed run, so the table holds exactly one row:
    the DELETE and the INSERT together are the update.

    Args:
        names (Names): Target objects.
        marker (Marker): The marker to record.
        now (datetime | None): Timestamp to record; defaults to now in UTC.

    Returns:
        list[str]: Two statements.
    """
    stamp = (now or datetime.now(UTC)).replace(tzinfo=None).isoformat(timespec="seconds")
    return [
        f"DELETE FROM {names.marker_table};",
        f"INSERT INTO {names.marker_table} (projects, source_trust, provenance, seeded_at) "
        f"VALUES ({_literal(' '.join(marker.projects))}, {marker.source_trust}, "
        f"{_literal(marker.provenance)}, '{stamp}'::TIMESTAMP_NTZ);",
    ]


def _literal(value: str) -> str:
    """A SQL string literal with quotes and backslashes escaped."""
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


# ── DDL, staging and loading ─────────────────────────────────────────────────────────────


def sql_session_preamble(names: Names) -> list[str]:
    """USE statements pinning role, warehouse, database and schema for the whole session.

    The role is pinned explicitly rather than relied upon as the connection default, so a
    connection configured with a broader default role still seeds as the loader and nothing
    else.
    """
    return [
        f"USE ROLE {names.loader_role};",
        f"USE WAREHOUSE {names.warehouse};",
        f"USE DATABASE {names.database};",
        f"USE SCHEMA {names.omop_schema};",
    ]


def sql_create_tables(names: Names, ddl_sql: str) -> list[str]:
    """CREATE TABLE statements for the whole OMOP CDM, translated from the Postgres DDL.

    Args:
        names (Names): Target objects.
        ddl_sql (str): Contents of the Postgres DDL file.

    Returns:
        list[str]: DDL statements, marker table last.
    """
    return [*snowflake_ddl_statements(ddl_sql, names.omop_schema), sql_create_marker_table(names)]


def sql_put(local_path: Path, stage_path: str) -> str:
    """PUT one local file onto the seed stage.

    ``AUTO_COMPRESS=TRUE`` gzips in flight; ``OVERWRITE=TRUE`` so a re-seed is not blocked by
    the previous run's file of the same name.

    Args:
        local_path (Path): File to upload. Must be absolute, so the statement means the same
            thing whatever directory the CLI was invoked from.
        stage_path (str): Target stage path, e.g. ``@DB.STAGES.SEED/cxr_project``.

    Returns:
        str: The PUT statement.

    Raises:
        SnowflakeSeedError: The path is relative or carries a quote.
    """
    if not local_path.is_absolute():
        raise SnowflakeSeedError(f"PUT needs an absolute path, got {local_path}")
    text = str(local_path)
    if "'" in text or "'" in stage_path:
        raise SnowflakeSeedError(f"Refusing to build a PUT with a quote in a path: {text!r} -> {stage_path!r}")
    return f"PUT 'file://{text}' '{stage_path}' AUTO_COMPRESS = TRUE OVERWRITE = TRUE;"


#: One CSV file format for every COPY. The canonical CSVs are pandas output: quoted where
#: needed, empty field means NULL, one header row.
_FILE_FORMAT = (
    "FILE_FORMAT = (TYPE = CSV FIELD_DELIMITER = ',' SKIP_HEADER = 1 "
    "FIELD_OPTIONALLY_ENCLOSED_BY = '\"' NULL_IF = ('') EMPTY_FIELD_AS_NULL = TRUE "
    "TRIM_SPACE = FALSE COMPRESSION = AUTO)"
)


def sql_copy_into(names: Names, table: str, columns: list[str], stage_file: str) -> str:
    """COPY INTO one table from one staged CSV, by explicit column position.

    The column list comes from the CSV's own header and the SELECT projects ``$1..$n`` in the
    same order, so a canonical table whose column order differs from the DDL's still lands in
    the right columns -- and a column the table does not have fails loudly at COPY rather than
    being loaded into the wrong one.

    ``ON_ERROR = ABORT_STATEMENT`` (the default, stated anyway): a partially loaded table is
    not a state this seeder may leave behind.

    Args:
        names (Names): Target objects.
        table (str): OMOP table name.
        columns (list[str]): Column names in CSV order.
        stage_file (str): Staged file path, e.g. ``@DB.STAGES.SEED/cxr_project/person.csv.gz``.

    Returns:
        str: The COPY INTO statement.

    Raises:
        SnowflakeSeedError: A column name is not a bare identifier.
    """
    safe = [_identifier(column) for column in columns]
    selected = ", ".join(f"${index}" for index in range(1, len(safe) + 1))
    return (
        f"COPY INTO {names.omop_schema}.{table.upper()} ({', '.join(safe)})\n"
        f"  FROM (SELECT {selected} FROM '{stage_file}')\n"
        f"  {_FILE_FORMAT}\n"
        "  ON_ERROR = ABORT_STATEMENT\n"
        "  PURGE = FALSE;"
    )


def _identifier(name: str) -> str:
    """Allow a string into SQL as an identifier only if it is a plain SQL name."""
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise SnowflakeSeedError(f"Unsafe SQL identifier: {name!r}")
    return name


def sql_delete_person_rows(names: Names, person_ids: list[int], tables: list[str] | None = None) -> list[str]:
    """DELETE one project's rows from every canonical table, keyed by its persons.

    The Snowflake twin of ``import_tables.clean_project``: reverse insert order, by
    ``person_id``, so Synthea rows and every other project are untouched by construction. Ids
    are chunked into several statements rather than one unbounded ``IN`` list.

    Args:
        names (Names): Target objects.
        person_ids (list[int]): The project's person ids.
        tables (list[str] | None): Tables to clean; defaults to ``CANONICAL_TABLES``.

    Returns:
        list[str]: DELETE statements. Empty when there are no ids (nothing to remove).

    Raises:
        SnowflakeSeedError: A person id is not an integer -- a string there would be
            interpolated straight into the predicate.
    """
    if not person_ids:
        return []
    ids = [int(value) for value in person_ids]
    statements: list[str] = []
    for table in reversed(tables or CANONICAL_TABLES):
        for start in range(0, len(ids), _DELETE_CHUNK):
            chunk = ids[start : start + _DELETE_CHUNK]
            joined = ", ".join(str(value) for value in chunk)
            statements.append(f"DELETE FROM {names.omop_schema}.{table.upper()} WHERE person_id IN ({joined});")
    return statements


def sql_dicom_vocab_loaded(names: Names) -> str:
    """Query that is non-empty exactly when the DICOM vocabulary is already loaded."""
    return (
        f"SELECT 1 AS loaded FROM {names.omop_schema}.CONCEPT WHERE concept_id = {DICOM_VOCABULARY_CONCEPT_ID} LIMIT 1;"
    )


# ── staging the CSVs ─────────────────────────────────────────────────────────────────────


def write_project_csvs(
    data_dir: Path, project: str, staging_dir: Path, num_trusts: int, source_trust: int, partition: str
) -> dict[str, Path]:
    """Filter one project's canonical tables to this trust and write them for upload.

    Uses ``dataset.split_for_trust`` -- the same selection the Postgres seed makes -- so the
    two backends disagree about nothing except where the rows end up.

    Args:
        data_dir (Path): Directory holding the canonical <project>/<table>.csv files.
        project (str): Project name.
        staging_dir (Path): Directory to write the filtered CSVs into.
        num_trusts (int): Total number of trusts being stood up.
        source_trust (int): The partition to load.
        partition (str): A ``PARTITION_MODES`` entry.

    Returns:
        dict[str, Path]: Table name to written CSV, in FK-safe insert order. A table whose
        filtered slice is empty is omitted (nothing to COPY).

    Raises:
        FileNotFoundError: A required table CSV is missing.
        ValueError: A required table has a header but no rows.
    """
    out_dir = staging_dir / project
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for table in CANONICAL_TABLES:
        csv_path = data_dir / project / f"{table}.csv"
        if not csv_path.is_file():
            if table in OPTIONAL_TABLES:
                print(f"⚠️  Optional table CSV not found, skipping: {csv_path}")
                continue
            raise FileNotFoundError(f"Required table CSV not found: {csv_path}")
        full = pd.read_csv(csv_path)
        if full.empty and table not in OPTIONAL_TABLES:
            raise ValueError(f"{csv_path} contains headers but no rows; refusing to load a truncated table")
        frame = split_for_trust(full, num_trusts, source_trust, partition)
        if frame.empty:
            print(f"ℹ️  {project}/{table}: no rows for source_trust {source_trust}, nothing to upload")
            continue
        target = out_dir / f"{table}.csv"
        frame.to_csv(target, index=False)
        written[table] = target
    return written


def write_dicom_vocab_csvs(vocab_dir: Path, staging_dir: Path) -> dict[str, Path]:
    """Write the DICOM vocabulary tables as CSVs ready for upload.

    The frames come from ``load_dicom_vocab`` so the Snowflake and Postgres paths load exactly
    the same vocabulary rows.

    Args:
        vocab_dir (Path): Directory holding the DICOM vocab CSVs (sibling zip auto-extracted).
        staging_dir (Path): Directory to write into.

    Returns:
        dict[str, Path]: Table name to written CSV, in load order.
    """
    load_dicom_vocab.ensure_vocab_dir(vocab_dir)
    out_dir = staging_dir / "dicom_vocab"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = load_dicom_vocab.vocabulary_metadata_frames()
    concepts = pd.concat([frames["CONCEPT"], load_dicom_vocab.concept_frame(vocab_dir)], ignore_index=True)
    by_table = {
        "CONCEPT": concepts,
        "VOCABULARY": frames["VOCABULARY"],
        "CONCEPT_CLASS": frames["CONCEPT_CLASS"],
        "CONCEPT_RELATIONSHIP": load_dicom_vocab.relationship_frame(vocab_dir),
    }
    written: dict[str, Path] = {}
    for table in DICOM_VOCAB_TABLES:
        target = out_dir / f"{table.lower()}.csv"
        by_table[table].to_csv(target, index=False)
        written[table] = target
    return written


def load_statements(names: Names, written: dict[str, Path], stage_prefix: str) -> list[str]:
    """PUT + COPY INTO pairs for a set of written CSVs.

    Args:
        names (Names): Target objects.
        written (dict[str, Path]): Table name to local CSV, in load order.
        stage_prefix (str): Stage directory, e.g. ``@DB.STAGES.SEED/cxr_project``.

    Returns:
        list[str]: Every PUT, then every COPY INTO. PUTs first so an upload failure costs
        nothing: no table has been touched yet.
    """
    puts = [sql_put(path.resolve(), stage_prefix) for path in written.values()]
    copies = []
    for table, path in written.items():
        columns = list(pd.read_csv(path, nrows=0).columns)
        copies.append(sql_copy_into(names, table, columns, f"{stage_prefix}/{path.name}.gz"))
    return puts + copies


# ── the snow CLI ─────────────────────────────────────────────────────────────────────────


def run_sql(
    statements: list[str],
    connection: str,
    snow: str = "snow",
    dry_run: bool = False,
    capture: bool = False,
) -> list[dict[str, object]] | None:
    """Execute SQL through ``snow sql --stdin``, or print it.

    One process for the whole batch, so ``USE ROLE`` and ``USE WAREHOUSE`` in the preamble
    apply to everything that follows -- which is also why the preamble is never split off.

    Args:
        statements (list[str]): SQL statements, each already terminated.
        connection (str): ``snow`` connection name.
        snow (str): The CLI executable.
        dry_run (bool): Print the SQL and execute nothing.
        capture (bool): Parse the CLI's JSON output and return the rows.

    Returns:
        list[dict[str, object]] | None: Result rows when ``capture``; None otherwise, and
        always None on a dry run (nothing ran, so nothing is known).

    Raises:
        SnowflakeSeedError: The CLI is not installed, or exited non-zero.
    """
    script = "\n".join(statements) + "\n"
    if dry_run:
        print(script)
        return None
    if shutil.which(snow) is None:
        raise SnowflakeSeedError(
            f"{snow!r} is not on PATH. Install the Snowflake CLI and configure a connection "
            "(`snow connection add`), or re-run with DRY_RUN=1 to print the SQL."
        )
    # Templating off: snow otherwise rewrites & and &{...} in the SQL (&& arrives as &).
    command = [snow, "sql", "--stdin", "--enable-templating", "NONE", "--connection", connection]
    if capture:
        command += ["--format", "json"]
    result = subprocess.run(command, input=script, text=True, capture_output=capture)  # noqa: S603
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise SnowflakeSeedError(
            f"`snow sql` failed with exit code {result.returncode}{': ' + detail if detail else ''}"
        )
    if not capture:
        return None
    return parse_snow_json(result.stdout)


def parse_snow_json(stdout: str) -> list[dict[str, object]]:
    """Rows from ``snow sql --format json`` output.

    The CLI emits one JSON array per statement for a multi-statement script, and a bare array
    for a single one. Only the LAST array is returned: the queries this module captures are
    always the final statement of their batch, after the session preamble.

    Args:
        stdout (str): CLI standard output.

    Returns:
        list[dict[str, object]]: The rows, or an empty list when nothing was returned.
    """
    text = stdout.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    while isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
        parsed = parsed[-1]
    if isinstance(parsed, list) and all(isinstance(row, dict) for row in parsed):
        return parsed
    return []


def _row_value(row: dict[str, object], key: str) -> object:
    """A value from a snow CLI row, whose keys may be upper or lower case."""
    for candidate in (key, key.upper(), key.lower()):
        if candidate in row:
            return row[candidate]
    return None


def read_marker(names: Names, connection: str, snow: str, dry_run: bool) -> Marker | None:
    """The marker currently recorded in the trust's OMOP, if any.

    A missing marker TABLE is not an error: it means a database that has never been seeded,
    so the query is guarded by the CREATE TABLE IF NOT EXISTS the caller runs first.

    Args:
        names (Names): Target objects.
        connection (str): ``snow`` connection name.
        snow (str): The CLI executable.
        dry_run (bool): When true nothing is executed and None is returned (unknown).

    Returns:
        Marker | None: The recorded marker, or None when there is none.
    """
    rows = run_sql(
        [*sql_session_preamble(names), sql_create_marker_table(names), sql_read_marker(names)],
        connection=connection,
        snow=snow,
        dry_run=dry_run,
        capture=True,
    )
    if not rows:
        return None
    row = rows[-1]
    projects = str(_row_value(row, "projects") or "").split()
    source_trust = _row_value(row, "source_trust")
    provenance = str(_row_value(row, "provenance") or "")
    if not projects or source_trust is None:
        return None
    return Marker(projects=tuple(projects), source_trust=int(str(source_trust)), provenance=provenance)


def dicom_vocab_present(names: Names, connection: str, snow: str, dry_run: bool) -> bool:
    """Whether the DICOM vocabulary is already in this trust's OMOP.

    On a dry run this answers False, so the printed SQL shows the full seed including the
    vocabulary load -- the interesting case to read. On a fresh trust the CONCEPT table does
    not exist yet (the seed creates it after this check), and Snowflake answers that with
    error 002003: that also means "not loaded", not a failure.
    """
    try:
        rows = run_sql(
            [*sql_session_preamble(names), sql_dicom_vocab_loaded(names)],
            connection=connection,
            snow=snow,
            dry_run=dry_run,
            capture=True,
        )
    except SnowflakeSeedError as e:
        if "002003" in str(e) and ".CONCEPT" in str(e).upper():
            return False
        raise
    return bool(rows)


# ── the seed and unseed flows ────────────────────────────────────────────────────────────


def build_seed_statements(
    names: Names,
    data_dir: Path,
    projects: list[str],
    staging_dir: Path,
    num_trusts: int,
    source_trust: int,
    partition: str,
    marker: Marker,
    ddl_sql: str,
    vocab_dir: Path | None,
    load_vocab: bool,
    clean: bool = True,
    vocab_skip_note: str | None = None,
) -> list[str]:
    """Every statement of one seed run, in order.

    Order matters and is the Postgres path's order: schema first, then the DICOM vocabulary
    (so the projects' imaging concepts resolve whether or not the licensed core vocabulary has
    been loaded), then per project a scoped delete followed by the loads, then the marker.
    The marker is written LAST, so a run that fails part way leaves a marker that does not
    match and the next run re-seeds.

    Args:
        names (Names): Target objects.
        data_dir (Path): Canonical tables directory.
        projects (list[str]): Projects to seed.
        staging_dir (Path): Where filtered CSVs are written.
        num_trusts (int): Total number of trusts.
        source_trust (int): Partition to load.
        partition (str): A ``PARTITION_MODES`` entry.
        marker (Marker): The marker to record on success.
        ddl_sql (str): Contents of the Postgres DDL file.
        vocab_dir (Path | None): DICOM vocab directory; required when ``load_vocab``.
        load_vocab (bool): Load the DICOM vocabulary (skip when already present).
        clean (bool): Delete each project's existing rows by person id first.
        vocab_skip_note (str | None): SQL comment to emit in place of the vocabulary load;
            defaults to the "already present" explanation.

    Returns:
        list[str]: The SQL.

    Raises:
        SnowflakeSeedError: ``load_vocab`` without a ``vocab_dir``.
    """
    statements = [*sql_session_preamble(names), *sql_create_tables(names, ddl_sql)]

    if load_vocab:
        if vocab_dir is None:
            raise SnowflakeSeedError("loading the DICOM vocabulary needs --vocab-dir")
        print("🩻 Staging the DICOM vocabulary (loaded first, as on Postgres)")
        vocab_files = write_dicom_vocab_csvs(vocab_dir, staging_dir)
        statements += load_statements(names, vocab_files, f"{names.seed_stage}/dicom_vocab")
    else:
        statements.append(
            vocab_skip_note
            or (
                f"-- DICOM vocabulary already present (concept {DICOM_VOCABULARY_CONCEPT_ID}); skipped.\n"
                "-- concept_relationship has no unique key, so a blind re-load would duplicate its rows."
            )
        )

    for project in projects:
        statements.append(f"-- ── {project} ──")
        if clean:
            statements += sql_delete_person_rows(names, project_person_ids(data_dir, project))
        written = write_project_csvs(data_dir, project, staging_dir, num_trusts, source_trust, partition)
        statements += load_statements(names, written, f"{names.seed_stage}/{project}")

    statements += sql_write_marker(names, marker)
    return statements


def build_unseed_statements(names: Names, data_dir: Path, projects: list[str]) -> list[str]:
    """Statements that take the listed projects OUT and load nothing.

    The unseed twin of the scoped clean: point ``data_dir`` at the tables of the cut that IS
    in the database to remove exactly those rows. The marker is left as it is, matching
    ``unseed-omop`` on Postgres -- an unseed is a deliberate intervention, and the operator
    decides what the trust should hold next.

    Args:
        names (Names): Target objects.
        data_dir (Path): Canonical tables of the cut to remove.
        projects (list[str]): Projects to remove.

    Returns:
        list[str]: The SQL.

    Raises:
        FileNotFoundError: A project's ``person.csv`` is absent -- checked before any DELETE.
    """
    for project in projects:
        if not (data_dir / project / "person.csv").is_file():
            raise FileNotFoundError(f"Cannot unseed {project}: {data_dir / project / 'person.csv'} not found")
    statements = list(sql_session_preamble(names))
    for project in projects:
        statements.append(f"-- ── removing {project} as {data_dir} describes it ──")
        statements += sql_delete_person_rows(names, project_person_ids(data_dir, project))
    return statements


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: seed or unseed a Snowflake-hosted trust OMOP."""
    parser = argparse.ArgumentParser(description="Seed a Snowflake-hosted trust OMOP from the canonical dataset.")
    parser.add_argument("--code", required=True, help="Trust code from the kit (KIT=<CODE>), e.g. GSTT.")
    parser.add_argument("--connection", default="flip-test", help="snow CLI connection name (default: %(default)s).")
    parser.add_argument("--snow", default="snow", help="snow CLI executable (default: %(default)s).")
    parser.add_argument("--projects", nargs="+", default=DEFAULT_PROJECTS, help="Projects (default: %(default)s).")
    parser.add_argument("--source-trust", type=int, default=1, help="OMOP partition to load (default: %(default)s).")
    parser.add_argument("--num-trusts", type=int, default=2, help="Total trusts being stood up (default: %(default)s).")
    parser.add_argument("--partition", choices=PARTITION_MODES, default="source_trust", help="Split mode.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/canonical"), help="Canonical tables directory.")
    parser.add_argument("--vocab-dir", type=Path, default=None, help="DICOM vocabulary directory.")
    parser.add_argument("--revision", default=None, help="Data-version tag, recorded in the marker.")
    parser.add_argument("--canonical-dir", default=None, help="Local canonical tree, recorded in the marker instead.")
    parser.add_argument("--ddl-file", type=Path, default=None, help="Postgres OMOP DDL to translate.")
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="Do nothing when the marker already records exactly these projects, partition and version, the "
        "Snowflake equivalent of `make -C trust ensure-seeded`.",
    )
    parser.add_argument(
        "--remove-only",
        action="store_true",
        help="Unseed: delete the listed projects' rows (by the person ids in --data-dir) and load nothing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the SQL and execute nothing.")
    args = parser.parse_args(argv)

    names = names_for(args.code)

    if args.remove_only:
        statements = build_unseed_statements(names, args.data_dir, args.projects)
        run_sql(statements, connection=args.connection, snow=args.snow, dry_run=args.dry_run)
        print(f"✅ Unseeded {' '.join(args.projects)} from {names.database}")
        return 0

    marker = Marker(
        projects=tuple(args.projects),
        source_trust=args.source_trust,
        provenance=provenance_for(args.revision, args.canonical_dir),
    )

    if args.if_needed and not args.dry_run:
        current = read_marker(names, args.connection, args.snow, dry_run=False)
        if not projects_to_reseed(marker, current):
            print(f"✅ OMOP ({names.database}) already seeded: {' '.join(args.projects)} ({marker.provenance})")
            return 0

    validate_data_dir(args.data_dir, args.projects)
    load_vocab = not dicom_vocab_present(names, args.connection, args.snow, dry_run=args.dry_run)
    vocab_skip_note = None
    if load_vocab and args.vocab_dir is None:
        if not args.dry_run:
            raise SnowflakeSeedError(
                "the DICOM vocabulary is not loaded in this trust and --vocab-dir was not given "
                "(run `make fetch-vocab-dicom` first)"
            )
        # A dry run has to stay runnable with nothing fetched: that is what makes the whole
        # seed reviewable offline. Say what the real run would do instead of staging it.
        load_vocab = False
        vocab_skip_note = (
            "-- DRY RUN: the DICOM vocabulary would be staged and COPYed here, first, because the\n"
            "-- projects' imaging concepts resolve against it. Pass --vocab-dir to render it."
        )

    with tempfile.TemporaryDirectory(prefix="flip-snowflake-seed-") as tmp:
        statements = build_seed_statements(
            names=names,
            data_dir=args.data_dir,
            projects=args.projects,
            staging_dir=Path(tmp),
            num_trusts=args.num_trusts,
            source_trust=args.source_trust,
            partition=args.partition,
            marker=marker,
            ddl_sql=read_ddl(args.ddl_file),
            vocab_dir=args.vocab_dir,
            load_vocab=load_vocab,
            vocab_skip_note=vocab_skip_note,
        )
        run_sql(statements, connection=args.connection, snow=args.snow, dry_run=args.dry_run)

    print(
        f"✅ Seeded {names.database}.OMOP with: {' '.join(args.projects)} "
        f"(source_trust {args.source_trust}; {marker.provenance})"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SnowflakeSeedError, FileNotFoundError, ValueError) as error:
        # An operator at a terminal gets the reason, not a stack trace; the exception types
        # above are all "you asked for something that cannot be done", never a bug here.
        sys.exit(f"❌ {error}")
