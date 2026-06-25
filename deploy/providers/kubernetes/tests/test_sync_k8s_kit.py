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

"""Unit tests for sync_k8s_kit.py — focused on the FL-server port-only egress
rule (FLIP#593 pt.3) so a sync-kit re-run no longer silently strips it.

The egress rule is PORT-ONLY (FL_SERVER_PORT added to ``allowedEgressPorts``),
not a resolved-/32 CIDR pin: the FL server's internet-facing NLB has AWS-managed
IPs that rotate on recreation, so a pinned /32 would go stale. A port-only rule
is immune to that drift and renders deterministically across runs."""

import importlib.util
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


def test_render_override_emits_port_only_egress_rule():
    """The egress block is a port-only rule on FL_SERVER_PORT (no pinned CIDRs)."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert "networkPolicies:" in out
    assert "allowedEgressPorts:" in out
    # FL-server gRPC port present as a {port, protocol} entry...
    assert "- port: 8002" in out
    # ...and no resolved-/32 CIDR pinning remains.
    assert "allowedEgressCIDRsWithPorts" not in out
    assert "/32" not in out


def test_render_override_restates_default_egress_ports():
    """Helm replaces list values wholesale, so the override must restate the
    chart-default egress ports (DNS/HTTP/HTTPS) alongside the FL-server port —
    otherwise the override would wipe the default egress allowlist."""
    out = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    for port in ("- port: 53", "- port: 80", "- port: 443", "- port: 8002"):
        assert port in out, f"missing default/FL egress port: {port}"


def test_render_override_is_stable_across_runs():
    """The whole point of #593 pt.3: regenerating the override is idempotent —
    a second render with the same inputs reproduces the egress rule byte-for-byte.
    With a port-only rule this holds unconditionally (no DNS in the path)."""
    first = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    second = sync_k8s_kit.render_override(_FL_KIT, "Trust_K8s", "eu-west-2")
    assert first == second
    assert "allowedEgressPorts:" in second
    assert "- port: 8002" in second


def test_render_override_uses_kit_fl_server_port():
    """The port is sourced from the kit (FL_SERVER_PORT), not hardcoded — e.g.
    Flower's 9092 rather than NVFLARE's 8002."""
    kit = {**_FL_KIT, "FL_BACKEND": "flower", "FL_SERVER_PORT": "9092"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "- port: 9092" in out
    assert "- port: 8002" not in out


def test_render_override_omits_block_without_fl_port():
    """No FL_SERVER_PORT in the kit ⇒ no egress override emitted (the chart
    default egress allowlist from values.yaml applies unchanged)."""
    kit = {k: v for k, v in _FL_KIT.items() if k != "FL_SERVER_PORT"}
    out = sync_k8s_kit.render_override(kit, "Trust_K8s", "eu-west-2")
    assert "networkPolicies:" not in out
    assert "allowedEgressPorts" not in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
