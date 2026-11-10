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
"""A query file keeps its licence header on disk, but a project's cohort query never carries it (FLIP#1746)."""

import ast
from pathlib import Path

import pytest

from tests.query_files import read_query_file

REPO = Path(__file__).resolve().parents[3]
HEADER = (
    "-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London\n"
    '-- Licensed under the Apache License, Version 2.0 (the "License");\n'
    "-- you may not use this file except in compliance with the License.\n"
    "-- You may obtain a copy of the License at\n"
    "--     http://www.apache.org/licenses/LICENSE-2.0\n"
    "-- Unless required by applicable law or agreed to in writing, software\n"
    '-- distributed under the License is distributed on an "AS IS" BASIS,\n'
    "-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
    "-- See the License for the specific language governing permissions and\n"
    "-- limitations under the License.\n"
)


def test_strips_the_leading_licence_header_and_its_blank_line(tmp_path: Path) -> None:
    query = tmp_path / "query.sql"
    query.write_text(HEADER + "\n-- the cohort: every CT study\nSELECT * FROM omop.image_occurrence\n")

    assert read_query_file(query) == "-- the cohort: every CT study\nSELECT * FROM omop.image_occurrence\n"


def test_leaves_a_query_without_a_header_unchanged(tmp_path: Path) -> None:
    query = tmp_path / "query.sql"
    body = "-- people born before 1980\nSELECT person_id FROM omop.person WHERE year_of_birth < 1980\n"
    query.write_text(body)

    assert read_query_file(query) == body


def test_keeps_licence_words_that_are_not_the_leading_header(tmp_path: Path) -> None:
    query = tmp_path / "query.sql"
    body = "SELECT 1\n-- Licensed under the Apache License, Version 2.0 (quoted, not a header)\n"
    query.write_text(body)

    assert read_query_file(query) == body


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (REPO / "fl-tutorials").rglob("*.sql") if "/.venv/" not in str(p))
    + [REPO / "flip-api/tests/example_query.sql"],
    ids=lambda p: str(p.relative_to(REPO)),
)
def test_every_shipped_query_file_has_its_header_on_disk_and_none_in_the_cohort_query(path: Path) -> None:
    assert "Licensed under the Apache" in path.read_text()
    assert "Licensed under the Apache" not in read_query_file(path)


@pytest.mark.parametrize(
    "module", ["e2e_smoke.py", "demo_video.py", "seed_demo_projects.py", "debug_prelaunch_task.py"]
)
def test_scripts_that_submit_a_query_file_read_it_through_read_query_file(module: str) -> None:
    source = (REPO / "flip-api/tests" / module).read_text()
    tree = ast.parse(source)
    raw_reads = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and "query" in ast.unparse(node.func.value).lower()
    ]
    raw_opens = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        and node.args
        and ".sql" in ast.unparse(node.args[0])
    ]
    assert raw_reads == [], f"{module} reads a query file raw at lines {raw_reads}"
    assert raw_opens == [], f"{module} opens a .sql file raw at lines {raw_opens}"
    assert "read_query_file" in source
