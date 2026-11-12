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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        """The EC2 root's upgrade passes NUM_AVAILABLE_GPUS=0: it must beat a template kit's 1 (FLIP#1204).

        The EC2 node is GPU-less; with the kit's value the apply phase adds the GPU overlay and the
        recreated fl-client fails with "could not select device driver nvidia".
        """
        gpu_kit = kit_with("NUM_AVAILABLE_GPUS=0", "NUM_AVAILABLE_GPUS=1")
        assert ".gpu.yml" in dry_run("_upgrade-trust-apply", kit=gpu_kit), "a GPU kit should get the overlay"
        out = dry_run("_upgrade-trust-apply", "NUM_AVAILABLE_GPUS=0", kit=gpu_kit)
        assert ".gpu.yml" not in out, f"the CPU-only override did not drop the GPU overlay:\n{out}"

    def test_apply_phase_prepares_the_membership_store_before_compose_does(self):
        """A site upgrading into the membership store (FLIP#857) has no directory for it; compose would create it
        root-owned and the non-root data-access-api would disable the store. The production default is absolute and
        prepared by a container on the docker host, so it also works over a remote DOCKER_CONTEXT (EC2)."""
        out = dry_run("_upgrade-trust-apply")
        assert 'dir="/opt/flip/cohort-snapshots/trust-9"; owner="1000:1000"' in out, out
        assert out.index('dir="/opt/flip/cohort-snapshots/trust-9"') < out.index(" up -d"), out

    def test_compose_mounts_the_directory_the_makefile_prepared(self):
        """One resolved path: the Makefile's default and the compose default can no longer disagree."""
        out = dry_run("_upgrade-trust-apply")
        assert 'COHORT_SNAPSHOT_STORAGE_DIR="/opt/flip/cohort-snapshots/trust-9"' in out, out
        kit = KIT + "COHORT_SNAPSHOT_STORAGE_DIR=/srv/flip/members\n"  # pragma: allowlist secret
        out = dry_run("_upgrade-trust-apply", kit=kit)
        assert 'dir="/srv/flip/members"' in out, out
        assert 'COHORT_SNAPSHOT_STORAGE_DIR="/srv/flip/members"' in out, out

    def test_the_prepared_store_image_is_pinned(self):
        """An unpinned alpine is a moving target and a surprise pull on a host behind a firewall."""
        out = dry_run("_upgrade-trust-apply")
        assert "alpine:3.20" in out, out
        assert " alpine " not in out.replace("alpine:3.20", ""), out

    def test_a_local_deployed_store_is_checked_not_escalated(self):
        """Only a remote DOCKER_CONTEXT prepares the store through a container; locally a non-root operator
        gets the check and the prepare-host-dirs remedy (FLIP#1723)."""
        out = dry_run("_upgrade-trust-apply")
        assert '[ -n "$DOCKER_CONTEXT" ]' in out, out
        assert "alpine:3.20 chown" not in out, out
        assert "prepare-host-dirs KIT=SCR HUB=prod" in out, out

    def test_the_store_owner_follows_the_kits_flip_uid(self):
        """data-access-api runs as the kit's FLIP_UID when set, so that is who must own the store (FLIP#1731)."""
        out = dry_run("_upgrade-trust-apply", kit=KIT + "FLIP_UID=4242\nFLIP_GID=4243\n")
        assert 'owner="4242:4243"' in out, out
        assert 'owner="1000:1000"' not in out, out

    def test_apply_phase_refuses_a_flip_uid_the_data_dirs_do_not_carry(self):
        """FLIP_UID is a first-install choice: turned on, changed or cleared afterwards, the recreated Orthanc and
        APIs would own none of their files. The apply phase checks the data dirs' owner before pulling anything
        (FLIP#1731): all three under FLIP_UID, and the Orthanc dir (999 on the image-uid route) either way."""
        out = dry_run("_upgrade-trust-apply", kit=KIT + "FLIP_UID=4242\nFLIP_GID=4243\n")
        assert '[ "$owner" = "4242" ]' in out, out
        assert '"/nonexistent/omop"' in out, out
        assert out.index("FLIP_UID is a first-install choice") < out.index(" pull"), out
        assert out.index("which writes the XNAT backup") < out.index(" pull"), "the backup dir is checked first too"
        assert out.index("could not create the XNAT data dirs") < out.index(" pull"), "and XNAT's mount sources made"
        unset = dry_run("_upgrade-trust-apply")
        assert '[ "$owner" = "999" ]' in unset, unset
        assert '"/nonexistent/omop"' not in unset, unset
        with tempfile.TemporaryDirectory() as td:
            for d in ("omop", "pacs", "images/net-1"):
                Path(td, d).mkdir(parents=True)
            kit = KIT.replace("/nonexistent/omop", f"{td}/omop").replace("/nonexistent/orthanc", f"{td}/pacs")
            kit = kit.replace("/nonexistent/images", f"{td}/images")
            assert kit.count(td) == 3, kit
            other_uid = f"FLIP_UID={os.getuid() + 1}\nFLIP_GID={os.getgid()}\n"
            changed = run_target("_upgrade-trust-apply", kit=kit + other_uid)
            cleared = run_target("_upgrade-trust-apply", kit=kit)  # installed as this user, FLIP_UID since removed
            remote = run_target("_upgrade-trust-apply", kit=kit, env={"DOCKER_CONTEXT": "ec2-node"})
        assert changed.returncode != 0, changed.stdout
        assert f"{td}/omop is owned by uid {os.getuid()}" in changed.stdout, changed.stdout
        assert " pull" not in changed.stdout, "refused before anything was pulled"
        assert cleared.returncode != 0, cleared.stdout
        assert f"leaves FLIP_UID unset (the image uids), but {td}/pacs is owned by uid {os.getuid()}" in cleared.stdout
        assert " pull" not in cleared.stdout, "refused before anything was pulled"
        assert "FLIP_UID is a first-install choice" not in remote.stdout, "a remote host's dirs are not here"

    def test_up_trust_is_still_the_first_install_verb(self):
        """The guard would be meaningless if up-trust had quietly stopped seeding and resetting."""
        out = dry_run("up-trust")
        assert "ensure-seeded" in out, out
        assert "up-xnat" in out, out


class SampleDataOff(unittest.TestCase):
    """TRUST_DATA_SEED=false: a real trust's first install fetches, loads and marks nothing (FLIP#1687)."""

    KIT_OFF = KIT + "TRUST_DATA_SEED=false\n"
    SEED_STEPS = ("seed-omop", "seed-orthanc", ".seeded", "pg_isready")

    def test_a_first_install_loads_nothing_even_after_a_data_version_bump(self):
        for extra in ((), ("HF_TRUST_DATA_REVISION=main",)):
            out = dry_run("up-trust", *extra, kit=self.KIT_OFF)
            assert "Sample data is off for SCR" in out, out
            for step in self.SEED_STEPS:
                assert step not in out, f"{step!r} with the sample data off:\n{out}"

    def test_unset_or_true_still_seeds(self):
        for kit in (KIT, KIT + "TRUST_DATA_SEED=true\n"):
            out = dry_run("ensure-seeded", kit=kit)
            assert "seed-omop" in out, out
            assert "seed-orthanc" in out, out

    def test_any_other_value_is_refused_rather_than_read_as_true(self):
        for value in ("no", "False"):
            result = run_target("ensure-seeded", kit=KIT + f"TRUST_DATA_SEED={value}\n")
            assert result.returncode != 0, result.stdout
            assert "TRUST_DATA_SEED must be true or false" in result.stdout, result.stdout + result.stderr

    def test_up_trust_refuses_a_bad_value_before_any_container_starts(self):
        out = dry_run("up-trust", kit=KIT + "TRUST_DATA_SEED=no\n")
        assert out.index("TRUST_DATA_SEED must be true or false") < out.index(" up -d"), out

    def test_quotes_and_a_trailing_comment_read_as_sync_kit_reads_them(self):
        for line in ('TRUST_DATA_SEED="false"', "TRUST_DATA_SEED='false'", "TRUST_DATA_SEED=false  # real trust"):
            out = dry_run("ensure-seeded", kit=KIT + line + "\n")
            assert "Sample data is off for SCR" in out, f"{line}:\n{out}"

    def test_the_explicit_seed_verbs_refuse_a_trust_with_the_sample_data_off_unless_forced(self):
        for target in ("seed", "seed-omop", "seed-orthanc", "unseed-omop", "unseed-orthanc"):
            result = run_target(target, kit=self.KIT_OFF)
            assert result.returncode != 0, f"{target} ran with the sample data off:\n{result.stdout}"
            assert "has TRUST_DATA_SEED=false" in result.stdout, result.stdout + result.stderr
            assert "TRUST_INDEX" not in result.stdout, result.stdout
        out = dry_run("seed", "FORCE_SEED=1", kit=self.KIT_OFF)
        assert "seed-omop TRUST_INDEX" in out, out
        assert "seed-orthanc TRUST_INDEX" in out, out

    def test_the_seed_verbs_refuse_what_they_cannot_read_as_true_or_false(self):
        """A mistyped value, or a kit file that is not there, must not read as "sample data on"."""
        for target in ("seed-omop", "seed-orthanc", "unseed-omop", "unseed-orthanc"):
            mistyped = run_target(target, kit=KIT + "TRUST_DATA_SEED=False\n")
            assert mistyped.returncode != 0, f"{target} ran on TRUST_DATA_SEED=False:\n{mistyped.stdout}"
            assert "TRUST_DATA_SEED must be true or false" in mistyped.stdout, mistyped.stdout + mistyped.stderr
            no_kit = run_target(target, "KIT=NOPE")
            assert no_kit.returncode != 0, f"{target} ran without a kit file:\n{no_kit.stdout}"
            assert "not found" in no_kit.stdout, no_kit.stdout + no_kit.stderr
            for result in (mistyped, no_kit):
                assert "TRUST_INDEX" not in result.stdout, result.stdout

    def test_a_plain_force_from_an_outer_make_does_not_open_the_guard(self):
        """FORCE=1 belongs to upgrade-onprem-trust and release-instance, and make hands it to every sub-make."""
        result = run_target("outer", "FORCE=1", kit=self.KIT_OFF, probe="outer:\n\t$(MAKE) seed-omop\n")
        assert result.returncode != 0, result.stdout
        assert "FORCE_SEED=1 runs it anyway" in result.stdout, result.stdout + result.stderr
        assert "seed-omop TRUST_INDEX" not in result.stdout, result.stdout

    def test_a_dry_run_passes_only_where_it_writes_nothing(self):
        """seed-orthanc / unseed-orthanc honour DRY_RUN=1; seed-omop / unseed-omop would ignore it and write."""
        for target in ("seed-orthanc", "unseed-orthanc"):
            result = run_target(target, "DRY_RUN=1", kit=self.KIT_OFF)
            assert "has TRUST_DATA_SEED=false" not in result.stdout, f"{target}:\n{result.stdout}"
            assert f"{target} TRUST_INDEX" in result.stdout, f"{target} never ran:\n{result.stdout}{result.stderr}"
        for target in ("seed-omop", "unseed-omop"):
            result = run_target(target, "DRY_RUN=1", kit=self.KIT_OFF)
            assert result.returncode != 0, f"{target} ran:\n{result.stdout}"
            assert "has TRUST_DATA_SEED=false" in result.stdout, f"{target}:\n{result.stdout}"

    def test_the_guard_needs_the_kit_file_not_a_complete_one(self):
        """A kit still waiting on the hub's values can seed its own stores, as before the guard (needs only KIT)."""
        kit = KIT + "SOMETHING=<run-make-sync-trust-kit>\n"
        result = run_target("guard", kit=kit, probe="guard:\n\t$(refuse_seed_when_off)\n\t@echo GUARD-PASSED\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "GUARD-PASSED" in result.stdout, result.stdout


MONAI_KIT = KIT.replace("NUM_AVAILABLE_GPUS=0", "NUM_AVAILABLE_GPUS=1")


class MonaiLabelArchive(unittest.TestCase):
    """up-xnat's xnat-reset recreates the archive under a running monailabel, whose mount keeps the deleted one."""

    def test_up_trust_checks_the_fresh_archive_then_recreates_monailabel_onto_it(self):
        out = dry_run("up-trust", "MONAI_LABEL=true", kit=MONAI_KIT)
        reset = out.index("xnat-reset")
        check = out.index('[ ! -d "/nonexistent/xnat/xnat-data/archive" ]')
        recreate = out.index("up -d --force-recreate --no-deps monailabel")
        assert reset < check < recreate, out

    def test_a_trust_without_monai_label_recreates_nothing(self):
        assert "--force-recreate" not in dry_run("up-trust")


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
        assert "data-access-api/data_access_api/scripts/check_governance.py" in out, out
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
        assert "fl-governance-init $clients" in out, out

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


class DevelopmentFlRegistry(unittest.TestCase):
    """A direct `make -C trust …` in development resolves the FL registry like `make up` (FLIP#1384).

    A dev kit leaves its Hub-shared block commented out and inherits it from the hub's
    .env.development, which normally sets DOCKER_REGISTRY but not DOCKER_FL_REGISTRY. A bare
    `export DOCKER_FL_REGISTRY` ahead of fl_backend.mk defines it as empty, so its
    `?= $(DOCKER_REGISTRY)` never applies and the FL client image loses its registry.
    """

    HUB_ENV = "DOCKER_REGISTRY=ghcr.io/londonaicentre/\nDOCKER_TAG=stag\nDOCKER_FL_TAG=stag\nFL_BACKEND=nvflare\n"
    DEV_KIT = KIT.split("# ── Hub-shared")[0] + "# ── Hub-shared (managed) ──\n# DOCKER_FL_REGISTRY=\n"

    def fl_registry(self, *extra: str, hub_env: str = HUB_ENV) -> str:
        """DOCKER_FL_REGISTRY as trust/Makefile exports it to its recipes (what compose reads)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                "deploy/env_mode.mk",
                "deploy/fl_backend.mk",
                "deploy/instance.mk",
                "deploy/portable.mk",
                "trust/Makefile",
            ):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text((Path(__file__).resolve().parents[2] / rel).read_text())
            (root / ".env.development").write_text(hub_env)
            (root / "trust" / ".env.SCR.development").write_text(self.DEV_KIT)
            (root / "probe.mk").write_text("fl-registry: ; @echo [$$DOCKER_FL_REGISTRY]\n")
            # The caller's DOCKER_*/HUB/MAKEFLAGS would pre-empt the scratch env files.
            dropped = {"PROD", "HUB", "MAKEFLAGS"}
            env = {k: v for k, v in os.environ.items() if not k.startswith("DOCKER_") and k not in dropped}
            out = subprocess.run(
                [
                    "make",
                    "-s",
                    "-C",
                    str(root / "trust"),
                    # Read after the Makefile, like --eval, which make 3.81 lacks.
                    "-f",
                    "Makefile",
                    "-f",
                    str(root / "probe.mk"),
                    "fl-registry",
                    "KIT=SCR",
                    *extra,
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            assert out.returncode == 0, out.stdout + out.stderr
            return out.stdout.strip().splitlines()[-1]

    def test_fl_registry_defaults_to_the_hub_registry(self):
        assert self.fl_registry() == "[ghcr.io/londonaicentre/]"

    def test_a_hub_env_fl_registry_wins_over_the_default(self):
        assert self.fl_registry(hub_env=self.HUB_ENV + "DOCKER_FL_REGISTRY=localhost:5000/\n") == "[localhost:5000/]"

    def test_an_explicit_empty_fl_registry_still_selects_local_images(self):
        assert self.fl_registry("DOCKER_FL_REGISTRY=") == "[]"


class ReconvertNifti(unittest.TestCase):
    """reconvert-nifti reports unless LAUNCH is set, and hands each option to the command (FLIP#1456 G3)."""

    COMMAND = "python -m imaging_api.scripts.reconvert_nifti"

    def test_by_default_it_only_reports(self):
        out = dry_run("reconvert-nifti")
        assert self.COMMAND in out, out
        for flag in ("--launch", "--project", "--wait", "--max-attempts", "--relaunch-unfinished"):
            assert flag not in out, f"{flag} without being asked:\n{out}"
        assert_data_safe(out, "reconvert-nifti")

    def test_each_option_reaches_the_command(self):
        out = dry_run(
            "reconvert-nifti", "PROJECT=P1 P2", "LAUNCH=1", "WAIT=5", "MAX_ATTEMPTS=2", "RELAUNCH_UNFINISHED=1"
        )
        expected = f"{self.COMMAND} --project P1 --project P2 --launch --wait 5 --max-attempts 2 --relaunch-unfinished"
        assert expected in " ".join(out.replace("\\\n", " ").split()), out

    def test_the_seeding_projects_list_does_not_narrow_it(self):
        """PROJECTS is the datasets to seed (default cxr_project), not XNAT projects."""
        out = dry_run("reconvert-nifti", "PROJECTS=cxr_project prostate_project")
        assert "--project" not in out, out


@unittest.skipUnless(shutil.which("docker"), "docker CLI not installed")
class FlClientList(unittest.TestCase):
    """up/down-fl-clients-kit act on the fl-clients the composed stack defines (FLIP#1677).

    Development runs one per FL net, a deployed trust only net-1: a hardcoded net-2 made both
    targets fail on every deployed Compose trust. Renders the real compose files (config only).
    """

    PROBE = 'fl-clients: ; @$(resolve_fl_clients); echo "[$$clients]"\n'

    def clients(self, hub: str, backend: str, gpus: str = "0") -> list[str]:
        kit = KIT.replace("FL_BACKEND=nvflare", f"FL_BACKEND={backend}").replace(
            "NUM_AVAILABLE_GPUS=0", f"NUM_AVAILABLE_GPUS={gpus}"
        )
        result = run_target("fl-clients", hub=hub, kit=kit, probe=self.PROBE)
        assert result.returncode == 0, result.stdout + result.stderr
        return sorted(result.stdout.strip().splitlines()[-1].strip("[ ]").split())

    def test_development_runs_a_client_per_net_and_production_one(self):
        for backend in ("nvflare", "flower"):
            for gpus in ("0", "1"):
                with self.subTest(backend=backend, gpus=gpus):
                    assert self.clients("", backend, gpus) == ["fl-client-net-1", "fl-client-net-2"]
                    assert self.clients("prod", backend, gpus) == ["fl-client-net-1"]

    def test_no_client_in_the_config_fails_instead_of_acting_on_every_service(self):
        with tempfile.TemporaryDirectory() as stub_dir:
            stub = Path(stub_dir) / "docker"
            stub.write_text("#!/bin/sh\necho trust-api\n")
            stub.chmod(0o755)
            result = run_target("fl-clients", probe=self.PROBE, env={"PATH": f"{stub_dir}:{os.environ['PATH']}"})
        assert result.returncode != 0, result.stdout
        assert "No fl-client-net-N service" in result.stdout, result.stdout

    def test_the_targets_take_the_list_from_the_config(self):
        for target in ("up-fl-clients-kit", "down-fl-clients-kit"):
            out = dry_run(target)
            assert "config --services" in out, out
            assert "fl-client-net-2" not in out.replace('"$base/net-2"', ""), out


class DeployedPull(unittest.TestCase):
    def test_a_deployed_trust_without_a_kit_registry_still_pulls_fresh(self):
        """Kits carry no registry since FLIP#1480; the FL client's `--pull always` must not go with it."""
        # A calling make (this suite's own runner) exports an empty DOCKER_FL_REGISTRY, which would decide it.
        env = {k: v for k, v in os.environ.items() if not k.startswith("DOCKER_") and k != "MAKEFLAGS"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert "up -d --pull always" in dry_run("up-trust")


if __name__ == "__main__":
    main()
