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

"""The chart ships only what a release needs.

Helm stores the whole packaged chart in every release record (a Secret capped at 1 MiB), so
without a ``.helmignore`` each ``helm upgrade`` carries this directory's tests, tool caches,
docs and operator scripts into the cluster — and a cache left behind by a local run can push
a release over the limit (``Secret "sh.helm.release.v1…" is invalid: data: Too long``).
"""

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
HELMIGNORE = CHART_DIR / ".helmignore"

#: Top-level entries a release needs: the chart, its values, the templates and the files
#: they read with ``.Files.Get`` (config/).
SHIPPED = {"Chart.yaml", "values.yaml", "values.schema.json", "templates", "config"}


def test_helmignore_excludes_everything_a_release_does_not_need() -> None:
    assert HELMIGNORE.is_file(), "trust/deploy/helm/.helmignore is missing"
    patterns = {
        line.strip() for line in HELMIGNORE.read_text().splitlines() if line.strip() and not line.startswith("#")
    }
    for required in ("tests/", "__pycache__/", ".mypy_cache/", "*.py", "*.md", "k8s-trust-*.yaml", "Makefile", "ci/"):
        assert required in patterns, f".helmignore no longer excludes {required!r}"


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_the_packaged_chart_holds_only_the_shipped_entries(tmp_path: Path) -> None:
    """What ``helm package`` produces is what every release record stores; pin its contents."""
    (tmp_path / "chart").mkdir()
    shutil.copytree(CHART_DIR, tmp_path / "chart", dirs_exist_ok=True)
    # A local run's leftovers must be dropped too, not only tracked files.
    (tmp_path / "chart" / ".mypy_cache").mkdir()
    (tmp_path / "chart" / ".mypy_cache" / "cache.db").write_bytes(b"\0" * 4096)
    (tmp_path / "chart" / "k8s-trust-SCR.yaml").write_text("flClient: {}\n")
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["helm", "package", str(tmp_path / "chart"), "-d", str(out)], check=True, capture_output=True)
    (package,) = out.glob("*.tgz")

    with tarfile.open(package) as tar:
        top_level = {name.split("/", 2)[1] for name in tar.getnames() if "/" in name}

    assert top_level == SHIPPED, f"the package ships {sorted(top_level - SHIPPED)} beyond {sorted(SHIPPED)}"
