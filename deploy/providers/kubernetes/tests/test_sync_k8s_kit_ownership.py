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

"""Unit tests for the Helm Secret-ownership stamping in sync_k8s_kit.py
(FLIP#595) — so a Secret this script creates can be adopted by a subsequent
`helm upgrade --install` instead of aborting the release."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "sync_k8s_kit.py"
_spec = importlib.util.spec_from_file_location("sync_k8s_kit", _SCRIPT)
sync_k8s_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_k8s_kit)


def test_derive_release_name_strips_chart_suffix():
    assert sync_k8s_kit.derive_release_name("trust-release-flip-trust-secrets") == "trust-release"
    assert sync_k8s_kit.derive_release_name("my-trust-flip-trust-secrets") == "my-trust"


def test_derive_release_name_without_suffix_is_identity():
    assert sync_k8s_kit.derive_release_name("some-other-secret") == "some-other-secret"


def test_stamp_helm_ownership_issues_label_and_annotate(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda args, **kw: calls.append(args) or None)
    sync_k8s_kit.stamp_helm_ownership("trust-release-flip-trust-secrets", "flip-trust", "trust-release")

    label = next(c for c in calls if c[1] == "label")
    annotate = next(c for c in calls if c[1] == "annotate")
    assert "app.kubernetes.io/managed-by=Helm" in label
    assert "--overwrite" in label and "--overwrite" in annotate
    assert "meta.helm.sh/release-name=trust-release" in annotate
    assert "meta.helm.sh/release-namespace=flip-trust" in annotate
    assert ["-n", "flip-trust"] == label[3:5]  # namespaced


def test_patch_k8s_secret_stamps_ownership_on_create(monkeypatch):
    """A freshly-created Secret must be stamped Helm-owned (the #595 fix)."""
    calls = []

    class _Res:
        returncode = 1  # secret does not yet exist → create path

    def fake_run(args, **kw):
        calls.append(args)
        return _Res()

    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", fake_run)
    sync_k8s_kit.patch_k8s_secret(
        "trust-release-flip-trust-secrets", "flip-trust",
        {"trust-api-key": "x"}, "trust-release",
    )
    verbs = [c[1] for c in calls]
    assert "create" in verbs          # secret created
    assert "label" in verbs           # ...then stamped
    assert "annotate" in verbs


def test_stamp_helm_ownership_default_namespace(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda args, **kw: calls.append(args) or None)
    sync_k8s_kit.stamp_helm_ownership("s", "", "rel")
    annotate = next(c for c in calls if c[1] == "annotate")
    assert "meta.helm.sh/release-namespace=default" in annotate


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
