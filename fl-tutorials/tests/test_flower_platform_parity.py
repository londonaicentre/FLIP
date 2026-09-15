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
# Drift guard: a Flower tutorial's app/ must be the code the platform actually runs.
#
# flip-api's bundler (fl_service.bundle_flower_application) uploads the fl-apps template into the
# app bundle FIRST, then copies the researcher's model files in only where the key is still free:
#
#     if s3.object_exists(dst_key):
#         logger.warning(f"The file name {rel} is reserved for this base application, ...")
#         continue
#
# So every file the template ships is RESERVED. A tutorial that ships its own server_app.py or
# strategy.py has that copy silently discarded at deploy time and runs the template's instead.
# That is deliberate — a researcher must not be able to replace platform-owned code by naming a
# file the same thing — but it means a tutorial whose copy has drifted is a lie: `flwr run` in the
# tutorial directory exercises code the platform will never execute, and the divergence surfaces
# only as a runtime failure on a real trust.
#
# Discovery rather than a list. scripts/check_tutorial_sync.sh pinned these pairs by hand, which
# holds only as long as every future author remembers to extend it — a new tutorial could be
# merged with a drifted server_app.py and leave CI green. Deriving the pairs from the tree instead
# makes the invariant cover a tutorial the moment it exists.
#
# Static, like its neighbours: this reads bytes and JSON, so it costs nothing and needs no GPU,
# no dataset and no FL image.

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIALS_DIR = REPO_ROOT / "fl-tutorials" / "flower"
TEMPLATES_DIR = REPO_ROOT / "fl-apps" / "flower"


def _tutorial_apps() -> list[Path]:
    """Every fl-tutorials/flower/<name>/app directory, sorted for stable test ids."""
    return sorted(p for p in TUTORIALS_DIR.glob("*/app") if p.is_dir())


TUTORIAL_APPS = _tutorial_apps()


def _tutorial_id(app_dir: Path) -> str:
    return app_dir.parent.name


def _job_type(app_dir: Path) -> str:
    """The job type an app declares, which selects the fl-apps template that will deploy it."""
    return json.loads((app_dir / "config.json").read_text(encoding="utf-8"))["job_type"]


def _reserved_files(job_type: str) -> list[Path]:
    """Files the template ships, and which the bundler therefore refuses to let a tutorial replace."""
    return sorted(p for p in (TEMPLATES_DIR / job_type / "app").glob("*.py"))


def _is_inert(source: Path) -> bool:
    """True if a module contains nothing but a docstring, so its content cannot alter behaviour."""
    body = ast.parse(source.read_text(encoding="utf-8")).body
    if not body:
        return True
    return len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)


def test_discovery_actually_finds_the_flower_tutorials():
    """Guard the guard: an empty parametrize list makes pytest skip the suite, not fail it."""
    assert TUTORIAL_APPS, f"no tutorial app/ directories found under {TUTORIALS_DIR} — the glob has drifted"


@pytest.mark.parametrize("app_dir", TUTORIAL_APPS, ids=_tutorial_id)
def test_tutorial_declares_a_job_type_backed_by_a_template(app_dir: Path):
    """An app whose job type has no fl-apps template cannot be bundled at all.

    flip-api resolves FL_APP_BASE_DIR/flower/<job_type>/ at upload time, so a typo or an invented
    job type fails the researcher's very first upload with a stack trace from inside the bundler,
    long after the tutorial looked finished.
    """
    config = app_dir / "config.json"
    assert config.is_file(), f"{_tutorial_id(app_dir)} ships no app/config.json, so flip-api cannot bundle it"

    declared = json.loads(config.read_text(encoding="utf-8")).get("job_type")
    assert declared, f"{config} declares no job_type"

    template = TEMPLATES_DIR / declared / "app"
    available = sorted(p.name for p in TEMPLATES_DIR.iterdir() if (p / "app").is_dir())
    assert template.is_dir(), f"job_type {declared!r} has no template at {template} — available: {available}"


@pytest.mark.parametrize("app_dir", TUTORIAL_APPS, ids=_tutorial_id)
def test_tutorial_ships_every_file_its_job_type_requires(app_dir: Path):
    """The bundler validates the manifest before it uploads anything, so a gap fails the upload.

    Checked here rather than left to that runtime check because the failure arrives as a
    FileNotFoundError from flip-api during an e2e run, by which point a trust has already pulled
    imaging for the project.
    """
    job_type = _job_type(app_dir)
    manifest = json.loads((TEMPLATES_DIR / "required_files.json").read_text(encoding="utf-8"))
    assert job_type in manifest, (
        f"job_type {job_type!r} has no entry in fl-apps/flower/required_files.json — the bundler "
        f"treats an empty manifest as a deployment misconfiguration and refuses the upload"
    )
    required = manifest[job_type]

    missing = [name for name in required if not (app_dir / name).is_file()]
    assert not missing, f"{_tutorial_id(app_dir)} (job_type {job_type!r}) is missing required file(s): {missing}"


@pytest.mark.parametrize("app_dir", TUTORIAL_APPS, ids=_tutorial_id)
def test_platform_owned_files_are_identical_in_the_tutorial(app_dir: Path):
    """Every file the template reserves must be present, and equivalent, in the tutorial's app/.

    Present, because the tutorial has to run on the flwr simulator on its own; equivalent, because
    the platform runs the template's copy regardless of what the tutorial ships. A tutorial that
    satisfies only one of the two is the failure this guard exists for: it runs locally and
    deploys as something else.

    Byte-identity is the rule, with one carve-out that is itself checked rather than assumed: a
    file that carries no executable code in *either* tree cannot make the two behave differently,
    so it may differ. That is what lets each tutorial's ``__init__.py`` keep its own docstring
    while still failing the day anyone puts a statement in one.
    """
    job_type = _job_type(app_dir)
    reserved = _reserved_files(job_type)
    assert reserved, f"template {job_type!r} ships no app/*.py — the reserved set cannot be empty"

    for template_file in reserved:
        tutorial_file = app_dir / template_file.name
        assert tutorial_file.is_file(), (
            f"{_tutorial_id(app_dir)} does not ship {template_file.name}, which "
            f"fl-apps/flower/{job_type} owns — the tutorial cannot run on the simulator as written"
        )
        if tutorial_file.read_bytes() == template_file.read_bytes():
            continue
        both_inert = _is_inert(tutorial_file) and _is_inert(template_file)
        assert both_inert, (
            f"{_tutorial_id(app_dir)}/app/{template_file.name} has drifted from "
            f"fl-apps/flower/{job_type}/app/{template_file.name}, which is the copy the platform "
            f"runs — the tutorial's copy is discarded at bundle time, so the tutorial deploys as "
            f"something other than what it shows. Resync by copying the fl-apps file over it."
        )
