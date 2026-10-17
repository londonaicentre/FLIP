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
"""The root Makefile's site upgrade verb is gated and data-safe (FLIP#1204), the hub-only
bring-up creates every network it needs (FLIP#1344), and the targets that drive the hub compose
project refuse one that another checkout created (FLIP#1227).

`make upgrade-onprem-trust` must run the readiness checklist and then reach the trust-level
upgrade, never the first-install steps (`ensure-seeded` re-seeds OMOP / Orthanc, `xnat-reset`
wipes the XNAT archive and database).

Usage:
    python3 tests/test_makefile.py
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_dry_run import REPO, assert_data_safe, dry_run, main  # noqa: E402

HUB_ONLY_SERVICES = {"flip-db", "flip-api"}  # what `make central-hub` starts (flip-api/Makefile `up`)


def hub_only_networks() -> set[str]:
    """The unprefixed names of the external networks HUB_ONLY_SERVICES join in the dev compose.

    Read from the file rather than listed here, so a network added to either service fails the
    test instead of breaking `make central-hub` on a fresh host again. Line-based: the root tests
    are stdlib-only (no PyYAML on the runner).
    """
    keys: set[str] = set()
    names: dict[str, str] = {}
    section = entry = None
    in_networks = False
    for line in (REPO / "deploy" / "compose.development.yml").read_text().splitlines():
        if top := re.match(r"^([\w-]+):", line):
            section, entry = top[1], None
        elif key := re.match(r"^  ([\w-]+):\s*$", line):
            entry, in_networks = key[1], False
        elif section == "services" and entry in HUB_ONLY_SERVICES:
            if re.match(r"^    networks:\s*$", line):
                in_networks = True
            elif in_networks and (item := re.match(r"^      - ([\w-]+)", line)):
                keys.add(item[1])
            elif re.match(r"^    \S", line):
                in_networks = False
        elif section == "networks" and (name := re.match(r"^    name: \$\{FLIP_INSTANCE:[^}]*\}(\S+)", line)):
            names[entry] = name[1]
    return {names[key] for key in keys}


class UpgradeOnpremTrust(unittest.TestCase):
    def test_upgrade_onprem_trust_is_gated_and_data_safe(self):
        out = dry_run("upgrade-onprem-trust", "TAG=v0.6.0", "YES=1", subdir=".")
        assert "onboard_onprem_trust.py" in out, out  # the readiness checklist gates it
        assert "site_upgrade.py plan" in out, out
        assert "--tag v0.6.0" in out, out
        assert "--yes" in out, out
        gate = next(line for line in out.splitlines() if "onboard_onprem_trust.py" in line)
        assert "--gate" in gate, gate  # the gate must not suggest up-onprem-trust
        assert "--kit-file" in gate, gate
        assert ".env.SCR.prod" in gate, gate
        assert "_upgrade-trust-apply" in out, out
        assert_data_safe(out, "upgrade-onprem-trust")


class CentralHubNetworks(unittest.TestCase):
    def test_the_compose_file_is_still_parsed(self):
        assert "deploy_central-hub-network" in hub_only_networks(), hub_only_networks()

    def test_create_networks_centralhub_creates_every_hub_only_network(self):
        """`make central-hub` on a fresh host must not need the trust Makefile's `create-networks`."""
        for instance in ("", "kc"):
            out = dry_run("create-networks-centralhub", f"FLIP_INSTANCE={instance}", subdir=".")
            for network in hub_only_networks():
                name = f"{instance}-{network}" if instance else network
                assert f"docker network create --driver bridge {name}" in out, f"{name}:\n{out}"

    def test_remove_networks_removes_them(self):
        out = dry_run("remove-networks", "FLIP_INSTANCE=kc", subdir=".")
        for network in hub_only_networks():
            assert f"kc-{network}" in out, f"kc-{network}:\n{out}"


ENVIRONMENTS = (
    (None, "prod"),
    ("prod", "prod"),
    ("stag", "stag"),
)
#: Kit names before FLIP#1479, by the HUB that still finds them (env_mode.mk's kit_file).
OLD_KIT_NAMES = (
    ("prod", ".env.SITE.production"),
    ("prod", ".env.SITE.lza-prod"),
    ("stag", ".env.SITE.lza-stag"),
)
#: The smallest hub env file the AWS Makefile's parse-time guards accept (inert values).
AWS_STUB_ENV = {
    "AWS_PROFILE": "probe",
    "PROD_AWS_PROFILE": "probe",
    "FLARE_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
    "MANAGE_DNS": "false",
}
AWS = "deploy/providers/AWS"
KIT = (
    "FL_BACKEND=nvflare\n"  # Synthetic kit values; no credentials.
    "FL_KIT_SLOT=Trust_9\n"
    "FL_KIT_SLOT_NUMBER=9\n"
    "NUM_AVAILABLE_GPUS=0\n"
    "XNAT_AETITLE=XNAT\n"
)


class MakeKitSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for rel in (
            "Makefile",
            "deploy/env_mode.mk",
            "deploy/fl_backend.mk",
            "deploy/instance.mk",
            "deploy/providers/AWS/Makefile",
            "trust/Makefile",
            "trust/deploy/helm/Makefile",
            "trust/xnat/Makefile",
            "trust/xnat/.env",
        ):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, target)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "with open(os.environ['ARGS_FILE'], 'w') as out: json.dump(sys.argv[1:], out)\n"
            "with open(os.environ['HUB_FILE'], 'w') as out: out.write(os.environ.get('HUB', 'unset'))\n"
            "sys.exit(bool(os.environ.get('UV_FAIL_ON')) and any(os.environ['UV_FAIL_ON'] in a for a in sys.argv))\n"
        )
        uv.chmod(0o755)
        self.args_file = self.root / "args.json"
        self.hub_file = self.root / "hub.txt"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PROD", "HUB", "KIT", "KIT_FILE", "MAKEFLAGS"}
        }
        self.env.update(
            PATH=f"{bin_dir}:{os.environ['PATH']}", ARGS_FILE=str(self.args_file), HUB_FILE=str(self.hub_file)
        )

    def make(self, target, *extra, dry=False, subdir="."):
        command = ["make", "--no-print-directory", *(["-n"] if dry else []), "-C", str(self.root / subdir), target]
        return subprocess.run(command + list(extra), env=self.env, capture_output=True, text=True, timeout=30)

    def run_make(self, target, hub=None, *extra, dry=False, subdir="."):
        hub_arg = [] if hub is None else [f"HUB={hub}"]
        result = self.make(target, "KIT=SITE", *hub_arg, *extra, dry=dry, subdir=subdir)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def write_kits(self, *names):
        for name in names:
            (self.root / "trust" / name).write_text(KIT)

    def check_selected(self, expected, hub=None, *extra, subdir="."):
        output = self.run_make("onboard-onprem-trust", hub, *extra, subdir=subdir)
        args = json.loads(self.args_file.read_text())
        assert args[:4] == ["run", "--no-config", "../scripts/onboard_onprem_trust.py", "SITE"], args
        assert Path(args[args.index("--kit-file") + 1]) == self.root / "trust" / expected, args
        assert self.hub_file.read_text() == (hub or ("" if subdir == "trust" else "prod"))
        return output

    def test_a_kit_under_two_names_is_refused(self):
        """Only one of them holds the live kit; choosing either silently could deploy the stale one."""
        for hub, names in (
            (None, (".env.SITE", ".env.SITE.prod")),
            ("prod", (".env.SITE.production", ".env.SITE.lza-prod")),
            ("stag", (".env.SITE.lza-stag", ".env.SITE.stag")),
        ):
            with self.subTest(hub=hub, names=names):
                self.write_kits(*names)
                result = self.make("onboard-onprem-trust", "KIT=SITE", *([f"HUB={hub}"] if hub else []))
                assert result.returncode != 0, result.stdout
                assert all(name in result.stderr for name in names), result.stderr
                assert f"keep the live one as .env.SITE.{hub or 'prod'}" in result.stderr, result.stderr
                for name in names:
                    (self.root / "trust" / name).unlink()

    def test_a_bare_kit_is_found_under_prod_only(self):
        """The bare .env.<CODE> was only ever a prod kit, so stag never falls back to it."""
        self.write_kits(".env.SITE")
        for hub, suffix in ENVIRONMENTS:
            with self.subTest(hub=hub):
                self.check_selected(".env.SITE" if suffix == "prod" else f".env.SITE.{suffix}", hub)

    def test_old_kit_names_are_still_found_with_a_rename_hint(self):
        """A live site's next upgrade must not break on a kit named before FLIP#1479."""
        probe = ".DEFAULT_GOAL := __probe\ninclude Makefile\n__probe:\n\t@printf '%s' '$(KIT_FILE)'\n"
        for hub, filename in OLD_KIT_NAMES:
            with self.subTest(filename=filename):
                self.write_kits(filename)
                output = self.check_selected(filename, hub)
                hint = f"trust/{filename} is an old kit name — rename (mv) it to trust/.env.SITE.{hub}"
                assert output.count(hint) == 1, output  # once, though several makes resolve it
                xnat = subprocess.run(
                    ["make", "-s", "-f", "-", "__probe", "KIT=SITE", f"HUB={hub}", "TRUST_NUM=9"],
                    input=probe,
                    cwd=self.root / "trust/xnat",
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert xnat.stdout == f"../{filename}", xnat.stdout + xnat.stderr
                (self.root / "trust" / filename).unlink()

    def test_new_trust_refuses_beside_an_old_named_kit(self):
        self.write_kits(".env.SITE.production")
        result = self.make("new-trust", "TRUST_CODE=SITE", "TRUST_NAME=Site", "HUB=prod")
        assert result.returncode != 0, result.stdout
        assert "trust/.env.SITE.production already exists under an old kit name" in result.stdout, result.stdout
        assert not self.args_file.exists(), "new_trust.py must not run"
        result = self.make("new-trust", "TRUST_CODE=OTHER", "TRUST_NAME=Other", "HUB=prod")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "scripts/new_trust.py" in self.args_file.read_text()

    def test_sweeps_take_current_names_and_name_the_old_kits_they_skip(self):
        """Only KIT= finds an old name: a sweep also took trust/.env.<word> (.env.local) for a kit."""
        self.write_kits(".env.SITE.production", ".env.OK.prod", ".env.local")
        for target, subdir, loop in (
            ("down-fl-clients", "trust", "for kit in OK;"),
            ("sync-trust-kits", ".", "for code in OK;"),
        ):
            with self.subTest(target=target):
                result = self.make(target, "HUB=prod", dry=True, subdir=subdir)
                assert loop in result.stdout, result.stdout + result.stderr
                assert "rename (mv) to include them: trust/.env.SITE.production → .env.SITE.prod" in result.stdout

    def test_a_sweep_fails_when_an_earlier_kit_fails(self):
        """A bare `for` loop exits with the last kit's status, so an earlier kit's failure was lost."""
        (self.root / "trust/.env.OTHER.prod").write_text(KIT.replace("SLOT_NUMBER=9", "SLOT_NUMBER=8"))
        (self.root / "trust/.env.SITE.prod").write_text(KIT)
        docker = self.root / "bin/docker"
        docker.write_text('#!/bin/sh\ncase "$*" in compose*trust8*) exit 1;; esac\n')
        docker.chmod(0o755)
        result = self.make("down-fl-clients", "HUB=prod", subdir="trust")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "-p trust9 rm" in result.stdout, result.stdout  # the later kit still ran

    def test_sync_trust_kits_fails_when_a_kit_fails(self):
        self.write_kits(".env.SITE.prod", ".env.OTHER.prod")
        self.env["UV_FAIL_ON"] = "OTHER."  # the first kit fails, the last succeeds
        result = self.make("sync-trust-kits", "HUB=prod")
        assert result.returncode != 0, result.stdout + result.stderr

    def test_the_aws_and_helm_targets_find_an_old_named_kit(self):
        self.write_kits(".env.SITE.lza-prod")
        env_file = self.root / "hub.env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in AWS_STUB_ENV.items()))
        kit = f"{self.root}/trust/.env.SITE.lza-prod"
        for target, subdir, expected in (
            ("register-trusts", "deploy/providers/AWS", f'KIT_FILES="{kit}"'),
            ("sync-kit", "trust/deploy/helm", f"--kit-file {kit}"),
            ("upgrade-trust-k8s", "trust/deploy/helm", f"plan --kit-file {kit}"),
        ):
            with self.subTest(target=target):
                args = ("HUB=prod", f"MAIN_ENV_FILE={env_file}", "KIT=SITE")
                result = self.make(target, *args, dry=True, subdir=subdir)
                assert expected in result.stdout, result.stdout + result.stderr
                assert "trust/.env.SITE.lza-prod is an old kit name" in result.stdout, result.stdout
        # Without KIT= it is a sweep: current names only.
        result = self.make("register-trusts", "HUB=prod", f"MAIN_ENV_FILE={env_file}", dry=True, subdir=AWS)
        assert 'KIT_FILES=""' in result.stdout, result.stdout + result.stderr
        assert "trust/.env.SITE.lza-prod → .env.SITE.prod" in result.stdout, result.stdout

    def test_a_kit_file_given_outright_is_not_looked_up(self):
        """Two names for one kit stop the lookup, which a KIT_FILE= makes unnecessary."""
        self.write_kits(".env.SITE.prod", ".env.SITE.production", "operator-kit")
        env_file = self.root / "hub.env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in AWS_STUB_ENV.items()))
        kit = self.root / "trust/operator-kit"
        self.check_selected("operator-kit", "prod", f"KIT_FILE={kit}")
        for target, subdir, expected in (
            ("register-trusts", AWS, f'KIT_FILES="{kit}"'),
            ("sync-kit", "trust/deploy/helm", f"--kit-file {kit}"),
            ("down-xnat", "trust/xnat", "xnat"),
        ):
            with self.subTest(target=target):
                args = ("KIT=SITE", "HUB=prod", f"KIT_FILE={kit}", f"MAIN_ENV_FILE={env_file}")
                result = self.make(target, *args, dry=True, subdir=subdir)
                assert result.returncode == 0, result.stdout + result.stderr
                assert expected in result.stdout, result.stdout

    def test_an_occupied_slot_is_its_own_trust_under_any_name_its_kit_answers_to(self):
        """A stack started before the FLIP#1479 rename still carries the old kit name in its label."""
        self.write_kits(".env.SITE.prod")
        docker = self.root / "bin/docker"
        docker.write_text('#!/bin/sh\ncase "$*" in *" -q") echo occupied;; *environment_file*) echo "$OWNER";; esac\n')
        docker.chmod(0o755)
        for owner, allowed in (
            ("/srv/flip/trust/.env.SITE.production", True),
            ("/srv/flip/trust/.env.SITE.lza-prod", True),
            ("/srv/flip/trust/.env.SITE", True),
            ("/srv/flip/trust/.env.SITE.lza-stag", False),  # the staging hub's kit
            ("/srv/flip/trust/.env.OTHER.production", False),
        ):
            with self.subTest(owner=owner):
                self.env["OWNER"] = owner
                result = self.make("down-fl-clients-kit", "KIT=SITE", "HUB=prod", subdir="trust")
                assert (result.returncode == 0) == allowed, result.stdout + result.stderr

    def test_trust_commands_ignore_an_old_named_hub_env_file(self):
        """They read only the kit, so a hub admin's .env.lza-prod must not stop them; a hub command still refuses."""
        self.write_kits(".env.SITE.prod")
        (self.root / ".env.lza-prod").write_text("")
        self.check_selected(".env.SITE.prod", "prod")
        self.run_make("upgrade-onprem-trust", "prod", dry=True)
        for target, subdir in (("down-fl-clients-kit", "trust"), ("upgrade-trust-k8s", "trust/deploy/helm")):
            with self.subTest(target=target):
                result = self.make(target, "KIT=SITE", "HUB=prod", dry=True, subdir=subdir)
                assert result.returncode == 0, result.stdout + result.stderr
        result = self.make("sync-trust-kits", "HUB=prod", dry=True)
        assert result.returncode != 0, result.stdout
        assert ".env.lza-prod is the hub env file's pre-FLIP#1479 name" in result.stderr, result.stderr

    def test_a_staging_hub_env_file_must_name_the_lza_vpc(self):
        """A .env.stag left from the retired self-contained staging account must not reach a kit."""
        self.write_kits(".env.SITE.stag")
        env_file = self.root / ".env.stag"
        env_file.write_text("CENTRAL_HUB_API_URL=https://retired.example/api\n")
        result = self.make("sync-trust-kits", "HUB=stag", dry=True)
        assert result.returncode != 0, result.stdout
        assert ".env.stag sets no LZA_VPC_NAME" in result.stderr, result.stderr
        env_file.write_text("LZA_VPC_NAME=probe-vpc\n")
        result = self.make("sync-trust-kits", "HUB=stag", dry=True)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_missing_kit_is_still_passed_to_the_checklist_for_diagnostics(self):
        self.check_selected(".env.SITE.prod")

    def test_a_development_kit_cannot_override_the_onprem_prod_default(self):
        (self.root / "trust/.env.SITE.development").write_text(KIT)
        (self.root / "trust/.env.SITE.prod").write_text(KIT)
        self.check_selected(".env.SITE.prod")
        self.check_selected(".env.SITE.development", subdir="trust")

    def test_explicit_kit_file_override_reaches_the_checklist(self):
        (self.root / "trust/.env.SITE.prod").write_text(KIT)
        (self.root / "trust/operator-kit").write_text(KIT)
        self.check_selected("operator-kit", None, "KIT_FILE=operator-kit")

    def test_the_checklist_passes_shell_metacharacters_as_part_of_the_path(self):
        for filename in ('operator kits/kit\'s $HOME `echo unsafe` "file"', "kit$(echo unsafe)`echo unsafe`\"file'"):
            selected = self.root / "trust" / filename
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_text(KIT)
            for path in (filename, str(selected)):
                with self.subTest(path=path):
                    # Dollars are escaped for Make's variable expansion, then passed literally to the shell.
                    self.check_selected(filename, None, f"KIT_FILE={path.replace('$', '$$')}")

    def test_copied_ready_and_governance_assignments_reach_make_unchanged(self):
        selected = self.root / 'trust/operator kits/kit\'s $HOME `echo unsafe` "file"'
        selected.parent.mkdir()
        selected.write_text(KIT)
        code = """
import contextlib, io, json, sys
from pathlib import Path
from unittest import mock
import onboard_onprem_trust as mod
selected = Path(sys.argv[1])
sys.argv = ['checklist', 'SITE', '--kit-file', str(selected)]
out = io.StringIO()
with contextlib.redirect_stdout(out), \\
     mock.patch.object(mod, 'run_checks', return_value=[mod.Check('x', mod.Status.PASS, 'mocked')]):
    try:
        mod.main()
    except SystemExit as error:
        assert error.code == 0
ready = next(line.strip() for line in out.getvalue().splitlines() if 'sudo -E make up-onprem-trust' in line)
with mock.patch.object(mod.subprocess, 'run', return_value=mod.subprocess.CompletedProcess([], 1, 'bad policy', '')):
    check = mod.check_governance_document(
        {'ACCESS_POLICY_FILE': 'governance.toml'}, True, 'SITE', selected.parents[2], selected)
hint = next(hint for hint in check.hints if 'check-governance' in hint)
print(json.dumps({'ready': ready, 'governance': hint[1:hint.rfind('`')]}))
"""
        for hub in ("prod", "stag"):
            generated = subprocess.run(
                [sys.executable, "-c", code, str(selected)],
                env={**self.env, "PYTHONPATH": str(REPO / "scripts"), "HUB": hub},
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert generated.returncode == 0, generated.stdout + generated.stderr
            commands = json.loads(generated.stdout)
            for name, command in commands.items():
                with self.subTest(hub=hub, command=name):
                    # Reuse the printed assignments, but run only the read-only checklist through stub uv.
                    assignments = shlex.split(command)[4:]
                    self.run_make(
                        "onboard-onprem-trust",
                        None,
                        *assignments,
                        subdir="trust" if name == "governance" else ".",
                    )
                    args = json.loads(self.args_file.read_text())
                    assert Path(args[args.index("--kit-file") + 1]) == selected, args
                    assert self.hub_file.read_text() == hub

    def test_an_occupied_slot_allows_only_its_own_kit_for_absolute_and_relative_paths(self):
        selected = self.root / "trust/.env.SITE.prod"
        selected.write_text(KIT)
        calls_file = self.root / "docker-calls.jsonl"
        docker = self.root / "bin/docker"
        docker.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['DOCKER_CALLS'], 'a') as out: out.write(json.dumps(args) + '\\n')\n"
            "if args[0] == 'ps':\n"
            "    if '-q' in args: print('occupied-container')\n"
            "    elif 'environment_file' in args[-1]: print(os.environ['SLOT_OWNER'])\n"
            "    elif 'working_dir' in args[-1]: print('/previous/checkout/trust')\n"
            "elif args[0] != 'compose': sys.exit('Unexpected Docker command')\n"
        )
        docker.chmod(0o755)
        self.env["DOCKER_CALLS"] = str(calls_file)
        makefile = self.root / "trust/Makefile"
        with makefile.open("a") as out:
            out.write("\n_test-slot-guard:\n\t$(check_slot_not_taken)\n\t@echo slot guard passed\n")
        overrides = (
            self.root / "trust/operator kits" / selected.name,
            self.root / 'trust/operator\'s $HOME `echo unsafe` "kits"' / selected.name,
            self.root / 'trust/operator kits/kit\'s $HOME `echo unsafe` "file"',
        )
        for override in overrides:
            override.parent.mkdir(parents=True, exist_ok=True)
            override.write_text(KIT)
        for requested in (selected.name, str(selected), *(str(override) for override in overrides)):
            # The guard itself can accept arbitrary filenames; other deployment recipes still assume CODE kits.
            target = "down-fl-clients-kit" if requested in (selected.name, str(selected)) else "_test-slot-guard"
            for owner, allowed in (
                (f"/previous/checkout/{Path(requested).name}", True),
                ("/other/.env.OTHER.prod", False),
                ("", False),
            ):
                with self.subTest(requested=requested, owner=owner):
                    self.env["SLOT_OWNER"] = owner
                    calls_file.write_text("")
                    result = subprocess.run(
                        [
                            "make",
                            "--no-print-directory",
                            "-C",
                            str(self.root / "trust"),
                            target,
                            "KIT=SITE",
                            "HUB=prod",
                            "FL_KIT_SLOT_NUMBER=9",
                            f"KIT_FILE={requested.replace('$', '$$')}",
                        ],
                        env=self.env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
                    assert (result.returncode == 0) == allowed, result.stdout + result.stderr
                    if target == "down-fl-clients-kit":
                        assert any(call[0] == "compose" for call in calls) == allowed, calls
                    else:
                        assert ("slot guard passed" in result.stdout) == allowed, result.stdout
                        assert all(call[0] == "ps" for call in calls), calls
                    if not allowed:
                        assert "trust//" not in result.stdout, result.stdout
                        hint_path = requested if Path(requested).is_absolute() else f"trust/{requested}"
                        assert f"in {hint_path}" in result.stdout, result.stdout
                        expected = "already in use" if owner else "cannot be identified"
                        assert expected in result.stdout, result.stdout

    def test_missing_kit_diagnostics_show_absolute_or_repo_relative_paths(self):
        for requested in ("missing-kit", str(self.root / "trust/missing-kit")):
            result = subprocess.run(
                [
                    "make",
                    "-C",
                    str(self.root / "trust"),
                    "up-trust-ec2",
                    "KIT=SITE",
                    "HUB=prod",
                    f"KIT_FILE={requested}",
                ],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            expected = requested if Path(requested).is_absolute() else f"trust/{requested}"
            assert result.returncode != 0, result.stdout
            assert f"Kit file {expected} not found" in result.stdout, result.stdout
            assert f"Copy {expected}.example to {expected}" in result.stdout, result.stdout
            assert "trust//" not in result.stdout, result.stdout

    def test_both_gate_callers_and_the_deployment_use_the_same_file(self):
        for filename in (".env.SITE.prod", ".env.SITE.production", ".env.SITE"):
            with self.subTest(filename=filename):
                kit_file = self.root / "trust" / filename
                kit_file.write_text(KIT)
                down = self.run_make("down-onprem-trust", dry=True)
                assert "down-trust KIT=SITE HUB=prod" in down, down
                assert f"--env-file {filename} " in down, down
                for target in ("up-onprem-trust", "upgrade-onprem-trust"):
                    output = self.run_make(target, dry=True)
                    gate = next(line for line in output.splitlines() if "onboard_onprem_trust.py" in line)
                    args = shlex.split(gate)
                    assert "--gate" in args, gate
                    assert Path(args[args.index("--kit-file") + 1]) == kit_file, gate
                    if target == "upgrade-onprem-trust":
                        plan = next(line for line in output.splitlines() if "site_upgrade.py plan" in line)
                        plan_args = shlex.split(plan)
                        assert plan_args[plan_args.index("--kit-file") + 1] == filename, plan
                    else:
                        assert f"Using kit file trust/{filename}" in output, output
                kit_file.unlink()


# Every root target that acts on the hub compose project: the ones whose recipe runs compose on
# it (see compose_targets), plus central-hub and restart-no-trust, which hand it to
# flip-api/Makefile. `restart` is `down up`, so the guard reaches it through both.
OWNER_GUARDED_TARGETS = {
    "up",
    "up-no-trust",
    "central-hub",
    "down",
    "clean",
    "clean-object-store",
    "restart-fl",
    "restart-no-trust",
    "ui",
    "ui-off",
    "debug",
    "debug-off",
    "debug-all",
    "debug-off-all",
    "up-pgadmin",
    "reset-keycloak",
    "register-trust",
}


COMPOSE_COMMAND = re.compile(r"\b(DEBUG_OVERRIDE_COMPOSE_COMMAND|DOCKER_COMMAND)\b")


def compose_targets(makefile: str) -> set[str]:
    """The targets whose recipe runs `$(DOCKER_COMMAND)` or its debug-override twin."""
    targets: set[str] = set()
    current = None
    for line in makefile.splitlines():
        if header := re.match(r"^([\w.-]+):(?![:=])", line):
            current = header[1]
        elif current and line.startswith("\t") and COMPOSE_COMMAND.search(line):
            targets.add(current)
    return targets


class HubProjectOwnerGuard(unittest.TestCase):
    """Targets that drive the hub compose project refuse one another checkout created (FLIP#1227)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root = base / "checkout"
        for rel in ("Makefile", "deploy/env_mode.mk", "deploy/fl_backend.mk", "deploy/instance.mk"):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, target)
        self.link = base / "link"
        self.link.symlink_to(self.root)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        # Lists one working_dir label per container, and only for a filter on project $PROJECT.
        docker.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "project = 'label=com.docker.compose.project=' + os.environ['PROJECT']\n"
            "if sys.argv[1:3] == ['ps', '-a'] and project in sys.argv:\n"
            "    for working_dir in json.loads(os.environ['WORKING_DIRS']): print(working_dir)\n"
        )
        docker.chmod(0o755)
        excluded = {"MAKEFLAGS", "PROD", "HUB", "FLIP_INSTANCE", "ALLOW_FOREIGN_PROJECT", "FORCE"}
        self.env = {key: value for key, value in os.environ.items() if key not in excluded}
        self.env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", PROJECT="deploy")

    def guard(self, *working_dirs, extra=()):
        self.env["WORKING_DIRS"] = json.dumps([str(working_dir) for working_dir in working_dirs])
        return subprocess.run(
            ["make", "--no-print-directory", "-C", str(self.root), "_check-compose-project-owner", *extra],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_an_empty_project_or_one_this_checkout_created_passes(self):
        for working_dirs in ((), (self.root / "deploy",), (self.root / "deploy", self.link / "deploy")):
            with self.subTest(working_dirs=working_dirs):
                result = self.guard(*working_dirs)
                assert result.returncode == 0, result.stdout + result.stderr

    def test_another_checkout_is_refused_and_named(self):
        result = self.guard(self.root / "deploy", "/home/someone/FLIP/deploy")
        assert result.returncode != 0, result.stdout
        assert "Compose project 'deploy' has containers created from another checkout" in result.stdout, result.stdout
        assert "/home/someone/FLIP/deploy" in result.stdout, result.stdout
        assert str(self.root / "deploy") not in result.stdout, result.stdout
        assert "FLIP_INSTANCE=<name>" in result.stdout, result.stdout

    def test_an_unlabelled_container_is_refused(self):
        result = self.guard("")
        assert result.returncode != 0, result.stdout
        assert "<no working_dir label>" in result.stdout, result.stdout

    def test_the_project_follows_flip_instance(self):
        self.env["PROJECT"] = "kc-deploy"
        assert self.guard("/elsewhere/deploy", extra=("FLIP_INSTANCE=kc",)).returncode != 0
        assert self.guard("/elsewhere/deploy").returncode == 0  # project `deploy` is not kc-deploy

    def test_only_allow_foreign_project_1_skips_the_check(self):
        # Not FORCE: `up` hands it on to generate-internal-service-key, which rotates the key.
        cases = (("ALLOW_FOREIGN_PROJECT=1", True), ("ALLOW_FOREIGN_PROJECT=true", False), ("FORCE=1", False))
        for extra, allowed in cases:
            with self.subTest(extra=extra):
                result = self.guard("/elsewhere/deploy", extra=(extra,))
                assert (result.returncode == 0) == allowed, result.stdout + result.stderr

    def test_it_is_the_first_prerequisite_of_every_target_that_drives_the_project(self):
        makefile = (REPO / "Makefile").read_text()
        unguarded = compose_targets(makefile) - {"build"} - OWNER_GUARDED_TARGETS  # build only builds images
        assert not unguarded, f"add these to OWNER_GUARDED_TARGETS and gate them: {sorted(unguarded)}"
        for target in sorted(OWNER_GUARDED_TARGETS):
            with self.subTest(target=target):
                assert re.search(rf"^{re.escape(target)}: _check-compose-project-owner(\s|$)", makefile, re.M), target


if __name__ == "__main__":
    main()
