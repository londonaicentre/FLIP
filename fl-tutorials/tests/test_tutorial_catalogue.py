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
# The parts of the tutorial contract (.claude/skills/tutorial-contract) that no other guard covers
# and a tutorial cannot derive from the tree: a twin pair's cohort query must be the same file on
# both backends and pinned as such; an NVFLARE tutorial's export output must be git-ignored; a Flower
# client must take its site identity from the context (the simulator sets no SUPERNODE_NAME, so a
# direct read hands every simulated site the whole cohort — the EHR Flower client shipped that way);
# and the cohort query must have the shape the trust-side validator accepts and the partitions rely
# on. Everything else (template parity, min_clients, import paths, the sim-tutorial mapping, offline
# apps, make targets) has its own test file. FLIP#1444.

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

FL_TUTORIALS = Path(__file__).resolve().parents[1]
REPO_ROOT = FL_TUTORIALS.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "check_tutorial_sync.sh"


def _tracked(subtree: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", subtree], cwd=REPO_ROOT, check=True, capture_output=True
    ).stdout
    return [REPO_ROOT / p for p in out.decode().split("\0") if p]


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# A tutorial root is what each harness discovers: nvflare/<category>/<name>/.env.app, and a Flower
# pyproject.toml directly under flower/ (FLIP#1500 moves Flower to <category>/<name>/ — bump the depth
# with it).
NVFLARE_ROOTS = sorted(p.parent for p in _tracked("fl-tutorials/nvflare") if p.name == ".env.app")
FLOWER_ROOTS = sorted(
    p.parent for p in _tracked("fl-tutorials/flower") if p.name == "pyproject.toml" and p.parent.parent.name == "flower"
)
assert NVFLARE_ROOTS, "no NVFLARE tutorial roots found — has the tutorial layout changed?"
assert FLOWER_ROOTS, "no Flower tutorial roots found — has the tutorial layout changed?"


def _sync_pairs() -> set[frozenset[str]]:
    """The PAIRS entries of scripts/check_tutorial_sync.sh, as unordered {copy, reference} pairs."""
    text = SYNC_SCRIPT.read_text(encoding="utf-8")
    block = re.search(r"^PAIRS=\(\n(.*?)^\)\s*$", text, re.M | re.S)
    assert block, "no PAIRS=( … ) array in scripts/check_tutorial_sync.sh — has its shape changed?"
    pairs = re.findall(r'^\s*"([^":]+):([^":]+)"', block.group(1), re.M)
    assert pairs, "no PAIRS entries parsed from scripts/check_tutorial_sync.sh — has its shape changed?"
    return {frozenset(pair) for pair in pairs}


def _twins() -> list[tuple[Path, Path]]:
    """Every tutorial present on both backends under the same name, as (nvflare root, flower root)."""
    flower_by_name = {root.name: root for root in FLOWER_ROOTS}
    return [(root, flower_by_name[root.name]) for root in NVFLARE_ROOTS if root.name in flower_by_name]


@pytest.mark.parametrize(("nvflare_root", "flower_root"), _twins(), ids=lambda p: p.name)
def test_twin_pair_query_is_pinned_in_the_sync_script(nvflare_root: Path, flower_root: Path):
    """Both backends must train on the same cohort, so a twin pair either shares a byte-pinned query.sql or
    neither side carries one (evaluation twins reuse their training tutorial's)."""
    nvflare_query, flower_query = nvflare_root / "query.sql", flower_root / "query.sql"
    assert nvflare_query.is_file() == flower_query.is_file(), (
        f"only one of {_relative(nvflare_root)} / {_relative(flower_root)} carries a query.sql"
    )
    if not nvflare_query.is_file():
        return
    pair = frozenset({_relative(nvflare_query), _relative(flower_query)})
    assert pair in _sync_pairs(), f"{_relative(flower_query)} and {_relative(nvflare_query)} are not a PAIRS entry"
    assert nvflare_query.read_bytes() == flower_query.read_bytes(), "the twin queries differ"


@pytest.mark.parametrize("root", NVFLARE_ROOTS, ids=_relative)
def test_nvflare_export_dir_is_gitignored(root: Path):
    """`make export` writes fl_job/ into the tutorial; a tutorial that does not ignore it ships it."""
    # The trailing slash is what lets a `fl_job/` pattern match a directory that does not exist yet.
    result = subprocess.run(["git", "check-ignore", "-q", f"{root / 'fl_job'}/"], cwd=REPO_ROOT)
    assert result.returncode == 0, f"{_relative(root)}/fl_job/ is not ignored by any .gitignore"


# The bare string literal, anywhere under app/: a read moved into a helper module or behind a
# constant (`SITE_ENV = "SUPERNODE_NAME"`) is the same drift as `os.getenv("SUPERNODE_NAME")`.
_SUPERNODE_LITERAL = re.compile(r"""["']SUPERNODE_NAME["']""")


@pytest.mark.parametrize("root", FLOWER_ROOTS, ids=_relative)
def test_flower_client_takes_its_identity_from_the_context(root: Path):
    """Identity comes from client_identity(context); the simulator sets no SUPERNODE_NAME at all, so any
    file under app/ that names the variable hands every simulated site the whole cohort."""
    sources = [p for p in _tracked(_relative(root / "app")) if p.suffix == ".py"]
    assert sources, f"no tracked Python under {_relative(root)}/app"
    for source in sources:
        hit = _SUPERNODE_LITERAL.search(source.read_text(encoding="utf-8"))
        assert hit is None, f"{_relative(source)} names SUPERNODE_NAME directly"
    client_app = (root / "app" / "client_app.py").read_text(encoding="utf-8")
    assert "client_identity(" in client_app, f"{_relative(root)}/app/client_app.py never calls client_identity(context)"


def _without_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


_QUERIES = sorted(root / "query.sql" for root in NVFLARE_ROOTS + FLOWER_ROOTS if (root / "query.sql").is_file())


@pytest.mark.parametrize("query", _QUERIES, ids=_relative)
def test_query_sql_reads_omop_and_orders_deterministically(query: Path):
    """The trust-side validator wants the omop schema and literal LIMIT/OFFSET; the per-site partitions
    and the frozen membership want a deterministic ORDER BY. Comments are stripped first — prose such
    as "the LIMIT below" is not a clause."""
    sql = _without_sql_comments(query.read_text(encoding="utf-8"))
    assert re.search(r"\bomop\.", sql), f"{_relative(query)} does not read the omop schema"
    assert re.search(r"\bORDER\s+BY\b", sql, re.I), f"{_relative(query)} has no ORDER BY"
    # Anywhere in the statement, not only at line start; the token after the keyword must be digits
    # (a trailing `;` is not part of it).
    for clause in re.finditer(r"\b(LIMIT|OFFSET)\b\s*(\S+)", sql, re.I):
        assert clause.group(2).rstrip(";").isdigit(), (
            f"{_relative(query)}: {clause.group(0).strip()!r} is not a literal integer"
        )
