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
    # One-shot job services (FLIP#1583).
    "xnat-configure",
    "seed-orthanc",
    "smoke-cstore",
    "reconvert",
}

#: The job services, and what each one is. A job is never "up": `make up` only stages its
#: specification, and an operator (or imaging-api, for dcm2niix) executes it on demand.
JOB_SERVICES = {"dcm2niix", "xnat-configure", "seed-orthanc", "smoke-cstore", "reconvert"}

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
    "xnat-configure": {"XNAT_ADMIN_PASSWORD", "XNAT_SERVICE_PASSWORD"},
    "seed-orthanc": {"ORTHANC_PASSWORD"},
    "smoke-cstore": {"ORTHANC_PASSWORD", "XNAT_SERVICE_PASSWORD"},
    "reconvert": {"TRUST_INTERNAL_SERVICE_KEY", "AES_KEY_BASE64", "XNAT_SERVICE_PASSWORD"},
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


#
# The job services that put a Snowflake trust into a working state (FLIP#1583).
#
# xnat-configure, seed-orthanc, smoke-cstore and reconvert are the SPCS twins of the
# Compose / Helm targets of the same names. The guards below pin the properties that are
# not visible from reading one spec: that they stay jobs, that their scripts reach the
# container at all, and that the two which only read are mounted read-only.
#


def test_job_services_are_jobs_and_expose_nothing(manifest: dict, specs: dict) -> None:
    """A job is executed on demand; a job with an endpoint would be a service by accident.

    ``make up`` walks the manifest and skips `kind: job`, so a job that drifted to
    `kind: service` would be created as a permanently running container on the pool, which
    is both a bill and (for smoke-cstore) a C-STORE in a loop.
    """
    kinds = {service["name"]: service["kind"] for service in manifest["services"]}
    for name in JOB_SERVICES:
        assert kinds[name] == "job", f"{name} must stay a job service"
        assert not specs[name]["spec"].get("endpoints"), f"{name} must expose no endpoint"
        assert specs[name]["spec"]["containers"][0]["name"] == name


def test_job_services_run_on_the_job_pool(manifest: dict) -> None:
    """Never the service pool: a long conversion there would starve the running services."""
    job_pool = manifest["pools"]["job"]
    for service in manifest["services"]:
        if service["name"] in JOB_SERVICES:
            assert service["pool"] == job_pool, f"{service['name']} is not on the job pool"


@pytest.mark.parametrize(
    ("service", "marker"),
    [
        ("xnat-configure", "bash configure-xnat.sh"),
        ("seed-orthanc", "ORTHANC_URL}/instances"),
        ("smoke-cstore", "/modalities/${XNAT_MODALITY}/store"),
        ("reconvert", "imaging_api.scripts.reconvert_nifti"),
    ],
)
def test_job_scripts_are_inlined_into_the_spec(specs: dict, service: str, marker: str) -> None:
    """The script travels in the spec, because a job service can fetch nothing.

    There is no image of our own for these and no stage to read a script from at start-up,
    so a spec whose args lost the script would produce a container that exits 0 having
    done nothing, which reads as a pass.
    """
    container = containers(specs[service])[0]
    assert container["command"] == ["/bin/bash"]
    assert container["args"][0] == "-c"
    assert len(container["args"]) == 2
    assert marker in container["args"][1]


def test_xnat_configure_does_not_register_the_container_service(specs: dict) -> None:
    """configure-dcm2niix.sh must not run here: there is no Container Service on SPCS.

    Registering the command would give XNAT a converter it can never launch (no Docker
    socket, no swarm, no Kubernetes backend), so every trigger would fire into a failure
    instead of leaving conversion to the dcm2niix job service (FLIP#1578).
    """
    script = containers(specs["xnat-configure"])[0]["args"][-1]
    assert "configure-dcm2niix.sh" in script, "say out loud why it is not run"
    assert "bash configure-dcm2niix.sh" not in script
    assert specs["xnat-web"]["spec"]["containers"][0]["env"]["XNAT_CONTAINER_SERVICE_ENABLED"] == "false"


@pytest.mark.parametrize(
    ("service", "volume"),
    [("seed-orthanc", "seed"), ("smoke-cstore", "xnat-archive"), ("reconvert", "xnat-archive")],
)
def test_read_only_intent_is_recorded_for_the_reading_jobs(manifest: dict, service: str, volume: str) -> None:
    """SPCS has no readOnly flag on a volume, so the intent is recorded and granted.

    smoke-cstore matters most: a smoke test able to write the archive could manufacture
    the very evidence it then reports as a pass.
    """
    entry = next(item for item in manifest["services"] if item["name"] == service)
    assert volume in entry["read_only_volumes"]


def test_seed_orthanc_reads_the_seed_stage_only(specs: dict) -> None:
    """Seeding has no egress on Snowflake: the corpus arrives on @STAGES.SEED."""
    spec = specs["seed-orthanc"]["spec"]
    assert [volume["stageConfig"]["name"] for volume in spec["volumes"]] == ["@FLIP_TRUST_GSTT.STAGES.SEED"]
    script = containers(specs["seed-orthanc"])[0]["args"][-1]
    assert "huggingface" not in script.lower(), "a Snowflake trust has no egress to the dataset host"


def test_credentials_never_reach_a_job_command_line(specs: dict) -> None:
    """A password in argv is readable from /proc and is echoed back by the spec itself.

    The two curl-driven jobs authenticate through a 0600 netrc written at run time; this
    pins that neither grows a `--user` or a `-u` with an interpolated secret.
    """
    for name in ("seed-orthanc", "smoke-cstore"):
        script = containers(specs[name])[0]["args"][-1]
        assert "--netrc-file" in script, f"{name} must authenticate through a netrc"
        assert "chmod 600" in script, f"{name} must not leave the netrc world-readable"
        # Comments are allowed to name the option; code is not.
        code = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
        offenders = [line for line in code if "--user" in line or " -u " in line]
        assert not offenders, f"{name} puts a credential in argv: {offenders}"


def test_imaging_api_and_reconvert_share_the_converter_contract(specs: dict) -> None:
    """Both sides of the conversion path must agree, or reconvert silently uses the other.

    reconvert IS imaging-api's code; if the job's CONVERTER_BACKEND or its CONVERTER_IO
    mount drifted from the service's, a reconvert run would look for output the service
    never wrote, or launch through a Container Service that is not there.
    """
    service_env = containers(specs["imaging-api"])[0]["env"]
    job_env = containers(specs["reconvert"])[0]["env"]
    for key in (
        "CONVERTER_BACKEND",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
        "SNOWFLAKE_TOKEN_FILE",
        "CONVERTER_IO_DIR",
    ):
        assert service_env[key] == job_env[key], f"{key} drifted between imaging-api and reconvert"
    assert service_env["CONVERTER_BACKEND"] == "snowflake"


def test_the_converter_path_carries_no_database_password(specs: dict) -> None:
    """The OAuth session token IS the credential for CALLing the converter procedures."""
    for name in ("imaging-api", "reconvert"):
        container = containers(specs[name])[0]
        assert container["mountSessionToken"] is True, f"{name} has no session token to connect with"
        assert "SNOWFLAKE_PASSWORD" not in container["env"]
        # SPCS injects both; a rendered copy could only go stale.
        assert "SNOWFLAKE_HOST" not in container["env"]
        assert "SNOWFLAKE_ACCOUNT" not in container["env"]


def test_xnat_web_waits_for_a_resolvable_database_host(specs: dict) -> None:
    """`xnat-db` resolves nowhere on SPCS; the spec must name the service's real DNS name.

    The image's entrypoint hardcoded it before FLIP#1583. Both halves are pinned here: the
    JDBC URL XNAT connects with and the host its wait targets must be the same service.
    """
    env = containers(specs["xnat-web"])[0]["env"]
    expected = "xnat-db.app.flip-trust-gstt.snowflakecomputing.internal"
    assert env["XNAT_DB_HOST"] == expected
    assert expected in env["XNAT_DATASOURCE_URL"]
    # And the config copy must not aim at the root-owned default, which uid 1001 cannot write.
    assert env["XNAT_CONFIG_SHARE_DIR"].startswith("/data/xnat/")


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


def test_no_container_has_args_beside_a_multi_element_command(specs: dict) -> None:
    """The live account rejects such a spec: "Args not allowed when multiple commands are specified"."""
    for name, spec in specs.items():
        for container in containers(spec):
            if len(container.get("command", [])) > 1:
                assert "args" not in container, name


def test_no_kit_password_reaches_a_spec_in_plain_env(kit: dict) -> None:
    """xnat-configure once carried the kit's XNAT_ADMIN_INITIAL_PASSWORD as plain env."""
    import spec_templates

    marker = "SENTINEL-initial-admin-7f30"  # pragma: allowlist secret
    for rendered in spec_templates.build_all({**kit, "XNAT_ADMIN_INITIAL_PASSWORD": marker}, "GSTT"):
        assert marker not in json.dumps(rendered.spec), rendered.name


def test_smoke_cstore_pages_orthanc_with_since(specs: dict) -> None:
    """Orthanc answers `/instances?limit=1` with HTTP 400 ("Missing since"), live account."""
    script = containers(specs["smoke-cstore"])[0]["args"][-1]
    assert "instances?since=0&limit=1" in script
    assert "instances?limit=" not in script


def test_smoke_cstore_fails_fast_on_an_orthanc_error(specs: dict) -> None:
    """A 404 from /modalities/<m>/store once cost a 180 s wait for evidence that could not come."""
    script = containers(specs["smoke-cstore"])[0]["args"][-1]
    assert script.index(".HttpStatus") < script.index("EVIDENCE_TIMEOUT_SECONDS )")


def test_orthanc_knows_xnat_as_a_modality(specs: dict) -> None:
    """Without ORTHANC__DICOM_MODALITIES, a C-STORE to XNAT is a 404 (live account)."""
    env = containers(specs["orthanc"])[0]["env"]
    modalities = json.loads(env["ORTHANC__DICOM_MODALITIES"])
    assert modalities["XNAT"]["AET"] == "XNAT"
    # Orthanc refused the full SPCS name as too long; the bare name resolves via the search list.
    assert modalities["XNAT"]["Host"] == "xnat-web"
    assert modalities["XNAT"]["Port"] == env["XNAT_PORT"]
