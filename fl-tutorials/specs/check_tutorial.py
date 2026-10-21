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
"""Check one FLIP tutorial (or the whole catalogue) against the specs in this directory.

    make -C fl-tutorials check-tutorial TUTORIAL=<name> [FL_BACKEND=nvflare|flower]
    make -C fl-tutorials check-all-tutorials

A tutorial's ``tutorial.yaml`` manifest states its profile (backend, data, model, loop, dataset
tier); ``profile.yaml`` maps each value to a spec file; every rule of every applicable spec is
evaluated and reported under its id. A failing MUST fails the run, a failing SHOULD warns, a
``manual`` rule is listed for the reviewer, and a rule the manifest waives (``waivers:``, with a
reason) is shown as waived. Heavy rules (``pytest``, ``shell``) run the guard suites, the sync
script and a GPU-free NVFLARE ``make export``; ``--static-only`` skips them, which is how
``fl-tutorials/tests/test_tutorial_specs.py`` runs this over the catalogue. The simulator is
never run here: it is the only thing that proves a tutorial trains, and it needs the dataset and,
for imaging, a GPU.

Spec rule schema and the check types are documented in ``README.md`` beside this file; the
``CHECKS`` registry below is the list the specs may use.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SPECS_DIR = Path(__file__).resolve().parent
FL_TUTORIALS = SPECS_DIR.parent
REPO_ROOT = FL_TUTORIALS.parent
MANIFEST_NAME = "tutorial.yaml"
PROFILE_FILE = SPECS_DIR / "profile.yaml"
COMMON_SPEC = "common.yaml"
LEVELS = ("MUST", "SHOULD")
HEAVY_TYPES = frozenset({"pytest", "shell"})
# The environment the tutorial-app guard tests run in (fl-tutorials/Makefile PYTEST).
PYTEST_CMD = [
    "uv",
    "run",
    "--project",
    str(REPO_ROOT / "flip-utils"),
    "--extra",
    "full",
    "--group",
    "dev",
    "pytest",
    "-q",
]
# Manifest axis -> dotted manifest field (profile.yaml keys its mapping by axis).
AXIS_FIELDS = {
    "backend": "backend",
    "data": "data",
    "model": "model",
    "loop": "loop",
    "dataset_tier": "dataset.tier",
}
APP_DIRS = {"nvflare": "app_files", "flower": "app"}
STATUS_ICONS = {"pass": "✅", "fail": "❌", "warn": "⚠️ ", "manual": "ⓘ ", "waived": "⏭ ", "skipped": "·"}


@dataclass(frozen=True)
class Tutorial:
    """One tutorial directory on one backend, with its manifest (``{}`` when the file is missing)."""

    name: str
    backend: str
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def relpath(self) -> str:
        return str(self.path.relative_to(REPO_ROOT))


@dataclass
class Outcome:
    """The result of one rule for one tutorial."""

    spec: str
    rule_id: str
    level: str
    status: str  # pass | fail | warn | manual | waived | skipped
    statement: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@functools.cache
def load_profile() -> dict[str, Any]:
    return load_yaml(PROFILE_FILE)


@functools.cache
def load_specs() -> dict[str, dict[str, Any]]:
    """Every spec file in this directory, keyed by file name (``profile.yaml`` is not a spec)."""
    return {p.name: load_yaml(p) for p in sorted(SPECS_DIR.glob("*.yaml")) if p.name != PROFILE_FILE.name}


@functools.cache
def discover_tutorials() -> list[Tutorial]:
    """Every tutorial directory the two harnesses would discover, manifest or not (read once per run)."""
    found: list[Tutorial] = []
    for env in sorted((FL_TUTORIALS / "nvflare").glob("*/*/.env.app")):
        found.append(_tutorial(env.parent, "nvflare"))
    # Both trees are <backend>/<category>/<name>/ (#1500); depth two only, so ignored leftovers at the
    # old flat fl-tutorials/flower/<name>/app in an older checkout are not mistaken for tutorials.
    for app in sorted((FL_TUTORIALS / "flower").glob("*/*/app")):
        if app.is_dir():
            found.append(_tutorial(app.parent, "flower"))
    return found


def _tutorial(path: Path, backend: str) -> Tutorial:
    manifest_path = path / MANIFEST_NAME
    manifest = load_yaml(manifest_path) if manifest_path.is_file() else {}
    return Tutorial(name=path.name, backend=backend, path=path, manifest=manifest if isinstance(manifest, dict) else {})


def get_field(mapping: dict[str, Any], dotted: str) -> Any:
    """``get_field(m, "dataset.tier")`` -> ``m["dataset"]["tier"]`` or ``None`` when any step is missing."""
    node: Any = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def manifest_errors(tutorial: Tutorial, profile: dict[str, Any], specs: dict[str, dict[str, Any]]) -> list[str]:
    """Why a manifest is not a valid profile: missing, wrong name/backend, a value outside the enums, a bad waiver."""
    m = tutorial.manifest
    if not m:
        return [f"{MANIFEST_NAME} missing or empty at {tutorial.relpath}"]
    errors: list[str] = []
    if m.get("name") != tutorial.name:
        errors.append(f"name {m.get('name')!r} != directory {tutorial.name!r}")
    if m.get("backend") != tutorial.backend:
        errors.append(f"backend {m.get('backend')!r} != tree position {tutorial.backend!r}")
    for axis, allowed in profile["axes"].items():
        value = get_field(m, AXIS_FIELDS[axis])
        if value not in allowed:
            errors.append(f"{AXIS_FIELDS[axis]}={value!r} not in {allowed}")
    for key in ("job_type", "dataset", "platform", "simulator"):
        if key not in m:
            errors.append(f"missing key {key!r}")
    if not isinstance(m.get("dataset"), dict) or not m["dataset"].get("name") or not m["dataset"].get("target"):
        errors.append("dataset needs name, tier and target")
    if not isinstance(m.get("platform"), dict) or "has_imaging" not in m["platform"]:
        errors.append("platform.has_imaging must be set")
    if get_field(m, "simulator.device") not in ("cpu", "gpu"):
        errors.append("simulator.device must be cpu or gpu")
    for optional in ("query_from", "weights_target", "twin"):
        if m.get(optional) is not None and not isinstance(m[optional], str):
            errors.append(f"{optional} must be null or a name")
    known_ids = {rule["id"] for spec in specs.values() for rule in spec.get("rules", [])}
    for rule_id, reason in (m.get("waivers") or {}).items():
        if rule_id not in known_ids:
            errors.append(f"waiver for unknown rule {rule_id}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"waiver {rule_id} has no reason")
    return errors


def applicable_specs(tutorial: Tutorial, profile: dict[str, Any], specs: dict[str, dict[str, Any]]) -> list[str]:
    """``common.yaml`` plus the spec each axis value of the manifest maps to, in axis order, de-duplicated."""
    names = [COMMON_SPEC]
    for axis, mapping in profile["specs"].items():
        value = get_field(tutorial.manifest, AXIS_FIELDS[axis])
        entry = mapping.get(value) if isinstance(value, str) else None
        if entry and entry["spec"] not in names:
            names.append(entry["spec"])
    return [n for n in names if n in specs]


def when_holds(when: dict[str, Any] | None, manifest: dict[str, Any]) -> bool:
    """A rule's ``when:``: each dotted field must equal the value, be in the list, be non-null (``"*"``) or be null."""
    for dotted, expected in (when or {}).items():
        actual = get_field(manifest, dotted)
        if expected == "*":
            if actual is None:
                return False
        elif isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

CheckFn = Callable[[dict[str, Any], Tutorial, dict[str, Any]], tuple[bool, str]]


def context_for(tutorial: Tutorial) -> dict[str, Any]:
    """The values a spec may interpolate into paths, patterns and commands."""
    m = tutorial.manifest
    return {
        "name": tutorial.name,
        "backend": tutorial.backend,
        "job_type": m.get("job_type", ""),
        "tutorial_dir": str(tutorial.path),
        "app_dir": str(tutorial.path / APP_DIRS[tutorial.backend]),
        "repo_root": str(REPO_ROOT),
        "fl_tutorials": str(FL_TUTORIALS),
        "twin": m.get("twin") or "",
        "e2e_target": get_field(m, "platform.e2e_target") or "",
        "demo_profile": get_field(m, "platform.demo_profile") or "",
        "dataset_name": get_field(m, "dataset.name") or "",
        "dataset_target": get_field(m, "dataset.target") or "",
        "weights_target": m.get("weights_target") or "",
        "query_from": m.get("query_from") or "",
    }


def render(text: str, ctx: dict[str, Any]) -> str:
    return text.format_map(ctx)


def _resolve(path: str, tutorial: Tutorial, ctx: dict[str, Any]) -> Path:
    rendered = Path(render(path, ctx))
    return rendered if rendered.is_absolute() else tutorial.path / rendered


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _flags(check: dict[str, Any]) -> int:
    return re.M | (re.I if check.get("ignore_case") else 0)


def check_file_exists(check, tutorial, ctx):
    if check.get("glob"):
        matches = sorted(tutorial.path.glob(render(check["path"], ctx)))
        return bool(matches), ", ".join(str(p.relative_to(tutorial.path)) for p in matches) or "no match"
    p = _resolve(check["path"], tutorial, ctx)
    return p.exists(), str(p.relative_to(REPO_ROOT)) if p.exists() else f"{p} missing"


def check_file_absent(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    return not p.exists(), f"{p.relative_to(REPO_ROOT)} present" if p.exists() else "absent"


def check_file_regex(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    if not p.is_file():
        return False, f"{p} missing"
    pattern = render(check["pattern"], ctx)
    found = re.search(pattern, _read(p), _flags(check)) is not None
    return found, "matched" if found else f"no match for {pattern!r} in {p.relative_to(REPO_ROOT)}"


def check_file_not_regex(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    if not p.is_file():
        return True, "file absent"
    hit = re.search(render(check["pattern"], ctx), _read(p), _flags(check))
    return hit is None, "clean" if hit is None else f"{p.relative_to(REPO_ROOT)} matches {hit.group(0)!r}"


def check_files_regex(check, tutorial, ctx):
    """``expect: all | any | none`` of the files matching ``glob`` contain ``pattern``."""
    files = sorted(p for p in tutorial.path.glob(render(check["glob"], ctx)) if p.is_file())
    expect = check.get("expect", "all")
    if not files:
        return expect == "none", f"no files match {check['glob']!r}"
    pattern, flags = render(check["pattern"], ctx), _flags(check)
    hits = [p for p in files if re.search(pattern, _read(p), flags)]
    rel = ", ".join(str(p.relative_to(tutorial.path)) for p in hits)
    if expect == "all":
        missing = [p for p in files if p not in hits]
        return not missing, "all match" if not missing else "no match in " + ", ".join(str(p.name) for p in missing)
    if expect == "any":
        return bool(hits), rel or "no file matches"
    return not hits, "none match" if not hits else "matched in " + rel


def _key_check(value: Any, present: bool, check: dict[str, Any], where: str) -> tuple[bool, str]:
    if check.get("absent"):
        return not present, f"{where}: absent" if not present else f"{where}: present ({value!r})"
    if not present:
        return False, f"{where}: missing"
    if "equals" in check and value != check["equals"]:
        return False, f"{where} = {value!r}, expected {check['equals']!r}"
    if "in" in check and value not in check["in"]:
        return False, f"{where} = {value!r}, expected one of {check['in']}"
    if check.get("value_type") == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        return False, f"{where} = {value!r} is not an int"
    if "min" in check and (not isinstance(value, int | float) or value < check["min"]):
        return False, f"{where} = {value!r} < {check['min']}"
    return True, f"{where} = {value!r}"


def check_json_key(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    if not p.is_file():
        return False, f"{p} missing"
    data = json.loads(_read(p))
    value = get_field(data, check["key"])
    return _key_check(value, value is not None, check, f"{p.name}:{check['key']}")


def check_toml_key(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    if not p.is_file():
        return False, f"{p} missing"
    with p.open("rb") as handle:
        data = tomllib.load(handle)
    value = get_field(data, check["key"])
    return _key_check(value, value is not None, check, f"{p.name}:{check['key']}")


def check_byte_identical(check, tutorial, ctx):
    p = _resolve(check["path"], tutorial, ctx)
    template = Path(render(check["template"], ctx))
    template = template if template.is_absolute() else REPO_ROOT / template
    if not p.is_file() or not template.is_file():
        return False, f"{p.name if not p.is_file() else template} missing"
    same = p.read_bytes() == template.read_bytes()
    return same, "identical" if same else f"{p.relative_to(REPO_ROOT)} differs from {template.relative_to(REPO_ROOT)}"


def check_manifest(check, tutorial, ctx):
    value = get_field(tutorial.manifest, check["field"])
    where = f"{MANIFEST_NAME}:{check['field']}"
    if check.get("is_null"):
        return value is None, f"{where} = {value!r}"
    if check.get("not_null"):
        return value is not None, f"{where} = {value!r}"
    return _key_check(value, value is not None, check, where)


def check_make_target(check, tutorial, ctx):
    """The target resolves in that Makefile (``make -n``: recipes printed, never run)."""
    target = render(check["target"], ctx) if "target" in check else get_field(tutorial.manifest, check["target_from"])
    if not target:
        return True, "no target declared"
    makefile_dir = Path(render(check["makefile_dir"], ctx))
    result = subprocess.run(
        ["make", "-n", "-C", str(makefile_dir), str(target)], capture_output=True, text=True, timeout=60
    )
    where = f"make -C {makefile_dir.relative_to(REPO_ROOT)} {target}"
    if result.returncode == 0:
        return True, f"{where}: resolves"
    # A missing rule, a parse error in the Makefile or an include it cannot read all mean the target
    # the manifest names would not run; say which.
    error = next((ln for ln in result.stderr.splitlines() if ln.strip()), f"exit {result.returncode}")
    return False, f"{where}: {error[:200]}"


def check_case_entry(check, tutorial, ctx):
    p = Path(render(check["path"], ctx))
    name = re.escape(render(check.get("name", "{name}"), ctx))
    pattern = rf"^[ \t]*([A-Za-z0-9_]+\|)*{name}(\|[A-Za-z0-9_]+)*\)"
    found = re.search(pattern, _read(p), re.M) is not None
    return found, "case entry present" if found else f"no case entry for {tutorial.name} in {p.relative_to(REPO_ROOT)}"


@functools.cache
def _run_pytest(files: tuple[str, ...]) -> tuple[bool, str]:
    result = subprocess.run([*PYTEST_CMD, *files], capture_output=True, text=True, cwd=FL_TUTORIALS, timeout=1800)
    tail = (result.stdout.strip().splitlines() or [""])[-1]
    return result.returncode == 0, tail


@functools.cache
def _run_shell(cmd: str, cwd: str) -> tuple[bool, str]:
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, cwd=cwd, timeout=1800)
    lines = [ln for ln in (result.stdout + result.stderr).splitlines() if ln.strip()]
    return result.returncode == 0, (lines[-1] if lines else f"exit {result.returncode}")[:200]


# The guard suites and the sync script are the same command for every tutorial, so under ``--all`` they
# run once per invocation; a command that interpolates the tutorial (``make export``) runs per tutorial.
def check_pytest(check, tutorial, ctx):
    return _run_pytest(tuple(str(FL_TUTORIALS / "tests" / f) for f in check["files"]))


def check_shell(check, tutorial, ctx):
    return _run_shell(render(check["cmd"], ctx), str(Path(render(check.get("cwd", "{repo_root}"), ctx))))


def check_manual(check, tutorial, ctx):
    return True, "manual"


# --- builtins: checks that need more than a path and a pattern ---------------------------------


def builtin_manifest_valid(check, tutorial, ctx):
    errors = manifest_errors(tutorial, load_profile(), load_specs())
    return not errors, "; ".join(errors) if errors else "valid"


def builtin_job_type_matches_tree(check, tutorial, ctx):
    declared = tutorial.manifest.get("job_type")
    config = tutorial.path / APP_DIRS[tutorial.backend] / "config.json"
    in_config = json.loads(_read(config)).get("job_type") if config.is_file() else None
    seen = {f"{APP_DIRS[tutorial.backend]}/config.json": in_config}
    if tutorial.backend == "nvflare":
        hit = re.search(r"^JOB_TYPE=(\S+)", _read(tutorial.path / ".env.app"), re.M)
        seen[".env.app JOB_TYPE"] = hit.group(1) if hit else None
    mismatched = {where: value for where, value in seen.items() if value != declared}
    detail = f"manifest {declared!r}" + "".join(f", {where} {value!r}" for where, value in seen.items())
    return not mismatched, detail


def builtin_required_files(check, tutorial, ctx):
    job_type = tutorial.manifest.get("job_type")
    required = REPO_ROOT / "fl-apps" / tutorial.backend / str(job_type) / "required_files.json"
    if not required.is_file():
        return False, f"no fl-apps/{tutorial.backend}/{job_type} template"
    app_dir = tutorial.path / APP_DIRS[tutorial.backend]
    missing = [f for f in json.loads(_read(required)) if not (app_dir / f).is_file()]
    return not missing, "all present" if not missing else "missing " + ", ".join(missing)


def builtin_twin_exists(check, tutorial, ctx):
    twin = tutorial.manifest.get("twin")
    if not twin:
        return True, "no twin declared"
    twins = [t for t in discover_tutorials() if t.name == tutorial.name and t.backend == twin]
    return bool(twins), f"{twin} twin {'found' if twins else 'missing'}"


def builtin_twin_same_category(check, tutorial, ctx):
    """The twin sits in the same <category>/<name> on the other backend (#1500)."""
    twin = tutorial.manifest.get("twin")
    if not twin:
        return True, "no twin declared"
    twins = [t for t in discover_tutorials() if t.name == tutorial.name and t.backend == twin]
    if not twins:
        return False, f"{twin} twin missing"
    mine, theirs = tutorial.path.parent.name, twins[0].path.parent.name
    return mine == theirs, f"{tutorial.backend}: {mine}/{tutorial.name}, {twin}: {theirs}/{tutorial.name}"


def builtin_query_source_exists(check, tutorial, ctx):
    """``query_from`` names a tutorial that carries the query.sql this one reuses (evaluation twins, the LDM)."""
    source = tutorial.manifest.get("query_from")
    if not source:
        return True, "own query.sql"
    hits = [t for t in discover_tutorials() if t.name == source and (t.path / "query.sql").is_file()]
    return bool(hits), f"{source}/query.sql {'found' if hits else 'missing'}"


def builtin_git_ignored(check, tutorial, ctx):
    """The path (which need not exist) is ignored by some .gitignore, the tutorial's or a parent's."""
    target = tutorial.path / render(check["path"], ctx)
    # A trailing slash makes `dir/` patterns match a directory that does not exist yet.
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", f"{target}/"], capture_output=True)
    ignored = result.returncode == 0
    return ignored, f"{target.relative_to(REPO_ROOT)} {'ignored' if ignored else 'not ignored by any .gitignore'}"


def builtin_twin_query_in_sync_pairs(check, tutorial, ctx):
    """When both twins carry a query.sql, the pair is pinned in scripts/check_tutorial_sync.sh PAIRS."""
    twin = tutorial.manifest.get("twin")
    if not twin or not (tutorial.path / "query.sql").is_file():
        return True, "no twin query to pin"
    others = [t for t in discover_tutorials() if t.name == tutorial.name and t.backend == twin]
    if not others or not (others[0].path / "query.sql").is_file():
        return True, "twin has no query.sql"
    pairs = _read(REPO_ROOT / "scripts" / "check_tutorial_sync.sh")
    mine, theirs = tutorial.relpath + "/query.sql", others[0].relpath + "/query.sql"
    pinned = f"{mine}:{theirs}" in pairs or f"{theirs}:{mine}" in pairs
    return pinned, "pinned in PAIRS" if pinned else f"{mine} / {theirs} not in PAIRS"


@functools.cache
def _demo_apps() -> dict[str, Any] | None:
    source = _read(REPO_ROOT / "flip-api" / "tests" / "demo_video.py")
    for node in ast.walk(ast.parse(source)):
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "APPS" for t in targets) and node.value is not None:
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def builtin_demo_profile_registered(check, tutorial, ctx):
    profile = get_field(tutorial.manifest, "platform.demo_profile")
    if not profile:
        return False, "no demo profile declared"
    apps = _demo_apps() or {}
    entry = apps.get(profile)
    if not entry:
        return False, f"APPS has no {profile!r} profile"
    app_dir = get_field(entry, f"backends.{tutorial.backend}.app_dir")
    expected = str((tutorial.path / APP_DIRS[tutorial.backend]).relative_to(REPO_ROOT))
    return app_dir == expected, f"APPS[{profile!r}].backends.{tutorial.backend}.app_dir = {app_dir!r}"


def builtin_demo_profile_has_imaging(check, tutorial, ctx):
    profile = get_field(tutorial.manifest, "platform.demo_profile")
    if not profile:
        return True, "no demo profile"
    entry = (_demo_apps() or {}).get(profile) or {}
    actual = entry.get("has_imaging", True)
    return actual == check["equals"], f"APPS[{profile!r}].has_imaging = {actual!r}"


def builtin_no_flip_utils_dependency(check, tutorial, ctx):
    with (tutorial.path / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle).get("project", {}).get("dependencies", [])
    bad = [d for d in deps if re.match(r"flip[-_]utils\b", d)]
    return not bad, "not declared" if not bad else f"declares {bad}"


def builtin_no_package_markers_above(check, tutorial, ctx):
    """No ``__init__.py`` in the category dir or ``nvflare/`` (would shadow the real nvflare package)."""
    bad = [p for p in (tutorial.path.parent / "__init__.py", tutorial.path.parent.parent / "__init__.py") if p.exists()]
    return not bad, "none" if not bad else ", ".join(str(p.relative_to(REPO_ROOT)) for p in bad)


BUILTINS: dict[str, CheckFn] = {
    "manifest_valid": builtin_manifest_valid,
    "job_type_matches_tree": builtin_job_type_matches_tree,
    "required_files": builtin_required_files,
    "twin_exists": builtin_twin_exists,
    "twin_query_in_sync_pairs": builtin_twin_query_in_sync_pairs,
    "twin_same_category": builtin_twin_same_category,
    "query_source_exists": builtin_query_source_exists,
    "git_ignored": builtin_git_ignored,
    "demo_profile_registered": builtin_demo_profile_registered,
    "demo_profile_has_imaging": builtin_demo_profile_has_imaging,
    "no_flip_utils_dependency": builtin_no_flip_utils_dependency,
    "no_package_markers_above": builtin_no_package_markers_above,
}


def check_builtin(check, tutorial, ctx):
    return BUILTINS[check["name"]](check, tutorial, ctx)


CHECKS: dict[str, CheckFn] = {
    "file_exists": check_file_exists,
    "file_absent": check_file_absent,
    "file_regex": check_file_regex,
    "file_not_regex": check_file_not_regex,
    "files_regex": check_files_regex,
    "json_key": check_json_key,
    "toml_key": check_toml_key,
    "byte_identical": check_byte_identical,
    "manifest": check_manifest,
    "make_target": check_make_target,
    "case_entry": check_case_entry,
    "builtin": check_builtin,
    "pytest": check_pytest,
    "shell": check_shell,
    "manual": check_manual,
}

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    tutorial: Tutorial, profile: dict[str, Any], specs: dict[str, dict[str, Any]], static_only: bool
) -> list[Outcome]:
    """Every applicable rule of every applicable spec, as outcomes (manifest problems first)."""
    ctx = context_for(tutorial)
    waivers = tutorial.manifest.get("waivers") or {}
    outcomes: list[Outcome] = []
    for spec_name in applicable_specs(tutorial, profile, specs):
        spec = specs[spec_name]
        for rule in spec.get("rules", []):
            if not when_holds(rule.get("when"), tutorial.manifest):
                continue
            check = rule["check"]
            level, statement = rule["level"], rule["statement"]
            if check["type"] == "manual":
                outcomes.append(Outcome(spec_name, rule["id"], level, "manual", statement))
                continue
            if static_only and check["type"] in HEAVY_TYPES:
                outcomes.append(Outcome(spec_name, rule["id"], level, "skipped", statement, "heavy check skipped"))
                continue
            try:
                passed, detail = CHECKS[check["type"]](check, tutorial, ctx)
            except Exception as exc:  # a broken check is a failure with the cause, never a crash of the whole run
                passed, detail = False, f"{type(exc).__name__}: {exc}"
            if passed and rule["id"] in waivers:
                status, detail = "warn", f"passes — the waiver is stale, remove it ({waivers[rule['id']]})"
            elif passed:
                status = "pass"
            elif rule["id"] in waivers and str(waivers[rule["id"]]).strip():
                status, detail = "waived", f"{waivers[rule['id']]} ({detail})"
            else:
                status = "fail" if level == "MUST" else "warn"
            outcomes.append(Outcome(spec_name, rule["id"], level, status, statement, detail))
    return outcomes


def report(tutorial: Tutorial, outcomes: list[Outcome], specs: dict[str, dict[str, Any]]) -> None:
    print(f"\n▶ {tutorial.name} ({tutorial.backend}) — {tutorial.relpath}")
    current = None
    for o in outcomes:
        if o.spec != current:
            current = o.spec
            print(f"  [{specs[o.spec]['id']}] {specs[o.spec]['title']}  ({o.spec})")
        suffix = f" — {o.detail}" if o.detail and o.status != "pass" else ""
        print(f"    {STATUS_ICONS[o.status]} {o.rule_id:<10} {o.level:<6} {o.statement}{suffix}")


def summarise(all_outcomes: list[Outcome]) -> int:
    counts = {s: sum(1 for o in all_outcomes if o.status == s) for s in STATUS_ICONS}
    print(
        f"\n{counts['fail']} MUST failed · {counts['warn']} SHOULD warnings · {counts['manual']} manual · "
        f"{counts['waived']} waived · {counts['skipped']} skipped · {counts['pass']} passed"
    )
    return 1 if counts["fail"] else 0


def select(name: str | None, backend: str, tutorials: list[Tutorial]) -> list[Tutorial]:
    wanted = [t for t in tutorials if (name is None or t.name == name) and (backend == "both" or t.backend == backend)]
    if name is not None and not wanted:
        hint = (
            f"fl-tutorials/nvflare/<category>/{name}/.env.app"
            if backend == "nvflare"
            else f"fl-tutorials/flower/<category>/{name}/app/"
            if backend == "flower"
            else f"fl-tutorials/nvflare/<category>/{name}/.env.app or fl-tutorials/flower/<category>/{name}/app/"
        )
        print(f"❌ no tutorial named {name!r} on {backend}: the discovery marker is {hint}", file=sys.stderr)
        sys.exit(1)
    return wanted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", nargs="?", help="tutorial name (the directory basename, same on both backends)")
    parser.add_argument("--all", action="store_true", help="check every tutorial in the catalogue")
    parser.add_argument("--backend", choices=("nvflare", "flower", "both"), default="both")
    parser.add_argument(
        "--static-only", action="store_true", help="skip pytest/shell rules (guard suites, sync, export)"
    )
    parser.add_argument("--json", action="store_true", help="print outcomes as JSON instead of the report")
    args = parser.parse_args(argv)
    if bool(args.name) == bool(args.all):
        parser.error("give a tutorial name or --all")
    profile, specs = load_profile(), load_specs()
    tutorials = select(None if args.all else args.name, args.backend, discover_tutorials())
    all_outcomes: list[Outcome] = []
    payload: list[dict[str, Any]] = []
    for tutorial in tutorials:
        outcomes = evaluate(tutorial, profile, specs, args.static_only)
        all_outcomes.extend(outcomes)
        if args.json:
            payload.append(
                {"name": tutorial.name, "backend": tutorial.backend, "outcomes": [o.as_dict() for o in outcomes]}
            )
        else:
            report(tutorial, outcomes, specs)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 1 if any(o.status == "fail" for o in all_outcomes) else 0
    code = summarise(all_outcomes)
    scope = args.name or "the catalogue"
    note = " (static rules only)" if args.static_only else " (simulator not run)"
    print(
        ("✅ " if code == 0 else "❌ ")
        + f"{scope} on {args.backend}: "
        + ("passes the contract" if code == 0 else "fix the MUST items above")
        + note
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
