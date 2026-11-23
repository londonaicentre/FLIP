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
"""Tests for the Secret-to-ConfigMap release migration (FLIP#1997).

A fake cluster holds the release records and answers the kubectl and helm calls the script makes, so
the order of writes, the refusals and the cleaning can be checked without a cluster. The kind
rehearsal (`trust/deploy/e2e/upgrade-agent/run.sh migrate`) runs the same script against a real one.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_release_driver.py"
_spec = importlib.util.spec_from_file_location("migrate_release_driver", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
subject = importlib.util.module_from_spec(_spec)
sys.modules["migrate_release_driver"] = subject  # dataclasses resolves annotations through sys.modules
_spec.loader.exec_module(subject)

REL, NS = "trust-release", "flip-trust"
SECRET_NAME = f"{REL}-flip-trust-secrets"
PASSWORD = "s3cret-omop-password"  # pragma: allowlist secret (a test fixture)

CHART_SECRET = f"""# Source: flip-trust/templates/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: {SECRET_NAME}
data:
  omop-postgres-password: {base64.b64encode(PASSWORD.encode()).decode()}
"""
SERVICE = """# Source: flip-trust/templates/trust-api.yaml
apiVersion: v1
kind: Service
metadata:
  name: trust-api
"""
STATEFULSET = """# Source: flip-trust/templates/omop-db.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: omop-db
"""


def _release(version: int, status: str = "superseded", create: bool = True) -> dict:
    secrets = {"create": True, "data": {"omop-postgres-password": PASSWORD}} if create else {"create": False}
    manifest = "---\n" + SERVICE + "---\n" + (CHART_SECRET + "---\n" if create else "") + STATEFULSET
    return {
        "name": REL,
        "version": version,
        "namespace": NS,
        "info": {"status": status, "description": f"Upgrade {version}"},
        "chart": {"metadata": {"name": "flip-trust", "version": "0.15.0", "appVersion": "v0.15.0"}},
        "config": {"secrets": secrets, "global": {"image": {"tag": "v0.15.0"}}},
        "manifest": manifest,
    }


def _encode(release: dict) -> str:
    return base64.b64encode(gzip.compress(json.dumps(release).encode())).decode()


def _labels(release: dict) -> dict:
    return {"name": REL, "owner": "helm", "status": release["info"]["status"], "version": str(release["version"])}


class FakeCluster:
    """The release records of one namespace, and the kubectl/helm calls the script makes on them."""

    def __init__(self, releases: list[dict]):
        self.secrets: dict[str, dict] = {}
        self.configmaps: dict[str, dict] = {}
        self.annotations: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.fail_on: str | None = None
        for r in releases:
            name = f"{subject.PREFIX}{REL}.v{r['version']}"
            self.secrets[name] = {
                "metadata": {"name": name, "namespace": NS, "labels": _labels(r)},
                "data": {"release": base64.b64encode(_encode(r).encode()).decode()},
            }

    def decoded(self, store: dict) -> dict[int, dict]:
        out = {}
        for name, item in store.items():
            text = item["data"]["release"]
            if store is self.secrets:
                text = base64.b64decode(text).decode()
            out[subject.revision_of(name)] = subject.decode_record(text)
        return out

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        self.calls.append(argv)
        if self.fail_on and self.fail_on in " ".join(argv):
            raise subject.Refused(f"`{' '.join(argv)}` failed: injected")
        if argv[0] == "env":
            driver = argv[1].split("=", 1)[1]
            store = self.secrets if driver == "secret" else self.configmaps
            return json.dumps(
                [
                    {
                        "revision": rev,
                        "status": r["info"]["status"],
                        "chart": "flip-trust-0.15.0",
                        "app_version": "v0.15.0",
                        "description": r["info"]["description"],
                    }
                    for rev, r in sorted(self.decoded(store).items())
                ]
            )
        assert argv[:3] == ["kubectl", "-n", NS], argv
        verb, rest = argv[3], argv[4:]
        if verb == "get":
            store = self.secrets if rest[0] == "secrets" else self.configmaps
            return json.dumps({"items": list(store.values())})
        if verb == "create":
            obj = json.loads(stdin or "")
            assert obj["kind"] == "ConfigMap"
            assert obj["metadata"]["name"] not in self.configmaps, "create must not overwrite"
            self.configmaps[obj["metadata"]["name"]] = obj
            return ""
        if verb == "annotate":
            self.annotations[rest[1]] = rest[2]
            return ""
        if verb == "delete":
            del self.secrets[rest[1]]
            return ""
        raise AssertionError(f"unexpected call {argv}")


def _cluster(fake: FakeCluster) -> object:
    return subject.Cluster(["kubectl"], ["helm"], NS, fake.run)


def _migrate(fake: FakeCluster, apply: bool = True) -> tuple[int, list[str]]:
    lines: list[str] = []
    return subject.migrate(_cluster(fake), REL, apply, say=lines.append), lines


class TestCleaning(unittest.TestCase):
    def test_the_values_lose_the_secret_data_and_point_at_the_live_secret(self):
        cleaned = subject.clean_release(_release(1), SECRET_NAME)
        assert cleaned["config"]["secrets"] == {"create": False, "existingName": SECRET_NAME}
        assert cleaned["config"]["global"] == {"image": {"tag": "v0.15.0"}}

    def test_the_manifest_loses_the_chart_secret_and_keeps_everything_else_byte_for_byte(self):
        cleaned = subject.clean_release(_release(1), SECRET_NAME)["manifest"]
        assert "kind: Secret" not in cleaned
        assert PASSWORD not in cleaned
        assert base64.b64encode(PASSWORD.encode()).decode() not in cleaned
        assert cleaned == "---\n" + SERVICE + "---\n" + STATEFULSET

    def test_a_secret_from_another_template_is_not_touched(self):
        other = "# Source: flip-trust/templates/fl-kit.yaml\napiVersion: v1\nkind: Secret\nmetadata:\n  name: kit\n"
        manifest = "---\n" + other + "---\n" + CHART_SECRET
        assert subject.clean_manifest(manifest) == "---\n" + other

    def test_the_original_revision_is_not_modified(self):
        original = _release(1)
        subject.clean_release(original, SECRET_NAME)
        assert original["config"]["secrets"]["data"] == {"omop-postgres-password": PASSWORD}

    def test_leaks_names_what_a_record_would_still_hand_over(self):
        assert subject.leaks(subject.clean_release(_release(3), SECRET_NAME)) == []
        assert len(subject.leaks(_release(3))) == 2

    def test_the_encoding_round_trips_and_is_deterministic(self):
        r = _release(2)
        assert subject.decode_record(subject.encode_record(r)) == r
        assert subject.encode_record(r) == subject.encode_record(r)


class TestSecretName(unittest.TestCase):
    def test_a_chart_made_secret_is_named_after_the_release(self):
        assert subject.secret_name_of({1: _release(1)}, REL) == SECRET_NAME

    def test_an_existing_secret_named_in_the_values_wins(self):
        r = _release(1, create=False)
        r["config"]["secrets"]["existingName"] = "flip-trust-secrets"
        assert subject.secret_name_of({1: r}, REL) == "flip-trust-secrets"

    def test_a_fullname_override_is_followed(self):
        r = _release(1)
        r["config"]["fullnameOverride"] = "site"
        assert subject.secret_name_of({1: r}, REL) == "site-secrets"


class TestMigrate(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCluster([_release(1), _release(2, status="deployed")])

    def test_the_default_is_a_plan_that_writes_nothing(self):
        rc, lines = _migrate(self.fake, apply=False)
        assert rc == 0
        assert self.fake.configmaps == {}
        assert len(self.fake.secrets) == 2
        assert self.fake.annotations == {}
        assert any("--apply" in line for line in lines)

    def test_apply_moves_every_revision_cleaned_and_removes_the_secrets(self):
        rc, _ = _migrate(self.fake)
        assert rc == 0
        assert self.fake.secrets == {}
        moved = self.fake.decoded(self.fake.configmaps)
        assert sorted(moved) == [1, 2]
        for record in moved.values():
            assert subject.leaks(record) == []
            assert PASSWORD not in json.dumps(record)
        assert moved[2]["info"]["status"] == "deployed"
        labels = self.fake.configmaps[f"{subject.PREFIX}{REL}.v2"]["metadata"]["labels"]
        assert labels == {"name": REL, "owner": "helm", "status": "deployed", "version": "2"}

    def test_the_secret_is_kept_before_anything_else_and_deleted_only_after_the_check(self):
        _migrate(self.fake)
        verbs = [c[3] if c[0] == "kubectl" else "history" for c in self.fake.calls]
        assert verbs.index("annotate") < verbs.index("create")
        assert self.fake.annotations[SECRET_NAME] == "helm.sh/resource-policy=keep"  # pragma: allowlist secret
        last_history = max(i for i, v in enumerate(verbs) if v == "history")
        assert min(i for i, v in enumerate(verbs) if v == "delete") > last_history

    def test_a_second_run_does_nothing(self):
        _migrate(self.fake)
        before = len(self.fake.calls)
        rc, lines = _migrate(self.fake)
        assert rc == 0
        assert "nothing to do" in lines[0]
        writes = [c for c in self.fake.calls[before:] if c[0] == "kubectl" and c[3] != "get"]
        assert writes == []

    def test_a_run_that_stopped_before_deleting_the_secrets_is_finished(self):
        self.fake.fail_on = "delete"
        with pytest.raises(subject.Refused):
            _migrate(self.fake)
        assert len(self.fake.configmaps) == 2
        assert len(self.fake.secrets) == 2
        self.fake.fail_on = None
        rc, lines = _migrate(self.fake)
        assert rc == 0
        assert self.fake.secrets == {}
        assert any("previous run stopped" in line for line in lines)

    def test_configmaps_written_by_something_else_are_refused(self):
        foreign = _release(2, status="deployed")
        foreign["config"]["global"]["image"]["tag"] = "v0.16.0"
        name = f"{subject.PREFIX}{REL}.v2"
        self.fake.configmaps[name] = {"metadata": {"name": name}, "data": {"release": _encode(foreign)}}
        with pytest.raises(subject.Refused) as caught:
            _migrate(self.fake)
        assert "not what this migration writes" in str(caught.value)
        assert len(self.fake.secrets) == 2

    def test_an_operation_in_flight_is_refused(self):
        fake = FakeCluster([_release(1), _release(2, status="pending-upgrade")])
        with pytest.raises(subject.Refused) as caught:
            _migrate(fake)
        assert "in flight" in str(caught.value)
        assert fake.configmaps == {}

    def test_no_release_is_refused(self):
        with pytest.raises(subject.Refused):
            _migrate(FakeCluster([]))

    def test_a_history_mismatch_leaves_the_secret_records_in_place(self):
        real_run = self.fake.run

        def lying_history(argv, stdin=None):
            out = real_run(argv, stdin)
            if argv[0] == "env" and argv[1] == "HELM_DRIVER=configmap":
                return json.dumps(json.loads(out)[:1])
            return out

        cluster = subject.Cluster(["kubectl"], ["helm"], NS, lying_history)
        with pytest.raises(subject.Refused) as caught:
            subject.migrate(cluster, REL, True, say=lambda _: None)
        assert "history differs" in str(caught.value)
        assert len(self.fake.secrets) == 2


class TestMain(unittest.TestCase):
    def test_a_refusal_exits_one_with_the_reason(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = subject.main(["--release", REL, "--namespace", NS], run=FakeCluster([]).run)
        assert rc == 1
        assert "no release named" in err.getvalue()

    def test_the_kubectl_and_helm_commands_may_carry_a_context(self):
        fake = FakeCluster([_release(1, status="deployed")])
        seen = []

        def strip_context(argv, stdin=None):
            seen.append(argv)
            if argv[0] == "kubectl":
                assert argv[1:3] == ["--context", "gstt"]
                argv = ["kubectl", *argv[3:]]
            else:
                assert argv[2:4] == ["helm", "--kube-context"]
                argv = [argv[0], argv[1], "helm", *argv[5:]]
            return fake.run(argv, stdin)

        with redirect_stdout(io.StringIO()):
            rc = subject.main(
                [
                    "--release",
                    REL,
                    "--namespace",
                    NS,
                    "--kubectl",
                    "kubectl --context gstt",
                    "--helm",
                    "helm --kube-context gstt",
                    "--apply",
                ],
                run=strip_context,
            )
        assert rc == 0
        assert fake.secrets == {}
        assert seen


if __name__ == "__main__":
    unittest.main()
