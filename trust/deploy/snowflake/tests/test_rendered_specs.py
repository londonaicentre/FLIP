#!/usr/bin/env python3
#
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

"""Guards over the rendered SPCS specifications (FLIP#1582).

These are the security properties of the Snowflake trust stated as assertions, so a spec
edit that quietly loses one fails here rather than in an account. They need no Snowflake
account, no credentials and no network: everything is read off the rendered files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import SENTINELS, containers

EXPECTED_SERVICES = {
    "trust-api",
    "data-access-api",
    "imaging-api",
    "fl-client-net-1",
    "xnat-web",
    "xnat-db",
    "orthanc",
    "dcm2niix",
}

#: The contract's least-privilege secret map, as SECRET OBJECT NAMES per service.
EXPECTED_SECRETS = {
    "trust-api": {"TRUST_API_KEY", "TRUST_INTERNAL_SERVICE_KEY", "AES_KEY_BASE64"},
    "data-access-api": {"TRUST_INTERNAL_SERVICE_KEY", "AES_KEY_BASE64"},
    "imaging-api": {"TRUST_INTERNAL_SERVICE_KEY", "AES_KEY_BASE64", "XNAT_SERVICE_PASSWORD"},
    "fl-client-net-1": {"TRUST_INTERNAL_SERVICE_KEY"},
    "xnat-web": {"XNAT_ADMIN_PASSWORD", "XNAT_SERVICE_PASSWORD", "XNAT_DB_PASSWORD"},
    "xnat-db": {"XNAT_DB_ADMIN_PASSWORD", "XNAT_DB_PASSWORD"},
    "orthanc": {"ORTHANC_REGISTERED_USERS"},
    "dcm2niix": set(),
}


def test_every_contract_service_is_rendered(specs: dict) -> None:
    assert set(specs) == EXPECTED_SERVICES


def test_no_endpoint_is_public(specs: dict) -> None:
    """The epic's hard rule: a trust exposes nothing to the internet.

    ``public`` defaults to false, so an omitted field would also be safe - but an
    omitted field is indistinguishable from an oversight when the spec is read by a
    reviewer, so every endpoint states it.
    """
    for name, spec in specs.items():
        for endpoint in spec["spec"].get("endpoints", []):
            assert "public" in endpoint, f"{name}/{endpoint['name']}: state public explicitly"
            assert endpoint["public"] is False, f"{name}/{endpoint['name']} is public"


def test_no_secret_value_appears_in_any_rendered_byte(rendered: Path) -> None:
    """Credentials reach containers as SECRET objects, never as rendered env values."""
    for path in sorted(rendered.iterdir()):
        text = path.read_text()
        for key, value in SENTINELS.items():
            assert value not in text, f"{path.name} contains the kit value of {key}"


def test_secret_map_is_exactly_the_contract(specs: dict) -> None:
    for name, expected in EXPECTED_SECRETS.items():
        mapped = {
            entry["snowflakeSecret"]["objectName"].rsplit(".", 1)[-1]
            for container in containers(specs[name])
            for entry in container.get("secrets", [])
        }
        assert mapped == expected, f"{name}: secret map drifted from the naming contract"


def test_secrets_are_object_references_not_literals(specs: dict) -> None:
    """Every secret entry names an object and a key, never carries a value."""
    for name, spec in specs.items():
        for container in containers(spec):
            for entry in container.get("secrets", []):
                assert set(entry) == {"snowflakeSecret", "envVarName", "secretKeyRef"}, name
                assert entry["snowflakeSecret"]["objectName"].startswith("FLIP_TRUST_GSTT.APP."), name
                assert entry["secretKeyRef"] == "secret_string", name  # pragma: allowlist secret


def test_fl_client_holds_no_aes_key(specs: dict) -> None:
    """The cohort-admin gate (FLIP#857) only holds while the client cannot prove the key.

    Researcher training code runs inside the fl-client as the same user as the client,
    so a mapped AES_KEY_BASE64 would let it rewrite or delete a project's approved
    cohort through data-access-api's snapshot routes.
    """
    env_and_secrets = repr(specs["fl-client-net-1"])
    assert "AES_KEY_BASE64" not in env_and_secrets


def test_fl_client_mounts_only_its_own_net_prefix(specs: dict) -> None:
    """Least privilege across FL networks: net-1's client sees net-1's cache only."""
    spec = specs["fl-client-net-1"]["spec"]
    images = next(volume for volume in spec["volumes"] if volume["name"] == "images")
    assert images["source"] == "stage"
    assert images["stageConfig"]["name"].endswith("/net-1"), images["stageConfig"]["name"]
    assert "IMAGES" in images["stageConfig"]["name"]
    mount = next(
        mount
        for container in containers(specs["fl-client-net-1"])
        for mount in container["volumeMounts"]
        if mount["name"] == "images"
    )
    # The container path keeps the suffix so imaging-api's absolute paths still resolve.
    assert mount["mountPath"] == "/app/data/images/net-1"


def test_fl_client_kit_is_read_only_and_net_scoped(specs: dict, manifest: dict) -> None:
    """SPCS has no readOnly mount flag, so read-only is the grant; the manifest records it."""
    spec = specs["fl-client-net-1"]["spec"]
    fl_kit = next(volume for volume in spec["volumes"] if volume["name"] == "fl-kit")
    assert fl_kit["stageConfig"]["name"].endswith("/net-1")
    assert "FL_KIT" in fl_kit["stageConfig"]["name"]
    entry = next(service for service in manifest["services"] if service["name"] == "fl-client-net-1")
    assert entry["read_only_volumes"] == ["fl-kit"]


def test_fl_client_exposes_no_endpoint(specs: dict) -> None:
    assert "endpoints" not in specs["fl-client-net-1"]["spec"]


def test_block_volumes_only_for_the_two_databases(specs: dict) -> None:
    """Everything else shares a file tree on a stage, which survives a redeploy."""
    with_block = {
        name
        for name, spec in specs.items()
        for volume in spec["spec"].get("volumes", [])
        if volume["source"] == "block"
    }
    assert with_block == {"xnat-db", "orthanc"}


def test_block_volumes_are_encrypted_and_sized(specs: dict) -> None:
    for name in ("xnat-db", "orthanc"):
        volume = next(v for v in specs[name]["spec"]["volumes"] if v["source"] == "block")
        assert volume["size"].endswith("Gi"), f"{name}: block size must use the Gi suffix"
        assert volume["blockConfig"]["encryption"] == "SNOWFLAKE_SSE"


def test_every_container_has_resource_limits(specs: dict) -> None:
    """Without a limit Snowflake defaults it to the whole node, so one container can
    starve every other service on it and the trust pays for capacity it cannot use."""
    for name, spec in specs.items():
        for container in containers(spec):
            resources = container.get("resources", {})
            assert resources.get("limits", {}).get("cpu"), f"{name}/{container['name']}: no cpu limit"
            assert resources.get("limits", {}).get("memory"), f"{name}/{container['name']}: no memory limit"
            assert resources.get("requests", {}).get("cpu"), f"{name}/{container['name']}: no cpu request"
            assert resources.get("requests", {}).get("memory"), f"{name}/{container['name']}: no memory request"


@pytest.mark.parametrize("service", ["trust-api", "data-access-api", "imaging-api"])
def test_apis_probe_health(specs: dict, service: str) -> None:
    for container in containers(specs[service]):
        for probe in ("readinessProbe", "livenessProbe"):
            assert container[probe]["path"] == "/health", f"{service}: {probe} must hit /health"
            assert container[probe]["port"] == 8000


def test_dicom_listeners_are_internal_tcp_endpoints(specs: dict) -> None:
    """Retrieval is DQR: the PACS opens a NEW association back to C-STORE (FLIP#993).

    Without a reachable receiver the C-FIND/C-MOVE succeed and every retrieval silently
    times out, so these two endpoints are the imaging path, not a convenience.
    """
    orthanc = {e["name"]: e for e in specs["orthanc"]["spec"]["endpoints"]}
    assert orthanc["dicom"]["port"] == 4242
    assert orthanc["dicom"]["protocol"] == "TCP"
    xnat = {e["name"]: e for e in specs["xnat-web"]["spec"]["endpoints"]}
    assert xnat["dicom"]["port"] == 8104, "the receiver port the PACS dials is the kit's XNAT_PORT"
    assert xnat["dicom"]["protocol"] == "TCP"


def test_inter_service_urls_use_spcs_dns_not_compose_names(specs: dict) -> None:
    """An SPCS service's DNS name is derived from service, schema and database names, so
    the Compose aliases (``http://imaging-api:8000``) resolve nowhere."""
    suffix = "app.flip-trust-gstt.snowflakecomputing.internal"
    env = {
        name: container["env"] for name, spec in specs.items() for container in containers(spec) if "env" in container
    }
    assert env["trust-api"]["IMAGING_API_URL"] == f"http://imaging-api.{suffix}:8000"
    assert env["trust-api"]["DATA_ACCESS_API_URL"] == f"http://data-access-api.{suffix}:8000"
    assert env["imaging-api"]["XNAT_URL"] == f"http://xnat-web.{suffix}:8080"
    assert env["xnat-web"]["PACS_HOST"] == f"orthanc.{suffix}"
    assert env["xnat-web"]["XNAT_DATASOURCE_URL"] == f"jdbc:postgresql://xnat-db.{suffix}:5432/xnat"
    assert env["fl-client-net-1"]["IMAGING_API_URL"] == f"http://imaging-api.{suffix}:8000"
    for name, values in env.items():
        for key, value in values.items():
            if key.endswith("_URL") and value.startswith("http"):
                assert "://imaging-api:" not in value, f"{name}/{key}"
                assert "://data-access-api:" not in value, f"{name}/{key}"


def test_data_access_api_reads_omop_from_snowflake(specs: dict) -> None:
    """OMOP lives in native Snowflake tables, read as a SELECT-only role through the
    SPCS-mounted OAuth token - so there is no database password to hold."""
    env = containers(specs["data-access-api"])[0]["env"]
    assert env["OMOP_BACKEND"] == "snowflake"
    assert env["SNOWFLAKE_DATABASE"] == "FLIP_TRUST_GSTT"
    assert env["SNOWFLAKE_SCHEMA"] == "OMOP"
    assert env["SNOWFLAKE_ROLE"] == "FLIP_GSTT_OMOP_READER"
    assert env["SNOWFLAKE_WAREHOUSE"] == "FLIP_GSTT_WH"
    assert int(env["SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS"]) > 0
    container = containers(specs["data-access-api"])[0]
    assert container["mountSessionToken"] is True
    # /snowflake/session is reserved: the live account rejects any sessionTokenConfig.path
    # there ("overlaps a location reserved by Snowflake"), and SPCS mounts the token at
    # /snowflake/session/token without being asked.
    assert "sessionTokenConfig" not in container
    assert "DATA_ACCESS_POSTGRES_PASSWORD" not in env


def test_governance_is_mounted_read_only_for_data_access_api(manifest: dict) -> None:
    """The service must never be able to rewrite its own policy (FLIP#1259)."""
    entry = next(s for s in manifest["services"] if s["name"] == "data-access-api")
    assert "governance" in entry["read_only_volumes"]


def test_data_access_api_keeps_the_kits_disclosure_floor(specs: dict) -> None:
    assert containers(specs["data-access-api"])[0]["env"]["COHORT_QUERY_THRESHOLD"] == "15"


def test_images_are_pulled_from_the_account_repository(specs: dict) -> None:
    """No run-time GHCR egress: every image is in the account's own repository."""
    for name, spec in specs.items():
        for container in containers(spec):
            assert container["image"].startswith("/FLIP_TRUST_GSTT/APP/IMAGES/"), f"{name}: {container['image']}"
            assert "ghcr.io" not in container["image"], name


def test_specs_reference_digests_once_images_are_mirrored(kit: dict, tmp_path: Path) -> None:
    """After `make mirror-images` the specs pin digests: a tag can be re-pushed."""
    from render_specs import render

    digest = "sha256:" + "ab" * 32
    render(kit, "GSTT", "stag", tmp_path, digests={"trust-api": digest})
    import yaml

    spec = yaml.safe_load((tmp_path / "trust-api.yaml").read_text())
    assert spec["spec"]["containers"][0]["image"].endswith(f"@{digest}")


def test_job_service_runs_on_the_job_pool_and_holds_nothing(specs: dict, manifest: dict) -> None:
    entry = next(s for s in manifest["services"] if s["name"] == "dcm2niix")
    assert entry["kind"] == "job"
    assert entry["pool"] == "FLIP_GSTT_JOB_POOL"
    assert entry["secrets"] == []
    assert "endpoints" not in specs["dcm2niix"]["spec"]


def test_logs_and_metrics_reach_the_event_table(specs: dict) -> None:
    """There is no Loki/Grafana/Alloy on Snowflake: the event table is the stack."""
    for name, spec in specs.items():
        assert spec["spec"]["logExporters"]["eventTableConfig"]["logLevel"] in ("INFO", "ERROR"), name
        assert spec["spec"]["platformMonitor"]["metricConfig"]["groups"], name


def test_multiple_fl_nets_each_get_their_own_prefix(kit: dict, tmp_path: Path) -> None:
    import yaml
    from render_specs import render

    multi = dict(kit, FL_NETS="1,2")
    render(multi, "GSTT", "stag", tmp_path, digests={})
    for net in ("1", "2"):
        spec = yaml.safe_load((tmp_path / f"fl-client-net-{net}.yaml").read_text())
        for volume in spec["spec"]["volumes"]:
            if volume["source"] == "stage":
                assert volume["stageConfig"]["name"].endswith(f"/net-{net}")


def test_the_flare_client_gets_a_writable_workspace_seeded_from_the_kit(specs: dict) -> None:
    """The FLARE client chmods /app/startup and writes /app/local (privacy.json, log config).
    The kit stage stays read-only; local volumes take the copy (found on the live account)."""
    spec = specs["fl-client-net-1"]["spec"]
    container = containers(specs["fl-client-net-1"])[0]
    mounts = {m["mountPath"]: m["name"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v for v in spec["volumes"]}
    for part in ("startup", "local", "transfer"):
        assert volumes[mounts[f"/app/{part}"]]["source"] == "local", part
        assert f"cp -R /app/fl-kit/{part}/. /app/{part}/" in container["command"][-1]
    assert volumes[mounts["/app/fl-kit"]]["source"] == "stage"
    assert container["command"][-1].endswith("exec /app/entrypoint.sh")


def test_the_flower_client_reads_the_kit_at_the_mount_root(kit: dict, tmp_path: Path) -> None:
    """The stage volume is FL_KIT/net-N itself, so no net-N segment inside the mount."""
    import spec_templates

    flower = {**kit, "FL_BACKEND": "flower", "NLB_SUBDOMAIN": "fl.example", "FL_SERVER_PORT": "9092"}
    rendered = {r.name: r for r in spec_templates.build_all(flower, "GSTT")}
    args = rendered["fl-client-net-1"].spec["spec"]["containers"][0]["args"]
    assert "/app/fl-kit/certificates/ca.crt" in args
    assert not any("/app/fl-kit/net-" in a for a in args)


def test_no_spec_points_a_session_token_at_the_reserved_path(specs: dict) -> None:
    """Snowflake reserves /snowflake/session and rejects the whole spec when a
    sessionTokenConfig.path overlaps it; the token is mounted there by default."""
    for name, spec in specs.items():
        for container in containers(spec):
            path = container.get("sessionTokenConfig", {}).get("path", "")
            assert not path.startswith("/snowflake/session"), f"{name}: {path}"


def test_xnat_db_keeps_the_superuser_apart_from_the_xnat_role(specs: dict) -> None:
    """With POSTGRES_USER=xnat the entrypoint made `xnat` the superuser with the admin
    password, so XNAT's datasource password was refused (live account)."""
    env = containers(specs["xnat-db"])[0]["env"]
    assert "POSTGRES_USER" not in env
    assert env["XNAT_DATASOURCE_USERNAME"] == "xnat"


def test_orthanc_knows_xnat_as_a_modality(specs: dict) -> None:
    """Without ORTHANC__DICOM_MODALITIES, a C-STORE to XNAT is a 404 (live account)."""
    env = containers(specs["orthanc"])[0]["env"]
    modalities = json.loads(env["ORTHANC__DICOM_MODALITIES"])
    assert modalities["XNAT"]["AET"] == "XNAT"
    assert modalities["XNAT"]["Host"].startswith("xnat-web.")
    assert modalities["XNAT"]["Port"] == env["XNAT_PORT"]
