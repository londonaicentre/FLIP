#!/usr/bin/env python3
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
"""Tests for scripts/release/ — the deterministic release pipeline (FLIP#1608).

Only the parts that need no Docker, GPU, network or GitHub: env and kit generation, the provenance checks
against fake ``docker inspect`` output, the gate runner's bookkeeping, the *Release checks* rendering and the
version-bump rules.

Usage:
    python3 scripts/tests/test_release_scripts.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))

import gates  # noqa: E402
import instance  # noqa: E402
import main_check  # noqa: E402
import prepare  # noqa: E402
import record  # noqa: E402
import verify  # noqa: E402
from release_lib import ReleaseError, default_instance, documented_value, placeholders, read_env  # noqa: E402

HUB_EXAMPLE = (ROOT / ".env.development.example").read_text()
KIT_EXAMPLES = sorted((ROOT / "trust").glob(".env.*.development.example"))
# Kit ports that live inside the trust's own network: the same in every instance.
TRUST_CONTAINER_PORTS = {"PACS_QR_PORT", "DATA_ACCESS_API_PORT", "IMAGING_API_PORT", "TRUST_API_PORT"}
# Stand-ins for what instance._mint returns, recognisable in any output that leaks them.
MINTED = {
    "POSTGRES_PASSWORD": "pg-SECRET",  # pragma: allowlist secret
    "AES_KEY_BASE64": "aes-SECRET",  # pragma: allowlist secret
    "ADMIN_USER_PASSWORD": "admin-SECRET",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY": "isk-SECRET",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "hash",  # pragma: allowlist secret
}


def refused(fn, *args) -> ReleaseError:
    """Call ``fn`` and return the ReleaseError it raises (fail if it does not)."""
    try:
        fn(*args)
    except ReleaseError as e:
        return e
    raise AssertionError("ReleaseError not raised")


def numeric_ports(text: str) -> set[str]:
    return {m[1] for m in re.finditer(r"^(?:# ?)?([A-Z0-9_]+_PORT)=(\d+)", text, re.M)}


class Instance(unittest.TestCase):
    def test_default_instance_name(self):
        assert default_instance("v0.13.0") == "rel0130"
        refused(default_instance, "v0.13.0-rc.1")

    def test_every_hub_host_port_is_moved(self):
        # A port added to the example without being listed would leave the instance on the default stack's.
        assert numeric_ports(HUB_EXAMPLE) <= set(instance.HUB_HOST_PORTS), numeric_ports(HUB_EXAMPLE)
        text = instance.hub_env(HUB_EXAMPLE, "rel0130", 10000, MINTED)
        for key in instance.HUB_HOST_PORTS:
            assert read_env_text(text)[key] == str(int(documented_value(HUB_EXAMPLE, key)) + 10000), key

    def test_hub_env_targets_the_instance(self):
        env = read_env_text(instance.hub_env(HUB_EXAMPLE, "rel0130", 10000, MINTED))
        assert env["FLIP_INSTANCE"] == "rel0130"
        assert env["CENTRAL_HUB_API_URL"] == env["FLIP_E2E_BASE_URL"] == "http://localhost:18080/api"
        assert env["KEYCLOAK_PUBLIC_URL"] == "http://localhost:18180"
        assert env["DOCKER_TAG"] == env["DOCKER_FL_TAG"] == "rel0130"
        assert env["DOCKER_FL_REGISTRY"] == ""
        assert not placeholders(instance.hub_env(HUB_EXAMPLE, "rel0130", 10000, MINTED))

    def test_minted_secrets_are_real(self):
        env = read_env_text(instance.hub_env(HUB_EXAMPLE, "x", 10000))
        import base64
        import hashlib

        assert len(base64.b64decode(env["AES_KEY_BASE64"])) == 32
        assert env["INTERNAL_SERVICE_KEY_HASH"] == hashlib.sha256(env["INTERNAL_SERVICE_KEY"].encode()).hexdigest()

    def test_unknown_placeholder_is_refused(self):
        refused(instance.hub_env, HUB_EXAMPLE + "\nNEW_SECRET=<fill-me>\n", "x", 10000, MINTED)

    def test_port_out_of_range_is_refused(self):
        refused(instance.hub_env, HUB_EXAMPLE, "x", 60000, MINTED)

    def test_trust_kits_move_host_ports_only(self):
        assert KIT_EXAMPLES
        for example in KIT_EXAMPLES:
            text = example.read_text()
            assert numeric_ports(text) <= set(instance.TRUST_HOST_PORTS) | TRUST_CONTAINER_PORTS, example
            kit = read_env_text(instance.trust_kit(text, "rel0130", 10000))
            for key in instance.TRUST_HOST_PORTS:
                assert kit[key] == str(int(documented_value(text, key)) + 10000), (example, key)
            for key in TRUST_CONTAINER_PORTS:
                assert kit[key] == documented_value(text, key), (example, key)
            assert kit["OMOP_DB_TAG"] == "rel0130"

    def test_write_files_refuses_to_overwrite_and_hides_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            shutil.copy(ROOT / ".env.development.example", repo)
            (repo / "trust").mkdir()
            for example in KIT_EXAMPLES:
                shutil.copy(example, repo / "trust")
            out = io.StringIO()
            with mock.patch.object(instance, "_mint", return_value=MINTED), contextlib.redirect_stdout(out):
                written = instance.write_files("rel0130", 10000, False, repo)
            assert len(written) == 1 + len(KIT_EXAMPLES)
            assert all(p.stat().st_mode & 0o777 == 0o600 for p in written)
            assert not any(s in out.getvalue() for s in MINTED.values())
            refused(instance.write_files, "rel0130", 10000, False, repo)
            with contextlib.redirect_stdout(io.StringIO()):
                instance.write_files("rel0130", 10000, True, repo)

    def test_only_kits_another_stack_wrote_are_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "trust").mkdir()
            for example in KIT_EXAMPLES:
                shutil.copy(example, repo / "trust")
            kits = list(instance.kit_examples(repo))
            assert instance.foreign_kits("rel0130", repo) == []  # nothing written yet
            kits[0].write_text(instance.trust_kit(KIT_EXAMPLES[0].read_text(), "rel0130", 10000))
            assert instance.foreign_kits("rel0130", repo) == []  # a re-run finds its own kit
            assert instance.foreign_kits("rel0140", repo) == [kits[0]]  # another release's instance
            kits[-1].write_text(KIT_EXAMPLES[-1].read_text())  # a developer's kit: OMOP_DB_TAG unset
            assert kits[-1] in instance.foreign_kits("rel0130", repo)

    def test_gates_refuse_a_checkout_with_a_developers_kits(self):
        err = io.StringIO()
        with (
            mock.patch.object(gates, "assert_clean"),
            mock.patch.object(gates, "assert_at_pr_head", return_value="a" * 40),
            mock.patch.object(gates.instance_mod, "foreign_kits", return_value=[ROOT / "trust/.env.GSTT.development"]),
            mock.patch.object(gates, "run") as run,
            contextlib.redirect_stderr(err),
        ):
            assert gates.main(["--tag", "v0.13.0"]) == 1
        run.assert_not_called()
        assert "trust/.env.GSTT.development" in err.getvalue()
        assert ".env.rel0130.development" in err.getvalue()
        assert "dedicated release worktree" in err.getvalue()


def read_env_text(text: str) -> dict[str, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write(text)
    try:
        return read_env(Path(f.name))
    finally:
        Path(f.name).unlink()


def container(name: str, image: str, workdir: str | None, service: str = "", stack: bool = False) -> dict:
    labels = {"com.docker.compose.service": service} if service else {}
    if workdir is not None:
        labels["com.docker.compose.project.working_dir"] = workdir
    if stack:
        labels["com.docker.stack.namespace"] = name.split("_")[0]
    return {"Name": f"/{name}", "Config": {"Image": image, "Labels": labels}, "State": {"Running": True}}


class RunStep(unittest.TestCase):
    """A build or download step gets one retry for a transient failure; any other step fails at once."""

    def flaky(self, tmp: Path, target: str, failures: int) -> list[str]:
        script = (
            "import pathlib, sys; c = pathlib.Path(sys.argv[1]); n = int(c.read_text() or 0) + 1; c.write_text(str(n));"
            f" sys.exit(1 if n <= {failures} else 0)"
        )
        counter = tmp / "count"
        counter.write_text("")
        return [sys.executable, "-c", script, str(counter), target]

    def run_step(self, step: list[str]) -> tuple[int, bool]:
        """How many times the step ran, and whether run_step raised."""
        raised = False
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                instance.run_step(step, self.tmp, {})
            except instance.subprocess.CalledProcessError:
                raised = True
        return int((self.tmp / "count").read_text()), raised

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_a_transient_build_failure_is_retried_once(self):
        assert self.run_step(self.flaky(self.tmp, "build-fl", failures=1)) == (2, False)

    def test_a_build_failing_twice_fails(self):
        assert self.run_step(self.flaky(self.tmp, "build-fl", failures=2)) == (2, True)

    def test_other_steps_are_not_retried(self):
        assert self.run_step(self.flaky(self.tmp, "up", failures=1)) == (1, True)

    def test_a_failure_keeps_its_exit_code(self):
        code = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                instance.run_step([sys.executable, "-c", "raise SystemExit(7)", "up"], self.tmp, {})
        except instance.subprocess.CalledProcessError as e:
            code = e.returncode
        assert code == 7


class Verify(unittest.TestCase):
    REPO = Path("/w/flip")

    def test_this_checkout_at_the_instance_tag_passes(self):
        found = [
            container("rel0130-deploy-flip-api-1", "ghcr.io/londonaicentre/flip-api:rel0130", "/w/flip/deploy"),
            container("rel0130-deploy-flip-ui-1", "rel0130-deploy-flip-ui", "/w/flip/deploy"),
            container("rel0130-deploy-flip-db-1", "postgres:17", "/w/flip/deploy"),
            container("rel0130-trust1-fl-client-net-1-1", "flare-fl-client:rel0130", "/w/flip/trust"),
            container(
                "rel0130-xnat1_xnat-web.1.x", "ghcr.io/londonaicentre/xnat-web:rel0130@sha256:ab", None, stack=True
            ),
        ]
        assert verify.check_containers(found, "rel0130", self.REPO) == []

    def test_pulled_or_foreign_containers_fail(self):
        found = [
            container("rel0130-trust1-omop-db-1", "ghcr.io/londonaicentre/omop-db:stag", "/w/flip/trust"),
            container("rel0130-deploy-fl-server-net-1-1", "flare-fl-server:dev", "/w/flip/deploy"),
            container("rel0130-deploy-flip-api-1", "ghcr.io/londonaicentre/flip-api:rel0130", "/w/other/deploy"),
            container("rel0130-stray", "busybox", None),
        ]
        problems = verify.check_containers(found, "rel0130", self.REPO)
        assert len(problems) == 4, problems
        assert verify.check_containers([], "rel0130", self.REPO)

    def test_services_must_be_replicated_at_the_instance_tag(self):
        ok = {"Name": "rel0130-xnat1_xnat-web", "Replicas": "1/1", "Image": "ghcr.io/londonaicentre/xnat-web:rel0130"}
        proxy = {"Name": "rel0130-xnat1_proxy", "Replicas": "1/1", "Image": "tecnativa/docker-socket-proxy:v0.4.2"}
        assert verify.check_services([ok, proxy], "rel0130") == []
        stuck = {**ok, "Replicas": "0/1"}
        pulled = {**ok, "Image": "ghcr.io/londonaicentre/xnat-web:stag"}
        assert len(verify.check_services([stuck, pulled], "rel0130")) == 2


class Gates(unittest.TestCase):
    def test_tutorial_metrics(self):
        lines = [
            (0.0, "🚀 Running FLARE tutorial xray_classification"),
            (60.0, "🚀 Running FLARE tutorial 3d_spleen_segmentation"),
            (100.0, "❌ tutorial 3d_spleen_segmentation failed"),
        ]
        m = gates.tutorial_metrics(lines)
        assert m["total"] == 2
        assert m["passed"] == 1
        assert m["tutorials"][0] == {"name": "xray_classification", "passed": True, "seconds": 60}
        assert m["tutorials"][1]["passed"] is False
        assert m["tutorials"][1]["seconds"] == 40
        flower = gates.tutorial_metrics([(0.0, "🚀 Running Flower tutorial fl-tutorials/flower/xray_classification")])
        assert flower["tutorials"][0]["name"] == "xray_classification"

    def test_smoke_metrics(self):
        lines = [
            (0.0, "   project_id   = a02be724"),
            (0.0, "   model_id     = 440df334"),
            (0.0, "   final_status = RESULTS_UPLOADED"),
        ]
        assert gates.smoke_metrics(lines) == {
            "project_id": "a02be724",
            "model_id": "440df334",
            "final_status": "RESULTS_UPLOADED",
        }

    def test_smoke_metrics_keep_the_project_of_a_smoke_that_failed(self):
        lines = [(0.0, "  ✅ project_id=1ab83312-5130"), (1.0, "❌ Smoke failed: Model entered ERROR state")]
        assert gates.smoke_metrics(lines) == {"project_id": "1ab83312-5130"}

    def test_flower_smoke_creates_its_own_project_without_a_flare_one(self):
        flower = gates.build_gates("v0.0.1", "rel0001", lambda: "")["smoke-flower"].steps[0]
        assert not any("--project-id" in arg for arg in flower())
        reused = gates.build_gates("v0.0.1", "rel0001", lambda: "abc")["smoke-flower"].steps[0]
        assert "EXTRA_ARGS=--project-id abc" in reused()

    def test_container_logs_are_saved_when_a_stack_gate_fails(self):
        cases = (({"smoke-nvflare": 1}, ["smoke-nvflare"]), ({"unit": 1}, []), ({"integration": 1}, []), ({}, []))
        for codes, gates_with_logs in cases:
            with mock.patch.object(gates, "collect_logs", return_value=[]) as collect:
                _, tmp = self.run_fake(codes, keep_going=True, instance="rel0001")
            assert [c.args[1].name for c in collect.call_args_list] == gates_with_logs, codes
            assert all(c.args[1].parent == tmp / "container-logs" for c in collect.call_args_list)

    def test_quiet_gates_run_before_the_instance_comes_up(self):
        order = list(gates.ORDER)
        quiet = ("unit", "integration", "tutorials-nvflare", "tutorials-flower")
        assert all(order.index(g) < order.index("instance") for g in quiet)
        assert all(order.index(g) > order.index("instance") for g in gates.STACK_GATES if g != "instance")

    def test_the_quickest_gates_run_first(self):
        assert list(gates.ORDER)[:2] == ["unit", "integration"]

    def run_fake(self, codes: dict[str, int], keep_going: bool = False, instance: str = "") -> tuple[dict, Path]:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        fake = {n: gates.Gate(n, [[sys.executable, "-c", f"raise SystemExit({codes.get(n, 0)})"]]) for n in gates.ORDER}
        with contextlib.redirect_stdout(io.StringIO()):
            results = gates.run(fake, list(gates.ORDER), tmp, {"sha": "abc"}, {}, keep_going, instance)
        return results, tmp

    def test_a_failure_stops_the_run_but_not_teardown(self):
        results, tmp = self.run_fake({"unit": 3})
        assert results["unit"]["status"] == "failed"
        assert results["unit"]["exit_code"] == 3
        assert results["smoke-nvflare"]["status"] == "not-run"
        assert results["teardown"]["status"] == "passed"
        written = json.loads((tmp / "smoke-flower.json").read_text())
        assert written == {"sha": "abc", "gate": "smoke-flower", "status": "not-run"}

    def test_continue_runs_every_gate(self):
        results, _ = self.run_fake({"unit": 1}, keep_going=True)
        assert all(r["status"] == "passed" for n, r in results.items() if n != "unit")

    def test_own_simulator_pids_only_matches_this_checkout(self):
        repo = Path("/home/u/flip-wt-a")
        ps = [
            "11 /home/u/flip-wt-a/flip-utils/.venv/bin/python /home/u/flip-wt-a/flip-utils/.venv/bin/flower-superlink",
            "12 /home/u/flip-wt-ab/flip-utils/.venv/bin/flower-superlink --insecure",  # a sibling with a longer name
            "13 /home/u/flip-wt-b/flip-utils/.venv/bin/flwr-simulation --run-once",  # another checkout's
            "14 /usr/local/bin/flower-superlink --insecure",  # a dev stack's container, visible on the host
            "15 /home/u/flip-wt-a/flip-utils/.venv/bin/python -m pytest",  # ours, but not a simulator
            "16 /home/u/flip-wt-a/flip-utils/.venv/bin/flwr-serverapp --insecure",
        ]
        assert gates.own_simulator_pids(ps, repo) == [11, 16]

    def test_cleanup_runs_after_a_failed_step(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        gate = gates.Gate("x", [[sys.executable, "-c", "raise SystemExit(2)"]], cleanup=lambda: ["stopped 42"])
        with contextlib.redirect_stdout(io.StringIO()):
            result = gates.run_gate(gate, tmp, {}, {})
        assert result["status"] == "failed"
        assert "🧹 stopped 42" in (tmp / "x.log").read_text()

    def test_a_failed_teardown_names_what_remains(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        check = gates.build_gates("v0.0.1", "rel0001", lambda: "")["teardown"].steps[-1]
        ps = mock.Mock(stdout="rel0001-flip-db-1\n")
        with (
            mock.patch.object(gates.subprocess, "run", return_value=ps),
            mock.patch.object(gates.verify_mod, "services", return_value=[{"Name": "rel0001-xnat-1_xnat-web"}]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = gates.run_gate(gates.Gate("teardown", [check]), tmp, {}, {})
        assert result["status"] == "failed"
        log = (tmp / "teardown.log").read_text()
        assert "container rel0001-flip-db-1 remains" in log
        assert "service rel0001-xnat-1_xnat-web remains" in log

    def test_a_failing_check_fails_its_gate(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        gate = gates.Gate("x", [gates.Check(lambda: ["not this commit"]), [sys.executable, "-c", "print('never')"]])
        with contextlib.redirect_stdout(io.StringIO()):
            result = gates.run_gate(gate, tmp, {}, {})
        assert result["status"] == "failed"
        assert "never" not in (tmp / "x.log").read_text()


def result(gate: str, status: str = "passed", **metrics) -> dict:
    return {
        "gate": gate, "status": status, "sha": "a" * 40, "instance": "rel0130", "host": "gpu-box",
        "gpu": "NVIDIA RTX 5090", "start": "2026-10-20T21:00:00+00:00", "seconds": 90, "exit_code": 0,
        "log": f"/x/{gate}.log", "metrics": metrics,
    }  # fmt: skip


class Record(unittest.TestCase):
    def test_passing_results_tick_every_box(self):
        results = {
            "tutorials-nvflare": result("tutorials-nvflare", tutorials=[{"name": "xray", "seconds": 53}], total=1),
            "tutorials-flower": result("tutorials-flower", tutorials=[], total=5),
            "smoke-nvflare": result("smoke-nvflare", final_status="RESULTS_UPLOADED"),
            "smoke-flower": result("smoke-flower", final_status="RESULTS_UPLOADED"),
            "unit": result("unit"),
            "integration": result("integration"),
        }
        text = record.render(results)
        assert "[ ]" not in text, text
        assert "NVIDIA RTX 5090 workstation, 20 October 2026" in text
        assert "gpu-box" not in text  # the public notes never name the host
        assert "xray 53 s" in text
        assert "`aaaaaaaaa`" in text

    def test_failed_or_missing_gates_stay_unticked(self):
        text = record.render({"tutorials-nvflare": result("tutorials-nvflare", "failed")})
        assert text.count("[x]") == 1  # only host and date
        assert "FLARE: failed (exit 0, log `tutorials-nvflare.log`)" in text
        assert "Flower: not run" in text
        assert record.render({}).count("[ ]") == 5

    def test_only_the_section_is_replaced(self):
        template = record.TEMPLATE.read_text()
        new = record.replace_section(template, record.render({}))
        before, after = template.split(record.HEADING)
        assert new.startswith(before)
        assert new.endswith(after[after.index("\n## ") + 1 :])

    def test_results_from_another_commit_are_refused(self):
        results = {"a": result("a"), "b": {**result("b"), "sha": "b" * 40}}
        refused(record.check_commit, results, "a" * 40)
        record.check_commit({"a": result("a")}, "a" * 40)
        with mock.patch.object(record, "git", return_value=".github/RELEASE_NOTES_TEMPLATE.md"):
            record.check_commit({"a": result("a")}, "c" * 40)
        with mock.patch.object(record, "git", return_value="flip-api/x.py\n.github/RELEASE_NOTES_TEMPLATE.md"):
            refused(record.check_commit, {"a": result("a")}, "c" * 40)


class Prepare(unittest.TestCase):
    def test_step_and_bump(self):
        assert prepare.step("v0.12.0", "v0.13.0") == "minor"
        assert prepare.step("v0.12.0", "v0.12.1") == "patch"
        assert prepare.step("v0.12.3", "v1.0.0") == "major"
        refused(prepare.step, "v0.13.0", "v0.13.0")
        assert prepare.bump("0.9.4", "minor") == "0.10.0"
        assert prepare.bump("0.9.4", "patch") == "0.9.5"
        assert prepare.bump("0.9.4", "major") == "1.0.0"

    def test_tests_and_markdown_do_not_count(self):
        assert prepare.counts_as_change("flip-api/src/x.py", "flip-api")
        assert not prepare.counts_as_change("flip-api/tests/unit/x.py", "flip-api")
        assert not prepare.counts_as_change("trust/trust-api/README.md", "trust/trust-api")
        assert not prepare.counts_as_change("flip-api-other/x.py", "flip-api")

    def test_plan_bumps_changed_services_only(self):
        changed = ["flip-api/src/a.py", "trust/trust-api/tests/b.py", "flip-utils/flip/c.py", "flip-ui/src/d.vue"]
        versions = prepare.plan("v9.9.0", "v9.8.0", "flip-utils-v99.0.0", changed, ROOT)
        assert versions["pyproject.toml"] == "9.9.0"
        assert set(versions) == {
            "pyproject.toml",
            "flip-api/pyproject.toml",
            "flip-ui/package.json",
            prepare.FLIP_UTILS,
        }
        assert versions[prepare.FLIP_UTILS] == "99.1.0"  # strictly above the last flip-utils tag

    def test_unlabelled_prs_start_after_the_previous_tag(self):
        prs = [{"number": 1, "title": "a", "labels": []}, {"number": 2, "title": "b", "labels": [{"name": "x"}]}]
        with mock.patch.object(prepare, "_gh_json", return_value=prs) as gh:
            assert prepare.unlabelled_prs("2026-10-20T14:03:11+01:00") == prs[:1]
        assert "merged:>2026-10-20T14:03:11+01:00" in gh.call_args.args

    def test_version_files_round_trip(self):
        for rel in ("pyproject.toml", "flip-ui/package.json", prepare.FLIP_UTILS):
            path = ROOT / rel
            new = prepare.write_version(path.read_text(), path.suffix, "9.9.9")
            assert re.search(prepare.VERSION_PATTERNS[path.suffix], new, re.M)[2] == "9.9.9"
            assert len(new.splitlines()) == len(path.read_text().splitlines())
        lock = prepare.set_package_lock((ROOT / "flip-ui/package-lock.json").read_text(), "9.9.9")
        data = json.loads(lock)
        assert data["version"] == data["packages"][""]["version"] == "9.9.9"


def release_pr(**overrides) -> dict:
    pr = {
        "number": 1700, "baseRefName": "main", "headRefName": "develop", "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            {"workflowName": main_check.ORIGIN_WORKFLOW, "conclusion": "SUCCESS"},
            {"workflowName": main_check.VERSION_WORKFLOW, "conclusion": "SUCCESS"},
            {"workflowName": "CodeQL", "conclusion": "SUCCESS"},
        ],
        "comments": [{"body": f"{main_check.PREVIEW_MARKER}\n## Release notes preview"}],
    }  # fmt: skip
    return {**pr, **overrides}


class MainCheck(unittest.TestCase):
    def test_a_ready_pr_passes(self):
        assert main_check.problems(release_pr(), flip_utils_changed=True) == []

    def test_each_gate_is_required(self):
        assert main_check.problems(release_pr(mergeable="CONFLICTING"), True)
        assert main_check.problems(release_pr(headRefName="feature"), True)
        assert main_check.problems(release_pr(comments=[]), True)
        no_origin = release_pr(statusCheckRollup=release_pr()["statusCheckRollup"][1:])
        assert main_check.problems(no_origin, True)

    def test_the_version_check_is_required_only_when_flip_utils_changed(self):
        # check-version-bump.yml only triggers on flip-utils/**, so its absence is fine otherwise.
        rollup = [c for c in release_pr()["statusCheckRollup"] if c["workflowName"] != main_check.VERSION_WORKFLOW]
        assert main_check.problems(release_pr(statusCheckRollup=rollup), flip_utils_changed=False) == []
        assert main_check.problems(release_pr(statusCheckRollup=rollup), flip_utils_changed=True)
        failed = [*rollup, {"workflowName": main_check.VERSION_WORKFLOW, "conclusion": "FAILURE"}]
        assert main_check.problems(release_pr(statusCheckRollup=failed), flip_utils_changed=False)

    def test_create_command_carries_the_title_checklist_and_owed_items(self):
        import shlex

        argv = shlex.split(main_check.create_command("v0.13.0", ["Delete JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB"]))
        assert argv[:7] == ["gh", "pr", "create", "--base", "main", "--head", "develop"]
        assert argv[argv.index("--title") + 1] == "Release v0.13.0 (develop → main)"
        assert argv[argv.index("--label") + 1] == "release"  # excluded from the generated notes
        body = argv[argv.index("--body") + 1]
        assert "merge commit" in body
        assert "fl_quiesced" in body
        assert "`:v0.13.0` on all twelve images" in body
        assert "- [ ] Delete JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB" in body


if __name__ == "__main__":
    unittest.main(verbosity=1)
