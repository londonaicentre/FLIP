# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the demo project seeder's --states subset selection (select_entries) and catalogue ownership."""

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from tests import seed_demo_projects
from tests.e2e_smoke import SmokeFailure
from tests.seed_demo_projects import build_project, select_entries

CATALOGUE = [
    {"name": "Spleen", "state": "approved_model"},
    {"name": "Pneumothorax", "state": "staged"},
    {"name": "Cardiomegaly", "state": "unstaged"},
]


def test_no_selection_returns_full_catalogue() -> None:
    assert select_entries(CATALOGUE, None) == CATALOGUE
    assert select_entries(CATALOGUE, "") == CATALOGUE


def test_selects_requested_states_only() -> None:
    assert select_entries(CATALOGUE, "staged,unstaged") == CATALOGUE[1:]


def test_whitespace_and_empty_tokens_are_tolerated() -> None:
    assert select_entries(CATALOGUE, " staged , ,unstaged ") == CATALOGUE[1:]


def test_unknown_state_raises_with_known_ladder() -> None:
    with pytest.raises(SmokeFailure, match="Unknown --states value.*bogus.*unstaged"):
        select_entries(CATALOGUE, "staged,bogus")


ADMIN = {"authorization": "Bearer admin"}
RESEARCHER = {"authorization": "Bearer researcher"}
RESEARCHER_ID = "11111111-1111-1111-1111-111111111111"


def _response(body: Any, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def _record_posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str]]]:
    posts: list[tuple[str, dict[str, str]]] = []

    def post(client, path: str, payload: dict[str, Any], headers, timeout: int = 30) -> requests.Response:
        posts.append((path, headers))
        return _response({"id": "p1", "query_id": "q1"})

    monkeypatch.setattr(seed_demo_projects, "_post", post)
    monkeypatch.setattr(seed_demo_projects.e2e_smoke, "wait_for_trusts_responded", lambda *a, **k: None)
    return posts


def test_the_owner_builds_the_project_and_the_admin_approves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posts = _record_posts(monkeypatch)
    query = tmp_path / "query.sql"
    query.write_text("SELECT 1")
    entry = {"name": "Spleen", "description": "d", "state": "approved", "query_file": query}

    build_project(requests.Session(), ADMIN, entry, [{"id": "t1"}], RESEARCHER)

    assert [(path, headers["authorization"]) for path, headers in posts] == [
        ("/projects/", "Bearer researcher"),
        ("/cohort/save/", "Bearer researcher"),
        ("/cohort/submit/", "Bearer researcher"),
        ("/projects/p1/stage/", "Bearer researcher"),
        ("/step/project/p1/approve/", "Bearer admin"),
    ]


def test_without_an_owner_the_admin_owns_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    posts = _record_posts(monkeypatch)

    build_project(requests.Session(), ADMIN, {"name": "Spleen", "description": "d", "state": "unstaged"}, [])

    assert posts == [("/projects/", ADMIN)]


@pytest.mark.parametrize(("owner_id", "warned"), [(RESEARCHER_ID, False), ("someone-else", True)])
def test_an_existing_entry_is_skipped_and_a_foreign_owner_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], owner_id: str, warned: bool
) -> None:
    monkeypatch.setattr(
        seed_demo_projects, "existing_projects", lambda *a: {"Spleen": {"id": "p1", "ownerId": owner_id}}
    )
    built: list[Any] = []
    monkeypatch.setattr(seed_demo_projects, "build_project", lambda *a: built.append(a))
    owner = seed_demo_projects.Owner(RESEARCHER_ID, RESEARCHER)

    seed_demo_projects.seed_catalogue(requests.Session(), ADMIN, [], [{"name": "Spleen"}], owner)

    assert built == []
    assert ("has another owner" in capsys.readouterr().out) is warned
