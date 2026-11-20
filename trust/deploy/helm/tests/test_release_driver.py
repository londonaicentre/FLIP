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
"""Tests for the release-driver report (FLIP#1644).

The report exists so an operator can tell, before pointing an agent at a trust, whether the agent
will be able to see the release at all. The cases that matter are the three answers plus the
half-migrated one, which is the state a real migration can leave behind.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

DRIVER_PY = Path(__file__).resolve().parents[1] / "scripts" / "release_driver.py"
_spec = importlib.util.spec_from_file_location("release_driver", DRIVER_PY)
assert _spec is not None
assert _spec.loader is not None
subject = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(subject)


def _object(name: str) -> dict:
    return {"metadata": {"name": name}}


class TestReleaseNames(unittest.TestCase):
    def test_it_reads_the_release_and_the_highest_revision(self):
        objects = [
            _object("sh.helm.release.v1.flip-trust.v1"),
            _object("sh.helm.release.v1.flip-trust.v7"),
            _object("sh.helm.release.v1.flip-trust.v3"),
        ]
        assert subject.release_names(objects, "secrets") == {"flip-trust": 7}

    def test_it_keeps_releases_apart_when_the_name_contains_a_dot(self):
        objects = [_object("sh.helm.release.v1.flip-trust-rel.v2")]
        assert subject.release_names(objects, "configmaps") == {"flip-trust-rel": 2}

    def test_it_ignores_objects_that_are_not_release_records(self):
        objects = [_object("governance-configmap"), _object("sh.helm.release.v1.broken"), _object("")]
        assert subject.release_names(objects, "secrets") == {}


class TestDescribe(unittest.TestCase):
    def test_a_configmap_record_is_the_answer_the_agent_needs(self):
        driver, why = subject.describe("flip-trust", {}, {"flip-trust": 4})
        assert driver == subject.CONFIGMAP
        assert "can upgrade" in why

    def test_a_secret_record_names_the_migration(self):
        driver, why = subject.describe("flip-trust", {"flip-trust": 4}, {})
        assert driver == subject.SECRET
        assert "migration" in why
        assert "FAILED" in why

    def test_a_half_migrated_release_is_named_as_one(self):
        """Both stores means an interrupted migration: the wrong answer is to pick one silently."""
        driver, why = subject.describe("flip-trust", {"flip-trust": 9}, {"flip-trust": 10})
        assert driver == subject.CONFIGMAP
        assert "twice" in why
        assert "9" in why
        assert "10" in why

    def test_no_record_at_all_is_its_own_answer(self):
        driver, why = subject.describe("flip-trust", {"other": 1}, {})
        assert driver == subject.NONE
        assert "no release" in why


class TestReport(unittest.TestCase):
    def _kubectl(self, secrets: list[dict], configmaps: list[dict]):
        def fake(cmd, **kwargs):
            items = secrets if "-l" in cmd and "secrets" in cmd else configmaps
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"items": items}), "")

        return fake

    def test_it_reads_both_stores_through_kubectl(self):
        secrets = [_object("sh.helm.release.v1.flip-trust.v2")]
        configmaps = [_object("sh.helm.release.v1.flip-trust.v5")]
        with mock.patch.object(subject.subprocess, "run", side_effect=self._kubectl(secrets, configmaps)):
            driver, why = subject.report("flip-trust", "flip-trust")
        assert driver == subject.CONFIGMAP
        assert "twice" in why


class TestExitCodes(unittest.TestCase):
    """The exit code is what a CI check or an operator's script branches on."""

    def _run(self, driver: str) -> int:
        with redirect_stdout(io.StringIO()):
            return subject.main(["--release", "r"], run=lambda *a: (driver, "why"))

    def test_a_configmap_record_exits_zero(self):
        assert self._run(subject.CONFIGMAP) == 0

    def test_a_secret_record_exits_one(self):
        assert self._run(subject.SECRET) == 1

    def test_no_record_exits_one(self):
        assert self._run(subject.NONE) == 1

    def test_an_unreadable_cluster_exits_two(self):
        def boom(*a):
            raise subprocess.CalledProcessError(1, "kubectl")

        with redirect_stdout(io.StringIO()), mock.patch.object(subject.sys, "stderr", io.StringIO()):
            assert subject.main([], run=boom) == 2
