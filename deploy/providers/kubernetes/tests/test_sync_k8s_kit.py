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

"""Unit tests for sync_k8s_kit.py — focused on the FL-server egress block
regeneration (FLIP#593 pt.3) so a re-run no longer silently strips it."""

import importlib.util
import socket
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "sync_k8s_kit.py"
_spec = importlib.util.spec_from_file_location("sync_k8s_kit", _SCRIPT)
sync_k8s_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_k8s_kit)


_FL_KIT = {
    "TRUST_NAME": "Kubernetes Trust",
    "FL_KIT_SLOT": "Trust_K8s",
    "FL_KIT_SLOT_NUMBER": "0",
    "CENTRAL_HUB_API_URL": "https://stag.flip.aicentre.co.uk/api",
    "FL_BACKEND": "nvflare",
    "AICENTRE_BUCKET_NAME": "flipstag-aicentre",
    "FLARE_KIT_DATE": "20260312",
    "NLB_SUBDOMAIN": "fl.stag.flip.aicentre.co.uk",
    "FL_SERVER_PORT": "8002",
}


def _fake_getaddrinfo(ips):
    return lambda host, *a, **k: [(socket.AF_INET, None, None, "", (ip, 0)) for ip in ips]


def test_resolve_egress_cidrs_sorted_unique(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo(["13.43.208.193", "13.43.162.56", "13.43.208.193"])
    )
    assert sync_k8s_kit.resolve_egress_cidrs("fl.example") == ["13.43.162.56/32", "13.43.208.193/32"]


def test_resolve_egress_cidrs_returns_empty_on_failure(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("offline")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert sync_k8s_kit.resolve_egress_cidrs("fl.unresolvable") == []


def test_render_override_emits_egress_block(monkeypatch):
    monkeypatch.setattr(sync_k8s_kit, "resolve_egress_cidrs", lambda host: ["13.43.162.56/32", "13.43.208.193/32"])
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert "networkPolicies:" in out
    assert "allowedEgressCIDRsWithPorts:" in out
    assert "- 13.43.162.56/32" in out
    assert "port: 8002" in out


def test_render_override_is_stable_across_runs(monkeypatch):
    """The whole point of #593 pt.3: regenerating the override is idempotent —
    a second render with the same inputs reproduces the egress block."""
    monkeypatch.setattr(sync_k8s_kit, "resolve_egress_cidrs", lambda host: ["13.43.162.56/32"])
    first = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    second = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert first == second
    assert "allowedEgressCIDRsWithPorts:" in second


def test_render_override_unresolved_leaves_todo(monkeypatch):
    monkeypatch.setattr(sync_k8s_kit, "resolve_egress_cidrs", lambda host: [])
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert "allowedEgressCIDRsWithPorts:" in out
    assert "TODO: could not resolve" in out


def test_render_override_omits_block_without_nlb(monkeypatch):
    monkeypatch.setattr(sync_k8s_kit, "resolve_egress_cidrs", lambda host: ["1.2.3.4/32"])
    kit = {k: v for k, v in _FL_KIT.items() if k != "NLB_SUBDOMAIN"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "allowedEgressCIDRsWithPorts" not in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
