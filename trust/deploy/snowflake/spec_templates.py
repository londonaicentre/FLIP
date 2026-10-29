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

"""One SPCS service-specification template per FLIP trust service (FLIP#1582).

Each ``_<service>`` function below is that service's template: it takes the trust's kit
file (``trust/.env.<CODE>.<env>``) plus the naming contract and returns the specification
Snowflake is handed at ``CREATE SERVICE`` / ``ALTER SERVICE``. The compose files
(``trust/deploy/compose_trust.production*.yml``, ``trust/xnat/docker-compose-stack.yml``)
are the source of truth for each service's environment, ports and volumes; the templates
are their SPCS translation, and three things change in that translation:

1. **Names resolve differently.** An SPCS service's DNS name is derived from the service,
   schema and database names, so ``http://imaging-api:8000`` resolves nowhere. Every
   inter-service URL (``IMAGING_API_URL``, ``DATA_ACCESS_API_URL``, ``XNAT_URL``,
   ``PACS_HOST``, the XNAT JDBC URL) is derived from :class:`naming.Names`.
2. **Secrets are objects, not values.** Nothing here reads a credential out of the kit.
   Each service maps the Snowflake SECRET objects of the contract through
   ``containers.secrets``; the values are written only by ``make sync-kit``.
3. **Storage is stages and block volumes.** Shared file trees (images, XNAT archive,
   converter I/O, cohort snapshots, governance) are stage volumes; the two databases
   (xnat-db, Orthanc) get their own block volume.

No endpoint is public. The only internal TCP endpoints are the two DICOM listeners
(Orthanc's 4242 and XNAT's receiver on ``XNAT_PORT``), which exist because retrieval is
DQR: the PACS opens a *new* association back to XNAT to C-STORE, and without a reachable
receiver queries succeed while every retrieval times out (FLIP#993).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from naming import Names, fl_client_image

#: Secret object -> container environment variable, per service. This is the contract's
#: least-privilege map, and the spec guards assert it exactly: the fl-client gets the
#: trust-internal key and nothing else, because holding AES_KEY_BASE64 would let
#: researcher training code pass data-access-api's cohort-admin gate and rewrite or
#: delete an approved cohort (trust/AGENTS.md, FLIP#857).
SECRET_MAP: dict[str, dict[str, str]] = {
    "trust-api": {
        "TRUST_API_KEY": "TRUST_API_KEY",  # pragma: allowlist secret
        "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
        "AES_KEY_BASE64": "AES_KEY_BASE64",  # pragma: allowlist secret
    },
    "data-access-api": {
        "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
        "AES_KEY_BASE64": "AES_KEY_BASE64",  # pragma: allowlist secret
    },
    "imaging-api": {
        "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
        "AES_KEY_BASE64": "AES_KEY_BASE64",  # pragma: allowlist secret
        "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    },
    "fl-client": {
        "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
    },
    "xnat-web": {
        "XNAT_ADMIN_PASSWORD": "XNAT_ADMIN_PASSWORD",  # pragma: allowlist secret
        "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
        "XNAT_DB_PASSWORD": "XNAT_DATASOURCE_PASSWORD",  # pragma: allowlist secret
    },
    "xnat-db": {
        "XNAT_DB_PASSWORD": "POSTGRES_PASSWORD",  # pragma: allowlist secret
    },
    "xnat-nginx": {},
    "orthanc": {
        "ORTHANC_PASSWORD": "ORTHANC_PASSWORD",  # pragma: allowlist secret
    },
    "dcm2niix": {},
    # One-shot job services (FLIP#1583). Each holds the narrowest set that lets it do its
    # one job: xnat-configure authenticates as the XNAT admin and creates the service
    # account, seed-orthanc only needs the PACS password, smoke-cstore drives Orthanc and
    # reads XNAT back as the service account, and reconvert is imaging-api's own set.
    "xnat-configure": {
        "XNAT_ADMIN_PASSWORD": "XNAT_ADMIN_PASSWORD",  # pragma: allowlist secret
        "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    },
    "seed-orthanc": {
        "ORTHANC_PASSWORD": "ORTHANC_PASSWORD",  # pragma: allowlist secret
    },
    "smoke-cstore": {
        "ORTHANC_PASSWORD": "ORTHANC_PASSWORD",  # pragma: allowlist secret
        "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    },
    "reconvert": {
        "TRUST_INTERNAL_SERVICE_KEY": "TRUST_INTERNAL_SERVICE_KEY",  # pragma: allowlist secret
        "AES_KEY_BASE64": "AES_KEY_BASE64",  # pragma: allowlist secret
        "XNAT_SERVICE_PASSWORD": "XNAT_SERVICE_PASSWORD",  # pragma: allowlist secret
    },
}

#: Where the job-service scripts live, beside this module. They are repo files rather than
#: heredocs inside the templates so that they can be read, reviewed and (for the two with
#: real logic) exercised on their own; ``_script`` inlines the text at render time, which
#: keeps the rendered specification self-contained and means a job service needs no image
#: of its own and no stage to fetch anything from.
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

#: Services allowed a block volume. Everything else shares a file tree on a stage, so a
#: second instance (or a redeploy) sees the same files. Only the two databases need the
#: POSIX semantics a block volume provides; a Postgres data directory on a stage volume
#: is not a supported shape.
BLOCK_VOLUME_SERVICES = frozenset({"xnat-db", "orthanc"})

#: Health endpoint of the FLIP APIs. XNAT, Orthanc, nginx and the converter have no
#: ``/health`` route, so they carry no probe (see README, "Assumptions pending spikes").
HEALTH_PATH = "/health"


@dataclass
class Rendered:
    """One rendered service, plus the facts the guards and the Makefile need about it."""

    #: Spec-level, lower-case name (``trust-api``). The SQL object name is its upper-case,
    #: underscored form: ``FLIP_TRUST_<CODE>.APP.TRUST_API``.
    name: str
    #: ``service`` (CREATE / ALTER SERVICE) or ``job`` (EXECUTE JOB SERVICE).
    kind: str
    #: Compute pool the service runs on.
    pool: str
    spec: dict[str, Any]
    #: Per-volume read-only intent. SPCS has no ``readOnly`` flag on a volume mount, so
    #: read-only is enforced by the GRANTs the deploy issues (READ, never WRITE, on the
    #: stage). Recording the intent here keeps it assertable offline and keeps the kit
    #: sync's grants and the specs from drifting apart.
    read_only_volumes: list[str] = field(default_factory=list)


def _resources(cpu: str, memory: str, cpu_limit: str, memory_limit: str) -> dict[str, Any]:
    """Requests and limits for a container.

    Every service carries both. Without a limit Snowflake defaults it to the node
    capacity of the pool's instance family, so one runaway container can starve every
    other service sharing the node, and a trust pays for a node it cannot use.
    """
    return {
        "requests": {"cpu": cpu, "memory": memory},
        "limits": {"cpu": cpu_limit, "memory": memory_limit},
    }


def _secrets(service: str, names: Names) -> list[dict[str, Any]]:
    """``containers.secrets`` entries for a service, from :data:`SECRET_MAP`.

    The object name is fully qualified (``FLIP_TRUST_<CODE>.APP.<SECRET>``) because a
    service is created with the deploy role's session, whose current schema is not
    guaranteed to be APP; an unqualified name would resolve somewhere else or nowhere.

    ``secretKeyRef: secret_string`` throughout: the contract's secrets are
    GENERIC_STRING objects holding a single value, not username/password pairs.
    """
    return [
        {
            "snowflakeSecret": {"objectName": names.secret(secret)},
            "envVarName": env_var,
            "secretKeyRef": "secret_string",  # pragma: allowlist secret
        }
        for secret, env_var in sorted(SECRET_MAP[service].items())
    ]


def _probe(port: int, path: str = HEALTH_PATH) -> dict[str, Any]:
    return {"port": port, "path": path, "periodSeconds": 10, "failureThreshold": 3}


def _image(names: Names, repo: str, kit: dict[str, str], digests: dict[str, str]) -> str:
    """An image reference in the account repository.

    By digest when ``make mirror-images`` has recorded one (``images.lock.json``), which
    is what a deployed trust should run: a tag in the account repository can be moved,
    a digest cannot. Falls back to the kit's tag so a first render, before any mirror,
    still produces a usable spec.
    """
    digest = digests.get(repo, "").strip()
    if digest:
        return f"{names.image_repository_path}/{repo}@{digest}"
    tag = kit.get("DOCKER_TAG", "").strip() or "stag"
    if repo in ("xnat-web", "xnat-db", "xnat-nginx", "xnat-dcm2niix"):
        tag = kit.get("XNAT_TAG", "").strip() or tag
    elif repo == "orthanc":
        tag = kit.get("ORTHANC_TAG", "").strip() or tag
    return f"{names.image_repository_path}/{repo}:{tag}"


def _log_level(kit: dict[str, str]) -> str:
    return kit.get("TRUST_LOG_LEVEL", "").strip() or "INFO"


def _event_table_exports() -> dict[str, Any]:
    """Container logs and platform metrics to the account event table (FLIP#1584).

    The Snowflake trust runs no Loki/Grafana/Alloy: three always-on containers cost about
    44 credits a month against roughly 0.02 credits per GB of event-table ingest, so the
    account's own telemetry is the observability stack.
    """
    return {
        "logExporters": {"eventTableConfig": {"logLevel": "INFO"}},
        "platformMonitor": {"metricConfig": {"groups": ["system", "status"]}},
    }


def _trust_api(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    env = {
        "ENV": "production",
        "LOG_LEVEL": _log_level(kit),
        "CENTRAL_HUB_API_URL": kit.get("CENTRAL_HUB_API_URL", ""),
        "TRUST_NAME": kit.get("TRUST_NAME", names.code),
        "POLL_INTERVAL_SECONDS": kit.get("POLL_INTERVAL_SECONDS", "5"),
        "TRUST_API_KEY_HEADER": kit.get("TRUST_API_KEY_HEADER", "Authorization"),
        "TRUST_INTERNAL_SERVICE_KEY_HEADER": kit.get(
            "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
        ),
        # Derived, not copied: the compose values name Docker network aliases.
        "DATA_ACCESS_API_URL": f"http://{names.dns('data-access-api')}:8000",
        "IMAGING_API_URL": f"http://{names.dns('imaging-api')}:8000",
        # The hub's Connection Status reports the FL build this site runs; the fl-client
        # has no health endpoint of its own (FLIP#1204).
        "FL_CLIENT_IMAGE_TAG": kit.get("DOCKER_FL_TAG", ""),
    }
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "trust-api",
                    "image": _image(names, "trust-api", kit, digests),
                    "env": env,
                    "readinessProbe": _probe(8000),
                    "livenessProbe": _probe(8000),
                    "resources": _resources("0.5", "1Gi", "1", "2Gi"),
                    "secrets": _secrets("trust-api", names),
                }
            ],
            "endpoints": [{"name": "api", "port": 8000, "protocol": "HTTP", "public": False}],
            **_event_table_exports(),
        }
    }
    return Rendered("trust-api", "service", names.pool, spec)


def _data_access_api(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """OMOP reads go to native Snowflake tables through the #13 backend seam.

    No database password anywhere: the container reads the SPCS-mounted OAuth session
    token and connects as ``FLIP_<CODE>_OMOP_READER``, a SELECT-only role with a
    statement timeout. ``OMOP_BACKEND=snowflake`` plus the ``SNOWFLAKE_*`` settings are
    all this template owns; the backend itself is FLIP#13's work.
    """
    env = {
        "ENV": "production",
        "LOG_LEVEL": _log_level(kit),
        "TRUST_INTERNAL_SERVICE_KEY_HEADER": kit.get(
            "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
        ),
        "OMOP_BACKEND": "snowflake",
        "SNOWFLAKE_DATABASE": names.database,
        "SNOWFLAKE_SCHEMA": "OMOP",
        "SNOWFLAKE_WAREHOUSE": names.warehouse,
        "SNOWFLAKE_ROLE": names.omop_reader_role,
        "SNOWFLAKE_TOKEN_FILE": "/snowflake/session/token",
        "SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS": kit.get("SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS", "300"),
        # The trust's own disclosure floor, not a hub setting.
        "COHORT_QUERY_THRESHOLD": kit.get("COHORT_QUERY_THRESHOLD", "10"),
        "COHORT_SNAPSHOT_DIR": "/snapshots",
    }
    volume_mounts = [
        {"name": "cohort-snapshots", "mountPath": "/snapshots"},
        {"name": "governance", "mountPath": "/governance"},
    ]
    volumes = [
        {"name": "cohort-snapshots", "source": "stage", "stageConfig": {"name": names.stage_ref("COHORT_SNAPSHOTS")}},
        {"name": "governance", "source": "stage", "stageConfig": {"name": names.stage_ref("GOVERNANCE")}},
    ]
    # The governance document (FLIP#1259) travels on the GOVERNANCE stage rather than as
    # a host path: a path on the deploy host means nothing inside a container. Unset =
    # platform defaults, exactly as in Compose, so the variable is only emitted when the
    # kit names a document.
    if kit.get("ACCESS_POLICY_FILE", "").strip():
        env["ACCESS_POLICY_FILE"] = "/governance/governance.toml"
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "data-access-api",
                    "image": _image(names, "data-access-api", kit, digests),
                    "env": env,
                    "readinessProbe": _probe(8000),
                    "livenessProbe": _probe(8000),
                    "resources": _resources("1", "2Gi", "2", "4Gi"),
                    # The OAuth session token IS this service's database credential.
                    "mountSessionToken": True,
                    "volumeMounts": volume_mounts,
                    "secrets": _secrets("data-access-api", names),
                }
            ],
            "endpoints": [{"name": "api", "port": 8000, "protocol": "HTTP", "public": False}],
            "volumes": volumes,
            **_event_table_exports(),
        }
    }
    # The service must never be able to rewrite its own policy: READ on GOVERNANCE, no WRITE.
    return Rendered("data-access-api", "service", names.pool, spec, read_only_volumes=["governance"])


def _converter_env(kit: dict[str, str], names: Names) -> dict[str, str]:
    """Settings for the Snowflake DICOM to NIfTI path, shared by imaging-api and reconvert.

    ``CONVERTER_BACKEND`` is the gate the whole feature hangs on: unset (or
    ``container-service``) nothing changes, and Compose and Helm keep launching dcm2niix
    through XNAT's Container Service exactly as before. ``snowflake`` makes imaging-api
    CALL the owner's-rights procedures instead (FLIP#1581), which is the only path that
    works here, since SPCS has no Docker socket for Container Service to drive.

    SNOWFLAKE_HOST and SNOWFLAKE_ACCOUNT are deliberately absent: SPCS injects both into
    every container, and a rendered copy could only go stale. The OAuth session token at
    SNOWFLAKE_TOKEN_FILE is the credential; there is no password anywhere on this path.
    """
    return {
        "CONVERTER_BACKEND": "snowflake",
        "SNOWFLAKE_DATABASE": names.database,
        # The procedures live in APP, beside the services and the secrets.
        "SNOWFLAKE_SCHEMA": "APP",
        "SNOWFLAKE_TOKEN_FILE": "/snowflake/session/token",
        # imaging-api reads the converter's output from its own mount of the stage the
        # procedure told the job to write to, then registers the files in XNAT over REST.
        "CONVERTER_IO_DIR": "/converter-io",
        # Named for the reader and for `make status`: the job the procedure launches.
        "DCM2NIIX_JOB_SERVICE": names.service("dcm2niix"),
        "DCM2NIIX_JOB_POOL": names.job_pool,
    }


def _imaging_api(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    env = {
        "ENV": "production",
        "LOG_LEVEL": _log_level(kit),
        "TRUST_INTERNAL_SERVICE_KEY_HEADER": kit.get(
            "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
        ),
        "XNAT_URL": f"http://{names.dns('xnat-web')}:8080",
        "DATA_ACCESS_API_URL": f"http://{names.dns('data-access-api')}:8000",
        "XNAT_PORT": kit.get("XNAT_PORT", "8104"),
        # The C-MOVE destination DQR matches by exact AE title and port.
        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
        "XNAT_SERVICE_USER": kit.get("XNAT_SERVICE_USER", ""),
        "BASE_IMAGES_DOWNLOAD_DIR": "/app/data/images",
        # NIfTI conversion is an SPCS job service rather than an XNAT Container Service
        # launch (FLIP#1578, FLIP#1583): imaging-api CALLs APP.CONVERT_SCAN, polls
        # APP.CONVERT_STATUS, and registers the result in XNAT over REST.
        **_converter_env(kit, names),
    }
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "imaging-api",
                    "image": _image(names, "imaging-api", kit, digests),
                    "env": env,
                    "readinessProbe": _probe(8000),
                    "livenessProbe": _probe(8000),
                    "resources": _resources("1", "2Gi", "2", "4Gi"),
                    # The OAuth session token is how imaging-api CALLs the converter
                    # procedures; it is not a database credential for anything else.
                    "mountSessionToken": True,
                    "volumeMounts": [
                        # The whole images tree: imaging-api writes every net's cache.
                        {"name": "images", "mountPath": "/app/data/images"},
                        # Where the dcm2niix job leaves the NIfTI it produced. imaging-api
                        # reads it from here and PUTs it into XNAT as a NIFTI resource.
                        {"name": "converter-io", "mountPath": "/converter-io"},
                    ],
                    "secrets": _secrets("imaging-api", names),
                }
            ],
            "endpoints": [{"name": "api", "port": 8000, "protocol": "HTTP", "public": False}],
            "volumes": [
                {"name": "images", "source": "stage", "stageConfig": {"name": names.stage_ref("IMAGES")}},
                {"name": "converter-io", "source": "stage", "stageConfig": {"name": names.stage_ref("CONVERTER_IO")}},
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("imaging-api", "service", names.pool, spec)


#: The FLARE participant kit's directories, as provisioning writes them and Helm mounts them.
FLARE_WORKSPACE = ("startup", "local", "transfer")


def _fl_client(kit: dict[str, str], names: Names, digests: dict[str, str], net: str) -> Rendered:
    """One FL client per FL network, mounting that net's images prefix and nothing else.

    Two isolation rules carry over from Compose unchanged, and the spec guards assert both:

    * The images mount is sliced to ``net-<N>``. imaging-api's returned paths all live
      under ``<IMAGES_DIR>/<NET_ID>/``; mounting the whole tree would let researcher
      training code read every other net's cached studies.
    * The kit is mounted read-only and the client receives no AES key, so it cannot pass
      data-access-api's cohort-admin gate.
    """
    net_id = f"net-{net}"
    backend = (kit.get("FL_BACKEND", "").strip() or "nvflare").lower()
    repo = fl_client_image(kit)
    env = {
        "NET_ID": net_id,
        "LOCAL_DEV": "false",
        "IMAGES_DIR": "/app/data/images",
        "NUM_AVAILABLE_GPUS": kit.get("NUM_AVAILABLE_GPUS", "0"),
        "MEMORY_PER_GPU_IN_GIB": kit.get("MEMORY_PER_GPU_IN_GIB", "0"),
        # FL clients never hold hub credentials; only the fl-server talks to flip-api.
        "DATA_ACCESS_API_URL": f"http://{names.dns('data-access-api')}:8000",
        "IMAGING_API_URL": f"http://{names.dns('imaging-api')}:8000",
        "TRUST_INTERNAL_SERVICE_KEY_HEADER": kit.get(
            "TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key"
        ),
    }
    container: dict[str, Any] = {
        "name": f"fl-client-{net_id}",
        "image": _image(names, repo, kit, digests),
        "env": env,
        "resources": _resources("2", "8Gi", "4", "16Gi"),
        "volumeMounts": [
            # The container path keeps the net-N suffix so imaging-api's absolute paths
            # still resolve inside the client.
            {"name": "images", "mountPath": f"/app/data/images/{net_id}"},
            {"name": "fl-kit", "mountPath": "/app/fl-kit"},
        ],
        "secrets": _secrets("fl-client", names),
    }
    if backend == "flower":
        env["SUPERNODE_NAME"] = kit.get("FL_KIT_SLOT", "")
        slot = kit.get("FL_KIT_SLOT_NUMBER", "1")
        superlink = kit.get("NLB_SUBDOMAIN", "")
        port = kit.get("FL_EDGE_PORT", "").strip() or kit.get("FL_SERVER_PORT", "").strip()
        container["args"] = [
            "--root-certificates",
            # The stage volume is FL_KIT/<net-N> itself, so the kit sits at the mount root.
            "/app/fl-kit/certificates/ca.crt",
            "--superlink",
            f"{superlink}:{port}",
            "--auth-supernode-private-key",
            f"/app/fl-kit/keys/supernode_credentials_{slot}",
            "--port",
            kit.get("SUPERNODE_1_CLIENTAPPIO_PORT", "9094"),
        ]
    flare_workspace: list[dict[str, str]] = []
    if backend != "flower":
        # The FLARE client runs from /app/startup (it chmods the scripts there) and renders
        # privacy.json and its log config into /app/local, so both must be writable. A stage
        # volume cannot be mounted writable here (the kit stays read-only), so the client gets
        # local volumes and the kit is copied in before the image's own entrypoint runs.
        flare_workspace = [{"name": f"fl-{part}", "source": "local"} for part in FLARE_WORKSPACE]
        container["volumeMounts"] += [{"name": f"fl-{part}", "mountPath": f"/app/{part}"} for part in FLARE_WORKSPACE]
        copies = " && ".join(
            f"if [ -d /app/fl-kit/{part} ]; then cp -R /app/fl-kit/{part}/. /app/{part}/; fi"
            for part in FLARE_WORKSPACE
        )
        container["command"] = ["/bin/sh", "-c", f"{copies} && exec /app/entrypoint.sh"]
    if int(kit.get("NUM_AVAILABLE_GPUS", "0") or 0) > 0:
        gpus = kit["NUM_AVAILABLE_GPUS"]
        container["resources"]["requests"]["nvidia.com/gpu"] = gpus
        container["resources"]["limits"]["nvidia.com/gpu"] = gpus
    pool = names.gpu_pool if int(kit.get("NUM_AVAILABLE_GPUS", "0") or 0) > 0 else names.pool
    spec = {
        "spec": {
            "containers": [container],
            # No endpoints at all: the client dials out to the FL server through the
            # external access integration and listens for nothing.
            "volumes": [
                {
                    "name": "images",
                    "source": "stage",
                    "stageConfig": {"name": names.stage_ref("IMAGES", net_id)},
                },
                {
                    "name": "fl-kit",
                    "source": "stage",
                    "stageConfig": {"name": names.stage_ref("FL_KIT", net_id)},
                },
                # FLARE and Flower both want real shared memory for dataloader workers;
                # the Compose stack's shm_size: 8gb equivalent.
                {"name": "dshm", "source": "memory", "size": "8Gi"},
                *flare_workspace,
            ],
            **_event_table_exports(),
        }
    }
    container["volumeMounts"].append({"name": "dshm", "mountPath": "/dev/shm"})
    return Rendered(f"fl-client-{net_id}", "service", pool, spec, read_only_volumes=["fl-kit"])


def _pacs_env(kit: dict[str, str], names: Names) -> dict[str, str]:
    """The upstream PACS and the DQR schedule, as configure-xnat.sh reads them.

    Shared by xnat-web (which serves the configuration) and the xnat-configure job (which
    applies it). One source so the two cannot disagree about which PACS this trust talks
    to, which is the drift that hid FLIP#993 behind a chart that looked correct.
    """
    return {
        # Derived: the PACS is another SPCS service, so its DNS name is not "orthanc".
        "PACS_HOST": kit.get("PACS_HOST_OVERRIDE", "").strip() or names.dns("orthanc"),
        "PACS_AETITLE": kit.get("PACS_AETITLE", "ORTHANC"),
        "PACS_QR_PORT": kit.get("PACS_QR_PORT", "4242"),
        "PACS_LABEL": kit.get("PACS_LABEL", "Test PACS instance"),
        "PACS_SUPPORTS_EXTENDED_NEGOTIATIONS": kit.get("PACS_SUPPORTS_EXTENDED_NEGOTIATIONS", "true"),
        "PACS_AVAILABILITY_DAYS": kit.get("PACS_AVAILABILITY_DAYS", "MONDAY,TUESDAY,WEDNESDAY,THURSDAY,FRIDAY"),
        "PACS_AVAILABILITY_START": kit.get("PACS_AVAILABILITY_START", "00:00"),
        "PACS_AVAILABILITY_END": kit.get("PACS_AVAILABILITY_END", "24:00"),
        "PACS_THREADS": kit.get("PACS_THREADS", "1"),
        "PACS_UTILIZATION_PERCENT": kit.get("PACS_UTILIZATION_PERCENT", "100"),
        "DQR_MAX_PACS_REQUEST_ATTEMPTS": kit.get("DQR_MAX_PACS_REQUEST_ATTEMPTS", "100"),
        "DQR_RETRY_WAIT_SECONDS": kit.get("DQR_RETRY_WAIT_SECONDS", "300"),
    }


def _xnat_web(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    dicom_port = int(kit.get("XNAT_PORT", "8104"))
    xnat_home = kit.get("XNAT_HOME", "").strip() or "/data/xnat/home"
    xnat_root = kit.get("XNAT_ROOT", "").strip() or "/data/xnat"
    db_name = kit.get("XNAT_DATASOURCE_NAME", "").strip() or "xnat"
    env = {
        "XNAT_HOME": xnat_home,
        "XNAT_ROOT": xnat_root,
        "XNAT_EMAIL": kit.get("XNAT_EMAIL", ""),
        "TRUST_NAME": kit.get("TRUST_NAME", names.code),
        "XNAT_ADMIN_USER": kit.get("XNAT_ADMIN_USER", "admin"),
        "XNAT_SERVICE_USER": kit.get("XNAT_SERVICE_USER", ""),
        "XNAT_PORT": str(dicom_port),
        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
        **_pacs_env(kit, names),
        "XNAT_DATASOURCE_DRIVER": kit.get("XNAT_DATASOURCE_DRIVER", "org.postgresql.Driver"),
        "XNAT_DATASOURCE_URL": f"jdbc:postgresql://{names.dns('xnat-db')}:5432/{db_name}",
        "XNAT_DATASOURCE_NAME": db_name,
        "XNAT_DATASOURCE_USERNAME": kit.get("XNAT_DATASOURCE_USERNAME", "xnat"),
        # The entrypoint's wait used to be hardcoded to the compose name `xnat-db`, which
        # resolves nowhere here. It now reads the JDBC URL above, and this makes the host
        # explicit so the spec says out loud which database the service waits for (FLIP#1583).
        "XNAT_DB_HOST": names.dns("xnat-db"),
        "XNAT_DB_PORT": "5432",
        # make-xnat-config.sh's convenience copy of the rendered configuration. Its default
        # (/usr/local/share/xnat) is root-owned and this container runs as uid 1001, so it
        # is pointed at the writable cache volume instead (FLIP#1583).
        "XNAT_CONFIG_SHARE_DIR": f"{xnat_root}/cache/config",
        "XNAT_SMTP_ENABLED": kit.get("XNAT_SMTP_ENABLED", "false"),
        # The Container Service plugin is not installed on SPCS: there is no Docker
        # socket and no swarm. dcm2niix runs as a job service instead (FLIP#1578).
        "XNAT_CONTAINER_SERVICE_ENABLED": "false",
        "CATALINA_OPTS": (
            f"-Xms{kit.get('XNAT_MIN_HEAP', '1g')} -Xmx{kit.get('XNAT_MAX_HEAP', '4g')} "
            f"-Dxnat.home={xnat_home} -XX:+UseG1GC -Djava.awt.headless=true"
        ),
    }
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "xnat-web",
                    "image": _image(names, "xnat-web", kit, digests),
                    "env": env,
                    "resources": _resources("2", "6Gi", "4", "12Gi"),
                    "volumeMounts": [
                        {"name": "xnat-archive", "mountPath": f"{xnat_root}/archive"},
                        {"name": "converter-io", "mountPath": f"{xnat_root}/build"},
                        {"name": "xnat-cache", "mountPath": f"{xnat_root}/cache"},
                    ],
                    "secrets": _secrets("xnat-web", names),
                }
            ],
            "endpoints": [
                {"name": "web", "port": 8080, "protocol": "HTTP", "public": False},
                # The DICOM SCP receiver. TCP, not HTTP: the PACS C-STOREs into it, and
                # DQR matches the C-MOVE destination against this exact port.
                {"name": "dicom", "port": dicom_port, "protocol": "TCP", "public": False},
            ],
            "volumes": [
                {"name": "xnat-archive", "source": "stage", "stageConfig": {"name": names.stage_ref("XNAT_ARCHIVE")}},
                {"name": "converter-io", "source": "stage", "stageConfig": {"name": names.stage_ref("CONVERTER_IO")}},
                # Scratch only: the prefetch cache is rebuilt, never read by anything else.
                {"name": "xnat-cache", "source": "local"},
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("xnat-web", "service", names.pool, spec)


def _xnat_db(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    size = kit.get("XNAT_DB_VOLUME_SIZE", "").strip() or "100Gi"
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "xnat-db",
                    "image": _image(names, "xnat-db", kit, digests),
                    # Raised above the Postgres default: a bulk DQR import opens many
                    # concurrent build connections and exhausted the pool (FLIP#662).
                    "args": ["postgres", "-c", "max_connections=300", "-c", "shared_buffers=512MB"],
                    "env": {
                        "POSTGRES_DB": kit.get("XNAT_DATASOURCE_NAME", "").strip() or "xnat",
                        "POSTGRES_USER": kit.get("XNAT_DATASOURCE_USERNAME", "xnat"),
                        "PGDATA": "/var/lib/postgresql/data/pgdata",
                    },
                    "resources": _resources("1", "4Gi", "2", "8Gi"),
                    "volumeMounts": [{"name": "pgdata", "mountPath": "/var/lib/postgresql/data"}],
                    "secrets": _secrets("xnat-db", names),
                }
            ],
            "endpoints": [{"name": "postgres", "port": 5432, "protocol": "TCP", "public": False}],
            "volumes": [
                {
                    "name": "pgdata",
                    "source": "block",
                    "size": size,
                    "blockConfig": {"encryption": "SNOWFLAKE_SSE", "snapshotOnDelete": True},
                }
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("xnat-db", "service", names.pool, spec)


def _xnat_nginx(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "xnat-nginx",
                    "image": _image(names, "xnat-nginx", kit, digests),
                    "env": {"XNAT_WEB_HOST": names.dns("xnat-web"), "XNAT_WEB_PORT": "8080"},
                    "resources": _resources("0.25", "512Mi", "0.5", "1Gi"),
                }
            ],
            "endpoints": [{"name": "web", "port": 80, "protocol": "HTTP", "public": False}],
            **_event_table_exports(),
        }
    }
    return Rendered("xnat-nginx", "service", names.pool, spec)


def _orthanc(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    size = kit.get("ORTHANC_VOLUME_SIZE", "").strip() or "200Gi"
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "orthanc",
                    "image": _image(names, "orthanc", kit, digests),
                    "env": {
                        "ORTHANC_USERNAME": kit.get("ORTHANC_USERNAME", "admin"),
                        # The modality XNAT C-STOREs back to, by derived DNS name.
                        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
                        "XNAT_HOST": names.dns("xnat-web"),
                        "XNAT_PORT": kit.get("XNAT_PORT", "8104"),
                    },
                    "resources": _resources("1", "2Gi", "2", "4Gi"),
                    "volumeMounts": [{"name": "orthanc-storage", "mountPath": "/var/lib/orthanc/db"}],
                    "secrets": _secrets("orthanc", names),
                }
            ],
            "endpoints": [
                {"name": "http", "port": 8042, "protocol": "HTTP", "public": False},
                {"name": "dicom", "port": 4242, "protocol": "TCP", "public": False},
            ],
            "volumes": [
                {
                    "name": "orthanc-storage",
                    "source": "block",
                    "size": size,
                    "blockConfig": {"encryption": "SNOWFLAKE_SSE", "snapshotOnDelete": True},
                }
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("orthanc", "service", names.pool, spec)


def _dcm2niix(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """The NIfTI converter, as an SPCS job service on the job pool (FLIP#1578).

    It replaces XNAT's Container Service launch, which needed a Docker socket. It holds
    no secrets, exposes nothing, and sees only the archive it reads and the I/O tree it
    writes.
    """
    xnat_root = kit.get("XNAT_ROOT", "").strip() or "/data/xnat"
    spec = {
        "spec": {
            "containers": [
                {
                    "name": "dcm2niix",
                    "image": _image(names, "xnat-dcm2niix", kit, digests),
                    "env": {"XNAT_ROOT": xnat_root, "CONVERTER_IO_DIR": "/converter-io"},
                    "resources": _resources("1", "2Gi", "2", "4Gi"),
                    "volumeMounts": [
                        {"name": "xnat-archive", "mountPath": f"{xnat_root}/archive"},
                        {"name": "converter-io", "mountPath": "/converter-io"},
                    ],
                }
            ],
            "volumes": [
                {"name": "xnat-archive", "source": "stage", "stageConfig": {"name": names.stage_ref("XNAT_ARCHIVE")}},
                {"name": "converter-io", "source": "stage", "stageConfig": {"name": names.stage_ref("CONVERTER_IO")}},
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("dcm2niix", "job", names.job_pool, spec)


def _script(name: str) -> str:
    """One job-service script, read from :data:`SCRIPTS_DIR` at render time.

    Raises:
        FileNotFoundError: The script is missing, which would otherwise produce a spec
            whose container exits immediately with an empty command.
    """
    path = SCRIPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"job-service script {path} is missing")
    return path.read_text()


def _job_container(
    name: str,
    image: str,
    script: str,
    env: dict[str, str],
    names: Names,
    *,
    cpu: str = "0.5",
    memory: str = "1Gi",
) -> dict[str, Any]:
    """A one-shot container that runs an inlined bash script.

    ``bash -c`` with the script as the argument rather than a path: a job service has no
    way to fetch a file that is not in its image or on a mounted stage, and inlining keeps
    the rendered specification the single artefact an operator deploys and reviews.
    """
    return {
        "name": name,
        "image": image,
        "command": ["/bin/bash", "-c"],
        "args": [script],
        "env": env,
        "resources": _resources(cpu, memory, cpu, memory),
        "secrets": _secrets(name, names),
    }


def _xnat_configure(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """configure-xnat.sh as a one-shot job service (FLIP#1583).

    The Compose ``make -C trust/xnat xnat-configure`` and the Helm chart's xnat-init hook,
    on SPCS. Until it has run, XNAT has registered no DICOM receiver and rejects every
    C-STORE, so this is not optional on a new deploy: ``make up`` leaves a site that
    answers HTTP and retrieves nothing.
    """
    env = {
        "XNAT_URL": f"http://{names.dns('xnat-web')}:8080",
        "XNAT_ADMIN_USER": kit.get("XNAT_ADMIN_USER", "admin"),
        "XNAT_SERVICE_USER": kit.get("XNAT_SERVICE_USER", ""),
        "XNAT_PORT": kit.get("XNAT_PORT", "8104"),
        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
        # XNAT's factory password unless the kit names the one in force. Not a secret
        # object: it is the published default of an uninitialised site, and a kit that
        # carries a real rotated value should put it in XNAT_ADMIN_PASSWORD instead.
        "XNAT_ADMIN_INITIAL_PASSWORD": kit.get("XNAT_ADMIN_INITIAL_PASSWORD", "").strip() or "admin",
        "XNAT_AUTH_PROBE_ATTEMPTS": "1",
        **_pacs_env(kit, names),
    }
    spec = {
        "spec": {
            "containers": [
                _job_container(
                    "xnat-configure",
                    _image(names, "xnat-web", kit, digests),
                    _script("xnat-configure-spcs.sh"),
                    env,
                    names,
                )
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("xnat-configure", "job", names.job_pool, spec)


def _seed_orthanc(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """The mocked PACS seeded from the SEED stage (FLIP#1583).

    Mirrors ``make -C trust seed-orthanc``, minus the half of it that cannot exist here: a
    Snowflake trust has no egress to Hugging Face and no host to stage a download on, so
    the DICOM arrives on @STAGES.SEED and this job uploads what is there. SEED is mounted
    read-only: seeding reads the corpus, it never rewrites it.

    Runs from the xnat-web image because it is the one mirrored image with bash, curl and
    jq; it touches nothing of XNAT's.
    """
    env = {
        "ORTHANC_URL": f"http://{names.dns('orthanc')}:8042",
        "ORTHANC_USERNAME": kit.get("ORTHANC_USERNAME", "admin"),
        "SEED_DIR": "/seed",
        "PROJECTS": kit.get("SEED_PROJECTS", ""),
        "SEED_DRY_RUN": "false",
    }
    container = _job_container(
        "seed-orthanc", _image(names, "xnat-web", kit, digests), _script("seed-orthanc-spcs.sh"), env, names
    )
    container["volumeMounts"] = [{"name": "seed", "mountPath": "/seed"}]
    spec = {
        "spec": {
            "containers": [container],
            "volumes": [{"name": "seed", "source": "stage", "stageConfig": {"name": names.stage_ref("SEED")}}],
            **_event_table_exports(),
        }
    }
    return Rendered("seed-orthanc", "job", names.job_pool, spec, read_only_volumes=["seed"])


def _smoke_cstore(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """A real C-STORE through the PACS, checked at the receiver (FLIP#1583).

    The Snowflake twin of the Helm chart's ``smoke-cstore``. A C-ECHO completes inside the
    association layer and never reaches XNAT's importer, so it passes while every store
    aborts (FLIP#1228); this drives the path the plugins are actually on.

    The XNAT_ARCHIVE stage is mounted read-only. A smoke test that could write to the
    archive could manufacture its own evidence.
    """
    env = {
        "ORTHANC_URL": f"http://{names.dns('orthanc')}:8042",
        "ORTHANC_USERNAME": kit.get("ORTHANC_USERNAME", "admin"),
        "XNAT_URL": f"http://{names.dns('xnat-web')}:8080",
        "XNAT_SERVICE_USER": kit.get("XNAT_SERVICE_USER", ""),
        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
        "ARCHIVE_DIR": "/archive",
    }
    container = _job_container(
        "smoke-cstore", _image(names, "xnat-web", kit, digests), _script("smoke-cstore-spcs.sh"), env, names
    )
    container["volumeMounts"] = [{"name": "xnat-archive", "mountPath": "/archive"}]
    spec = {
        "spec": {
            "containers": [container],
            "volumes": [
                {"name": "xnat-archive", "source": "stage", "stageConfig": {"name": names.stage_ref("XNAT_ARCHIVE")}}
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("smoke-cstore", "job", names.job_pool, spec, read_only_volumes=["xnat-archive"])


def _reconvert(kit: dict[str, str], names: Names, digests: dict[str, str]) -> Rendered:
    """``reconvert-nifti`` as a job service, on the Snowflake conversion path (FLIP#1583).

    The same ``python -m imaging_api.scripts.reconvert_nifti`` the Compose and Helm trusts
    run, with CONVERTER_BACKEND=snowflake so it calls APP.CONVERT_SCAN instead of XNAT's
    Container Service bulk launcher. It needs imaging-api's own environment because it is
    imaging-api's code: the same XNAT credentials, the same converter settings, and the
    session token that is its database credential.

    It mounts CONVERTER_IO read-write (it reads the NIfTI the dcm2niix job wrote, and the
    job writes there) and the archive read-only.
    """
    xnat_root = kit.get("XNAT_ROOT", "").strip() or "/data/xnat"
    env = {
        "ENV": "production",
        "LOG_LEVEL": _log_level(kit),
        "XNAT_URL": f"http://{names.dns('xnat-web')}:8080",
        "XNAT_PORT": kit.get("XNAT_PORT", "8104"),
        "XNAT_AETITLE": kit.get("XNAT_AETITLE", "").strip() or "XNAT",
        "XNAT_SERVICE_USER": kit.get("XNAT_SERVICE_USER", ""),
        "BASE_IMAGES_DOWNLOAD_DIR": "/app/data/images",
        "DATA_ACCESS_API_URL": f"http://{names.dns('data-access-api')}:8000",
        **_converter_env(kit, names),
    }
    container = _job_container(
        "reconvert",
        _image(names, "imaging-api", kit, digests),
        "set -euo pipefail\npython -m imaging_api.scripts.reconvert_nifti ${RECONVERT_ARGS:---launch}\n",
        env,
        names,
        cpu="1",
        memory="2Gi",
    )
    container["mountSessionToken"] = True
    container["volumeMounts"] = [
        {"name": "converter-io", "mountPath": "/converter-io"},
        {"name": "xnat-archive", "mountPath": f"{xnat_root}/archive"},
    ]
    spec = {
        "spec": {
            "containers": [container],
            "volumes": [
                {"name": "converter-io", "source": "stage", "stageConfig": {"name": names.stage_ref("CONVERTER_IO")}},
                {"name": "xnat-archive", "source": "stage", "stageConfig": {"name": names.stage_ref("XNAT_ARCHIVE")}},
            ],
            **_event_table_exports(),
        }
    }
    return Rendered("reconvert", "job", names.job_pool, spec, read_only_volumes=["xnat-archive"])


def fl_nets(kit: dict[str, str]) -> list[str]:
    """The FL networks this trust runs a client for.

    ``FL_NETS`` in the kit, as a comma-separated list of net numbers; a kit that names
    none runs net-1, which is what every shipped Compose overlay does.
    """
    raw = kit.get("FL_NETS", "").strip() or "1"
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_all(kit: dict[str, str], code: str, digests: dict[str, str] | None = None) -> list[Rendered]:
    """Render every service in the naming contract for one trust.

    Args:
        kit: The trust's kit file as a mapping. No value from it is a secret in the
            rendered output; credentials reach containers only as SECRET objects.
        code: The trust CODE.
        digests: Image repository -> ``sha256:...`` digest, from ``make mirror-images``.

    Returns:
        list[Rendered]: One entry per service, in deploy order (dependencies first).
    """
    names = Names(code)
    digests = digests or {}
    rendered = [
        _xnat_db(kit, names, digests),
        _xnat_web(kit, names, digests),
        _xnat_nginx(kit, names, digests),
        _orthanc(kit, names, digests),
        _data_access_api(kit, names, digests),
        _imaging_api(kit, names, digests),
        _trust_api(kit, names, digests),
    ]
    rendered += [_fl_client(kit, names, digests, net) for net in fl_nets(kit)]
    # Job services last: none of them is "up", each is executed on demand, and every one
    # of them needs the services above to already exist (FLIP#1583).
    rendered += [
        _dcm2niix(kit, names, digests),
        _xnat_configure(kit, names, digests),
        _seed_orthanc(kit, names, digests),
        _smoke_cstore(kit, names, digests),
        _reconvert(kit, names, digests),
    ]
    return rendered
