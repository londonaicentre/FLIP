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
"""trust/Makefile's upgrade verb never reaches a first-install step (FLIP#1204).

`up-trust` re-seeds OMOP / Orthanc whenever the seed markers differ from the kit and runs
`xnat-reset`; `upgrade-trust` exists so a live site can move to a release without either. These
dry-run the targets against a scratch kit, so a refactor that reuses up-trust's prerequisites
fails here rather than on a live site.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from make_dry_run import KIT, assert_data_safe, dry_run, kit_with, main, run_target  # noqa: E402


class UpgradeTrust(unittest.TestCase):
    def test_upgrade_trust_pulls_recreates_and_upgrades_xnat_without_reset(self):
        out = dry_run("upgrade-trust", "TAG=v0.6.0", "YES=1")
        assert "site_upgrade.py plan" in out, out
        assert "_upgrade-trust-apply" in out, out
        assert_data_safe(out, "upgrade-trust")

    def test_apply_phase_names_the_tag_only_via_the_kit(self):
        """The apply sub-make re-includes the kit, so the tag it uses is whatever the resolver wrote."""
        out = dry_run("_upgrade-trust-apply")
        assert "compose" in out, out
        assert " pull" in out, out
        assert " up -d" in out, out
        assert "upgrade-xnat" in out, out
        assert "sha-badcff1" in out, out  # printed from the kit, not from TAG=
        assert_data_safe(out, "_upgrade-trust-apply")

    def test_apply_phase_honours_a_cpu_only_override(self):
        """upgrade-trust-ec2 passes NUM_AVAILABLE_GPUS=0: it must beat a template kit's 1 (FLIP#1204).

        The EC2 trust is GPU-less; with the kit's value the apply phase adds the GPU overlay and the
        recreated fl-client fails with "could not select device driver nvidia".
        """
        gpu_kit = kit_with("NUM_AVAILABLE_GPUS=0", "NUM_AVAILABLE_GPUS=1")
        assert ".gpu.yml" in dry_run("_upgrade-trust-apply", kit=gpu_kit), "a GPU kit should get the overlay"
        out = dry_run("_upgrade-trust-apply", "NUM_AVAILABLE_GPUS=0", kit=gpu_kit)
        assert ".gpu.yml" not in out, f"the CPU-only override did not drop the GPU overlay:\n{out}"

    def test_up_trust_is_still_the_first_install_verb(self):
        """The guard would be meaningless if up-trust had quietly stopped seeding and resetting."""
        out = dry_run("up-trust")
        assert "ensure-seeded" in out, out
        assert "up-xnat" in out, out


GOVERNED_KIT = KIT + "ACCESS_POLICY_FILE=./governance.SCR.toml\n"
FLOWER_GOVERNED_KIT = GOVERNED_KIT.replace("FL_BACKEND=nvflare", "FL_BACKEND=flower")
# Also on the command line: CI runs this suite from `make ... FL_BACKEND=nvflare`, whose MAKEFLAGS
# the scratch make inherits, and a command-line variable beats the kit file's.
ON_FLOWER = "FL_BACKEND=flower"


class Governance(unittest.TestCase):
    """check-governance / reload-governance and the remote-daemon refusal (FLIP#1259)."""

    def test_check_governance_runs_both_checkers_without_syncing_a_project(self):
        """`uv run` in a service directory installed nvflare/pandas/fastapi onto the trust host to
        run stdlib-only modules. Both halves now run as files on a bare interpreter."""
        out = dry_run("check-governance", kit=GOVERNED_KIT)
        assert out.count("--no-project") == 2, out
        assert "data-access-api/scripts/check_governance.py" in out, out
        assert "flip-utils/flip/nvflare/site_policy.py --check --fl-backend nvflare" in out, out
        assert "cd ../flip-utils" not in out, out
        assert "cd data-access-api" not in out, out

    def test_check_governance_forwards_every_site_privacy_name(self):
        """Only the three known names were forwarded, so a misspelt one was never seen."""
        kit = GOVERNED_KIT + "FL_SITE_PRIVACY_PERCENTIL=5\n"
        out = dry_run("check-governance", kit=kit)
        assert 'FL_SITE_PRIVACY_PERCENTIL="5"' in out, out

    def test_check_governance_tells_the_renderer_the_backend(self):
        out = dry_run("check-governance", ON_FLOWER, kit=FLOWER_GOVERNED_KIT)
        assert "--fl-backend flower" in out, out

    def test_reload_governance_on_nvflare_re_extracts_for_the_clients(self):
        out = dry_run("reload-governance", kit=GOVERNED_KIT)
        assert "fl-governance-init" in out, out
        assert "--force-recreate --pull never" in out, out
        assert "[governance]" in out, out  # waits for the startup line
        assert_data_safe(out, "reload-governance")

    def test_reload_governance_on_flower_leaves_the_clients_alone(self):
        """Nothing on Flower reads the document, so recreating its clients only killed their jobs."""
        out = dry_run("reload-governance", ON_FLOWER, kit=FLOWER_GOVERNED_KIT)
        assert "fl-governance-init" not in out, out
        assert 'services="data-access-api"' in out, out
        assert 'if [ -n "" ]' in out, out  # the client branch is compiled out

    def test_reload_governance_neither_pulls_nor_prepares_data_dirs(self):
        """A policy edit must not swap the image (dev pull_policy: always) or touch data dirs."""
        out = dry_run("reload-governance", kit=GOVERNED_KIT)
        assert 'mkdir -p "$base/net-1"' not in out, out
        assert " pull" not in out.replace("--pull never", ""), out

    def test_up_fl_clients_kit_runs_the_extract_first(self):
        """--no-deps skips dependencies, so the init must be named or the clients start on a stale extract."""
        out = dry_run("up-fl-clients-kit", kit=GOVERNED_KIT)
        assert "fl-governance-init fl-client-net-1 fl-client-net-2" in out, out

    def test_a_governance_document_is_refused_over_a_remote_daemon(self):
        """Compose resolves the document on this machine and the trust host would mount a path it
        does not have (EC2 over DOCKER_CONTEXT=flip-trust)."""
        for target in ("upgrade-trust", "up-trust-ec2"):
            result = run_target(target, "YES=1", kit=GOVERNED_KIT, env={"DOCKER_HOST": "ssh://flip-trust"})
            assert result.returncode != 0, (target, result.stdout)
            assert "remote daemon (ssh://flip-trust)" in result.stdout, (target, result.stdout, result.stderr)
        # reload-governance validates first (its prerequisite needs the real checkers), so its
        # refusal is read off the recipe instead.
        assert "points at a remote daemon" in dry_run("reload-governance", kit=GOVERNED_KIT)

    def test_the_current_context_counts_when_docker_context_is_unset(self):
        """`docker context use flip-trust` leaves no DOCKER_CONTEXT in the environment."""
        with tempfile.TemporaryDirectory() as stub_dir:
            stub = Path(stub_dir) / "docker"
            stub.write_text('#!/bin/sh\ncase "$*" in "context inspect"*) echo ssh://flip-trust;; *) exit 0;; esac\n')
            stub.chmod(0o755)
            env = {"PATH": f"{stub_dir}:{os.environ['PATH']}", "DOCKER_HOST": "", "DOCKER_CONTEXT": ""}
            result = run_target("upgrade-trust", "YES=1", kit=GOVERNED_KIT, env=env)
        assert result.returncode != 0, result.stdout
        assert "remote daemon (ssh://flip-trust)" in result.stdout, result.stdout + result.stderr

    def test_a_local_tcp_daemon_is_not_remote(self):
        result = run_target("upgrade-trust", "YES=1", kit=GOVERNED_KIT, env={"DOCKER_HOST": "tcp://localhost:2375"})
        assert "remote daemon" not in result.stdout, result.stdout

    def test_check_governance_validates_the_kits_filter_without_a_document(self):
        """A misspelt FL_SITE_PRIVACY_* name is dropped silently by compose, so it must be caught here
        even when the trust has no document."""
        out = dry_run("check-governance", kit=KIT + "FL_SITE_PRIVACY_PERCENTIL=5\n")
        assert "site_policy.py --check --fl-backend nvflare" in out, out
        assert 'FL_SITE_PRIVACY_PERCENTIL="5"' in out, out

    def test_reload_governance_refuses_an_fl_image_that_cannot_extract_before_recreating(self):
        out = dry_run("reload-governance", kit=GOVERNED_KIT)
        preflight = out.index("site_policy --help")
        recreate = out.index("--force-recreate")
        assert preflight < recreate, out

    def test_a_remote_daemon_without_a_document_is_not_refused(self):
        out = run_target("upgrade-trust", "YES=1", dry=True, env={"DOCKER_HOST": "ssh://flip-trust"})
        assert out.returncode == 0, out.stdout + out.stderr


if __name__ == "__main__":
    main()
