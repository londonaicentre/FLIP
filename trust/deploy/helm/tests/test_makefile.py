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

"""The chart Makefile's release upgrade reads the kit the site was synced from, for every HUB (FLIP#1204).

``upgrade-trust-k8s`` hands trust/deploy/scripts/site_upgrade.py ``trust/.env.<KIT>.<env>``; the env token comes
from ``deploy/env_mode.mk`` like every other Makefile that reads HUB, so a deployed site's kit is
``.stag`` / ``.prod`` rather than a development kit that does not exist.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _kit_file(hub: str) -> str:
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "upgrade-trust-k8s", "KIT=ABC", f"HUB={hub}", "TAG=v0.7.0"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    line = next(line for line in out.splitlines() if "site_upgrade.py plan" in line)
    return line.split("--kit-file ", 1)[1].split()[0]


@pytest.mark.parametrize(("hub", "suffix"), [("", "development"), ("stag", "stag"), ("prod", "prod")])
def test_upgrade_reads_the_kit_for_its_environment(hub, suffix):
    assert _kit_file(hub).endswith(f"/trust/.env.ABC.{suffix}")


def test_deploy_trust_k8s_regenerates_the_override_from_the_kit_first():
    """Redeploying the file an earlier sync-kit left behind shipped a stale governance document,
    and a document edit never reached the pods (FLIP#1259)."""
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy-trust-k8s", "KIT=ABC", "HUB=stag"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    regenerate = out.index("--no-apply-secret")
    deploy = out.index("helm upgrade --install")
    assert regenerate < deploy, out


def _kit(tmp_path: Path, line: str) -> Path:
    kit = tmp_path / ".env.ABC.stag"
    kit.write_text(f"FL_BACKEND=nvflare\n{line}\n")
    return kit


@pytest.mark.parametrize(
    ("line", "off"), [("TRUST_DATA_SEED=false  # our own data", True), ("TRUST_DATA_SEED=true", False), ("", False)]
)
def test_a_kit_with_its_own_data_turns_the_seed_hook_off_without_the_override(tmp_path, line, off):
    """TRUST_DATA_SEED=false (FLIP#1687) holds on a deploy that names the KIT but no override, where a missing or
    stale one would leave the chart's default and seed a real trust's OMOP."""
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy", "KIT=ABC", "HUB=stag", f"KIT_FILE={_kit(tmp_path, line)}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.replace("\\\n", " ")
    install = next(row for row in out.splitlines() if "upgrade --install" in row)
    assert ("--set trustData.seed.enabled=false" in install) is off, install


def test_a_mistyped_sample_data_setting_stops_preflight_before_it_reaches_the_cluster(tmp_path):
    kit = _kit(tmp_path, "TRUST_DATA_SEED=no")
    result = subprocess.run(
        ["make", "-C", str(CHART_DIR), "preflight", "KIT=ABC", "HUB=stag", f"KIT_FILE={kit}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    lines = [row for row in result.stdout.splitlines() if not row.startswith("make")]
    assert lines == [f"❌ TRUST_DATA_SEED must be true or false in {tmp_path}/.env.ABC.stag, not 'no'."], result.stdout


def test_xnat_init_deletes_the_previous_init_job_before_applying_it():
    """Left in place, the apply reports the Job unchanged and the wait passes at once on the old one."""
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "xnat-init", "KUBE_CONTEXT=test"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.replace("\\\n", " ")

    delete = next(line for line in out.splitlines() if " delete job " in line)
    assert "--selector app.kubernetes.io/component=xnat-init" in delete
    assert "--ignore-not-found" in delete
    assert out.index(" delete job ") < out.index(" apply -n ") < out.index(" wait job"), out


def test_sync_k8s_kit_runs_with_the_dependencies_it_declares():
    """A host python3 without PyYAML stopped every sync-kit and upgrade-trust-k8s after the Secret
    patch: the values-secrets realign refuses to write a file it cannot parse. uv installs it."""
    makefile = (CHART_DIR / "Makefile").read_text()
    script = (CHART_DIR / "sync_k8s_kit.py").read_text()

    assert "python3 $(CHART_DIR)/sync_k8s_kit.py" not in makefile
    assert makefile.count("uv run --no-config $(CHART_DIR)/sync_k8s_kit.py") == 3
    assert '# dependencies = ["pyyaml>=6.0.3"]' in script


# A stand-in for kubectl, for xnat-db-migrate-dump (FLIP#1812): every call is logged to $CALLS,
# `get` finds the xnat-db pod (or StatefulSet) unless $NO_DB, `exec … cat …PG_VERSION` answers
# $CURRENT, `exec -i` logs which script came on stdin, and `rollout` times out under $ROLLOUT_FAILS.
STUB_KUBECTL = r"""#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in
  get) [[ "$*" == *component=xnat-db* ]] && [ -z "$NO_DB" ] && echo pod/trust-release-flip-trust-xnat-db-0; exit 0 ;;
  exec)
    if [ "$2" = -i ]; then
      [[ "$(cat)" == *"pg_dump -Fc"* ]] && echo "stdin: migrate-dump" >> "$CALLS"
      [ -z "$DUMP_FAILS" ]; exit
    fi
    echo "$CURRENT"; exit 0 ;;
  rollout) [ -z "$ROLLOUT_FAILS" ]; exit ;;
esac
exit 0
"""


def _migrate_dump(tmp_path: Path, fails: bool = False, target: str = "xnat-db-migrate-dump", **env: str) -> list[str]:
    stub = tmp_path / "bin" / "kubectl"
    stub.parent.mkdir()
    stub.write_text(STUB_KUBECTL)
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["make", "-C", str(CHART_DIR), target],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}", "CALLS": str(calls), **env},
    )
    assert (result.returncode != 0) == fails, result.stdout + result.stderr
    return calls.read_text().splitlines() if calls.exists() else []


def test_an_older_xnat_db_is_dumped_after_xnat_web_stops(tmp_path):
    calls = _migrate_dump(tmp_path, CURRENT="12")
    scale = next(i for i, c in enumerate(calls) if c.startswith("scale deployment") and "--replicas=0" in c)
    dump = next(i for i, c in enumerate(calls) if c.startswith("exec -i") and c.endswith("sh -s 16"))
    assert "component=xnat-web" in calls[scale], calls
    assert scale < dump, calls
    assert calls[dump + 1] == "stdin: migrate-dump", calls


@pytest.mark.parametrize("env", [{"CURRENT": "16"}, {"CURRENT": "12", "NO_DB": "1"}], ids=["same-major", "no-xnat-db"])
def test_xnat_db_migrate_dump_is_a_no_op_otherwise(tmp_path, env):
    calls = _migrate_dump(tmp_path, **env)
    assert not any(c.startswith(("scale", "exec -i")) for c in calls), calls


def test_a_failed_dump_starts_xnat_web_again(tmp_path):
    """Nothing was upgraded, so the site must not stay down on a full disk."""
    calls = _migrate_dump(tmp_path, fails=True, CURRENT="12", DUMP_FAILS="1")
    assert calls[-1].startswith("scale deployment"), calls
    assert calls[-1].endswith("--replicas=1"), calls


def test_a_major_that_is_not_a_number_is_refused_not_read_as_already_moved(tmp_path):
    calls = _migrate_dump(tmp_path, fails=True, CURRENT="")
    assert not any(c.startswith(("scale", "exec -i")) for c in calls), calls


def test_upgrade_trust_k8s_takes_the_migration_dump_before_the_helm_upgrade():
    makefile = (CHART_DIR / "Makefile").read_text()
    apply = makefile.split("\n_upgrade-trust-k8s-apply:\n", 1)[1].split("\n\n", 1)[0]
    assert apply.index("sync-kit") < apply.index("xnat-db-migrate-dump") < apply.index("$(MAKE) deploy"), apply
    # A release pinning the old xnat-db keeps it live, so the dump it did not use is dropped.
    assert apply.index("$(MAKE) deploy") < apply.index("xnat-db-drop-unused-dump"), apply
    # A failed deploy can leave it live too.
    deploy = next(line for line in apply.replace("\\\n", "").splitlines() if "$(MAKE) deploy" in line)
    assert deploy.endswith("|| { $(MAKE) xnat-db-drop-unused-dump AFTER_FAILED_DEPLOY=1; exit 1; }"), deploy


def _drop_unused_dump(tmp_path: Path, fails: bool = False, **env: str) -> list[str]:
    return _migrate_dump(tmp_path, fails=fails, target="xnat-db-drop-unused-dump", **env)


def test_the_unused_dump_is_dropped_only_once_xnat_db_has_rolled_out(tmp_path):
    """helm upgrade does not wait: the pod being replaced still holds the dump the new one restores."""
    calls = _drop_unused_dump(tmp_path)
    rollout = next(i for i, c in enumerate(calls) if c.startswith("rollout status"))
    assert rollout < next(i for i, c in enumerate(calls) if c.startswith("exec -i")), calls


def test_a_rollout_that_times_out_drops_nothing(tmp_path):
    calls = _drop_unused_dump(tmp_path, fails=True, ROLLOUT_FAILS="1")
    assert not any(c.startswith("exec -i") for c in calls), calls


def test_after_a_failed_deploy_the_dump_is_dropped_whatever_the_rollout_did(tmp_path):
    calls = _drop_unused_dump(tmp_path, ROLLOUT_FAILS="1", AFTER_FAILED_DEPLOY="1")
    assert any(c.startswith("exec -i") for c in calls), calls


def test_no_xnat_db_means_no_rollout_wait_and_no_drop(tmp_path):
    calls = _drop_unused_dump(tmp_path, NO_DB="1")
    assert not any(c.startswith(("rollout", "exec")) for c in calls), calls
