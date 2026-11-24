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
"""Every key in ``trust/.env.example`` is read on each node shape, or listed here as not applicable (FLIP#2047).

A kit key that one shape never reads fails silently: the operator sets it, every command
exits 0, and the service runs on its default. ``EXPECTED_TRUST_ID`` reached trust-api on no
shape for six months while the onboarding checklist printed PASS for it, and the Helm chart
kept its own ``TRUST_API_KEY_HEADER`` whatever the kit said.

A key counts as read on a shape when that shape's kit readers name it: ``${KEY`` in a trust
compose file (Compose), a quoted ``"KEY"`` in the chart's kit scripts (Helm) or in the spec
renderers (Snowflake). A Makefile counts only where it uses the value: it expands the
variable (``$(KEY)``, ``${KEY}``, ``$$KEY``) or reads its line out of the kit (``^KEY=``); a
name in an ``export`` list or a message is not a read. Comment lines do not count. This
proves a shape names the key, not that it passes the kit's value on, so a new key still
needs its own test.

Compose is two shapes: the deployed files (``compose_trust.prod*.yml``) and the development
ones are separate reader sets, so a key wired only in development does not pass for a
deployed trust.

A key no reader names goes in that shape's ``NOT_APPLICABLE`` with the reason. "not wired"
marks a gap rather than a decision: the PR that wires the key removes the entry, and the
guard fails until it does.

Stdlib-only and line-based, run as a plain script by ``test_trust_kit_scripts.yml``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT_EXAMPLE = REPO_ROOT / "trust" / ".env.example"

# A live assignment or a commented-out one (`# KEY=`, `#KEY=`): an optional key is still a key.
KIT_KEY = re.compile(r"^(?:# ?)?([A-Z_][A-Z0-9_]*)=", re.MULTILINE)

# shape -> (glob, pattern a reading line matches, with KEY the key)
COMPOSE_VAR = r"\$\{KEY(?![A-Za-z0-9_])"
MAKE_USE = r"\$[({]KEY[)}]|\$\$\{?KEY(?![A-Za-z0-9_])|\^KEY="
QUOTED = r'"KEY"'
# The XNAT stack file and the two Makefiles serve the deployed and the development trust alike.
COMPOSE_SHARED = (
    ("trust/xnat/docker-compose-stack.yml", COMPOSE_VAR),
    ("trust/Makefile", MAKE_USE),
    ("trust/xnat/Makefile", MAKE_USE),
)
READERS = {
    "compose (prod)": (("trust/deploy/compose/compose_trust.prod*.yml", COMPOSE_VAR), *COMPOSE_SHARED),
    "compose (development)": (
        ("trust/deploy/compose/compose_trust.development*.yml", COMPOSE_VAR),
        ("trust/deploy/compose/compose_trust-1_override.yml", COMPOSE_VAR),
        *COMPOSE_SHARED,
    ),
    "helm": (
        ("trust/deploy/helm/sync_k8s_kit.py", QUOTED),
        ("trust/deploy/helm/scripts/generate_values.py", QUOTED),
        ("trust/deploy/helm/Makefile", MAKE_USE),
    ),
    "snowflake": (
        ("trust/deploy/snowflake/spec_templates.py", QUOTED),
        ("trust/deploy/snowflake/render_specs.py", QUOTED),
        ("trust/deploy/snowflake/sync_snowflake_kit.py", QUOTED),
        # SECRET_KIT_VAR: the kit keys sync_snowflake_kit.py writes as Snowflake SECRETs.
        ("trust/deploy/snowflake/naming.py", QUOTED),
    ),
}

HOST_PORTS = (
    "OMOP_DB_PORT PACS_UI_PORT XNAT_WEB_PORT TRUST_DEBUG_PORT IMAGING_DEBUG_PORT DATA_ACCESS_DEBUG_PORT "
    "GRAFANA_PORT LOKI_PORT DATA_ACCESS_API_PORT IMAGING_API_PORT TRUST_API_PORT TRUST_BIND_ADDRESS"
)
MONAI_LABEL = (
    "MONAI_LABEL MONAI_LABEL_PORT MONAI_LABEL_MODELS MONAI_LABEL_PROJECTS MONAI_LABEL_PUBLIC_URL "
    "MONAI_LABEL_SHM_SIZE MONAI_LABEL_BIND_HOST MONAI_LABEL_AUTH_ENABLE"
)
SNOWFLAKE_OMOP = (
    "OMOP_BACKEND SNOWFLAKE_ACCOUNT SNOWFLAKE_WAREHOUSE SNOWFLAKE_DATABASE SNOWFLAKE_SCHEMA SNOWFLAKE_ROLE "
    "SNOWFLAKE_USER SNOWFLAKE_PRIVATE_KEY_FILE SNOWFLAKE_PRIVATE_KEY_PASSPHRASE SNOWFLAKE_HOST"
)

# shape -> reason -> keys. Keep the reason to a few words.
COMPOSE_BOTH = {
    "Helm only": "OMOP_VOCAB_DIR K8S_PLATFORM_VALUES",
    "no effect off SPCS yet, as trust/.env.example says": SNOWFLAKE_OMOP,
    "the hub's kit prefix; only an EC2 node's S3 kit fetch reads it": "FLOWER_KIT_DATE FLARE_KIT_DATE",
    "not wired (#2047, no stack file reads it on any shape)": "XNAT_ACTIVEMQ_PASSWORD",
}
NOT_APPLICABLE = {
    "compose (prod)": {
        **COMPOSE_BOTH,
        "debugger port; development compose only": "TRUST_DEBUG_PORT IMAGING_DEBUG_PORT DATA_ACCESS_DEBUG_PORT",
    },
    "compose (development)": {
        **COMPOSE_BOTH,
        "it dials the local hub and FL server": "CENTRAL_HUB_API_URL NLB_SUBDOMAIN FL_SERVER_PORT FL_EDGE_PORT",
    },
    "helm": {
        "host port or bind address; the chart publishes Services": HOST_PORTS,
        "host directory; the chart uses volume claims": (
            "ORTHANC_STORAGE_DIR BASE_IMAGES_DOWNLOAD_DIR XNAT_DATA_DIR COHORT_SNAPSHOT_STORAGE_DIR OMOP_DATA_DIR"
        ),
        "container uid; the chart sets its own securityContext": "FLIP_UID FLIP_GID",
        "in-cluster URL the chart derives": "DATA_ACCESS_API_URL IMAGING_API_URL",
        "kit staged by make stage-kit, not fetched by date": "FLOWER_KIT_DATE FLARE_KIT_DATE",
        "the chart takes FL_KIT_SLOT_NUMBER": "FL_KIT_SLOT",
        "read through trust_kit_lib.trust_data_seed, which names it": "TRUST_DATA_SEED",
        "set as a chart value, the kit line is not read": (
            "OMOP_POSTGRES_USER OMOP_POSTGRES_DB DATA_ACCESS_POSTGRES_USER XNAT_ADMIN_USER XNAT_PORT XNAT_AETITLE "
            "PACS_HOST PACS_AETITLE PACS_QR_PORT PACS_LABEL PACS_SUPPORTS_EXTENDED_NEGOTIATIONS "
            "PACS_AVAILABILITY_DAYS PACS_AVAILABILITY_START PACS_AVAILABILITY_END PACS_THREADS "
            "PACS_UTILIZATION_PERCENT DQR_MAX_PACS_REQUEST_ATTEMPTS DQR_RETRY_WAIT_SECONDS POLL_INTERVAL_SECONDS "
            "TRUST_LOG_LEVEL NUM_AVAILABLE_GPUS MEMORY_PER_GPU_IN_GIB " + MONAI_LABEL
        ),
        "no effect off SPCS yet, as trust/.env.example says": SNOWFLAKE_OMOP,
        "not wired (#2047 H10)": "COHORT_REMOVAL_GRACE_HOURS COHORT_RECONCILE_MINUTES IMAGE_CACHE_RETENTION_HOURS",
        "not wired (#2047, no stack file reads it on any shape)": "XNAT_ACTIVEMQ_PASSWORD",
    },
    "snowflake": {
        "host port or bind address; SPCS endpoints": HOST_PORTS,
        "host directory; SPCS volumes and stages": (
            "ORTHANC_STORAGE_DIR XNAT_DATA_DIR COHORT_SNAPSHOT_STORAGE_DIR OMOP_DATA_DIR OMOP_VOCAB_DIR"
        ),
        "container uid; SPCS runs the image's user": "FLIP_UID FLIP_GID",
        "no omop-db container; OMOP is the Snowflake database": (
            "OMOP_POSTGRES_USER OMOP_POSTGRES_PASSWORD OMOP_POSTGRES_DB DATA_ACCESS_POSTGRES_USER "
            "DATA_ACCESS_POSTGRES_PASSWORD OMOP_DB_TAG"
        ),
        "key-pair auth is for off SPCS; the service token carries the identity": (
            "SNOWFLAKE_USER SNOWFLAKE_PRIVATE_KEY_FILE SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
        ),
        "SPCS injects it into every container": "SNOWFLAKE_ACCOUNT SNOWFLAKE_HOST",
        "no Grafana on SPCS; logs go to the event table": "GRAFANA_ADMIN_PASSWORD",
        "MONAI Label is not deployed on SPCS": MONAI_LABEL,
        "Helm only": "K8S_PLATFORM_VALUES",
        "kit uploaded by make sync-kit, not fetched by date": "FLOWER_KIT_DATE FLARE_KIT_DATE",
        "Compose first start only; SPCS sets the admin one": "XNAT_ADMIN_INITIAL_PASSWORD",  # pragma: allowlist secret
        "not wired (#2047 N2)": "FL_SITE_PRIVACY_POLICY FL_SITE_PRIVACY_PERCENTILE FL_SITE_PRIVACY_GAMMA",
        "not wired (#2047, seed-orthanc never reads it)": "TRUST_DATA_SEED",
        "not wired (#2047, no finding ID)": (
            "COHORT_REMOVAL_GRACE_HOURS COHORT_RECONCILE_MINUTES IMAGE_CACHE_RETENTION_HOURS"
        ),
        "not wired (#2047, no stack file reads it on any shape)": "XNAT_ACTIVEMQ_PASSWORD",
    },
}


def kit_keys() -> list[str]:
    return list(dict.fromkeys(KIT_KEY.findall(KIT_EXAMPLE.read_text())))


def reader_text(shape: str) -> list[tuple[str, str]]:
    """Each reader's non-comment lines, with the pattern a reading line matches."""
    texts = []
    for glob, pattern in READERS[shape]:
        paths = sorted(REPO_ROOT.glob(glob))
        if not paths:
            raise ValueError(f"{glob}: no such file, so {shape} cannot be checked (moved?)")
        for path in paths:
            lines = [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]
            texts.append(("\n".join(lines), pattern))
    return texts


def check_shape(shape: str, keys: list[str], failures: list[str]) -> None:
    texts = reader_text(shape)
    not_applicable = {key: reason for reason, names in NOT_APPLICABLE[shape].items() for key in names.split()}
    for key in keys:
        read = any(re.search(pattern.replace("KEY", re.escape(key)), text) for text, pattern in texts)
        if not read and key not in not_applicable:
            failures.append(
                f"{shape}: nothing reads the kit key {key}. Wire it ({', '.join(g for g, _ in READERS[shape])}), "
                f"or add it to NOT_APPLICABLE['{shape}'] with the reason."
            )
        elif read and not_applicable.get(key, "").startswith("not wired"):
            failures.append(f"{shape}: {key} is now read; remove its '{not_applicable[key]}' entry.")
        elif read and key in not_applicable:
            print(f"  note: {shape}: {key} is now read; drop it from NOT_APPLICABLE ('{not_applicable[key]}')")
    for key in sorted(set(not_applicable) - set(keys)):
        failures.append(f"{shape}: NOT_APPLICABLE lists {key}, which trust/.env.example no longer declares.")


def main() -> int:
    failures: list[str] = []
    try:
        keys = kit_keys()
        if len(keys) < 50:
            raise ValueError(f"{KIT_EXAMPLE}: only {len(keys)} keys found, the parser is broken or the file moved")
        for shape in READERS:
            check_shape(shape, keys, failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ kit key coverage guard failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ kit key coverage guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
