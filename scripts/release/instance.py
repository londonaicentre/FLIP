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
"""Bring up an isolated FLIP instance for a release's gates, built entirely from this checkout (FLIP#1608).

``make release-instance TAG=v0.13.0 [INSTANCE=rel0130] [PORT_OFFSET=10000] [PR=<n>] [FORCE=1]``

1. Refuses a dirty checkout, or one that is not the release PR's head when ``PR`` is given.
2. Writes the hub env file ``.env.<INSTANCE>.development`` and each dev trust kit from the shipped examples:
   every host port moved by ``PORT_OFFSET``, the instance's own hub URLs, every image tag set to the instance
   name (never the shared ``:dev`` / ``:stag``), and the example's placeholder secrets minted. Secret values
   are never printed.
3. Provisions both FL backends, builds the FL images at the instance tag, fetches the XNAT plugins, and runs
   ``make up BUILD=true`` so every repo-built image (hub, trusts, omop-db, XNAT, FL) comes from this commit.

``--write-only`` stops after step 2. Run ``make release-verify`` afterwards.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_lib import (  # noqa: E402
    REPO,
    ReleaseError,
    assert_at_pr_head,
    assert_clean,
    default_instance,
    documented_value,
    env_file_name,
    instance_env,
    instance_make_args,
    placeholders,
    read_env,
    set_env,
)

# Hub host ports (deploy/AGENTS.md § Multi-instance). Each is global to the host, so a second stack moves them all.
HUB_HOST_PORTS = (
    "UI_PORT",
    "API_PORT",
    "DB_PORT",
    "API_DEBUG_PORT",
    "PGADMIN_PORT",
    "FL_API_PORT",
    "FL_SERVER_PORT",
    "FL_API_DEBUG_PORT",
    "FLOWER_SUPERLINK_NET_1_PORT",
    "FLOWER_SUPERLINK_NET_2_PORT",
    "KEYCLOAK_PORT",
    "OBJECT_STORE_PORT",
    "OBJECT_STORE_CONSOLE_PORT",
)
# Per-trust host ports in a kit; the kit's other *_PORT values are container-side and stay put.
TRUST_HOST_PORTS = (
    "OMOP_DB_PORT",
    "PACS_UI_PORT",
    "XNAT_PORT",
    "XNAT_WEB_PORT",
    "MONAI_LABEL_PORT",
    "TRUST_DEBUG_PORT",
    "IMAGING_DEBUG_PORT",
    "DATA_ACCESS_DEBUG_PORT",
    "GRAFANA_PORT",
    "LOKI_PORT",
    "MONAI_LABEL_PORT",
)


def _mint() -> dict[str, str]:
    """Fresh values for the secrets the hub example leaves as placeholders."""
    internal_key = secrets.token_urlsafe(32)
    return {
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "AES_KEY_BASE64": base64.b64encode(secrets.token_bytes(32)).decode(),
        "ADMIN_USER_PASSWORD": secrets.token_urlsafe(18),
        "INTERNAL_SERVICE_KEY": internal_key,
        "INTERNAL_SERVICE_KEY_HASH": hashlib.sha256(internal_key.encode()).hexdigest(),
    }


def shift_ports(text: str, keys: tuple[str, ...], offset: int) -> str:
    """Move each key's documented port by ``offset``; a key the example does not carry is an error."""
    for key in keys:
        value = documented_value(text, key)
        if not value or not value.isdigit():
            raise ReleaseError(f"the example carries no numeric default for {key}")
        port = int(value) + offset
        if not 1024 <= port <= 65535:
            raise ReleaseError(f"{key}={value} + PORT_OFFSET={offset} is outside 1024-65535")
        text = set_env(text, key, str(port))
    return text


def hub_env(example: str, instance: str, offset: int, minted: dict[str, str] | None = None) -> str:
    """The instance's hub env file, from the example text."""
    text = shift_ports(example, HUB_HOST_PORTS, offset)
    api = int(documented_value(text, "API_PORT") or 0)
    keycloak = int(documented_value(text, "KEYCLOAK_PORT") or 0)
    values = {
        "FLIP_INSTANCE": instance,
        "CENTRAL_HUB_API_URL": f"http://localhost:{api}/api",
        "KEYCLOAK_PUBLIC_URL": f"http://localhost:{keycloak}",
        "FLIP_E2E_BASE_URL": f"http://localhost:{api}/api",
        "DOCKER_TAG": instance,
        "DOCKER_FL_REGISTRY": "",
        "DOCKER_FL_TAG": instance,
        **(minted or _mint()),
    }
    for key, value in values.items():
        text = set_env(text, key, value)
    left = placeholders(text)
    if left:
        raise ReleaseError(f"the hub example has placeholders this script does not fill: {', '.join(left)}")
    return text


def trust_kit(example: str, instance: str, offset: int) -> str:
    """A dev trust kit with its host ports moved and omop-db at the instance tag.

    The kit's ``<run-make-...>`` placeholders stay: ``make up`` → ``register-trusts`` fills them on the running hub.
    """
    return set_env(shift_ports(example, TRUST_HOST_PORTS, offset), "OMOP_DB_TAG", instance)


def kit_examples(repo: Path = REPO) -> dict[Path, Path]:
    """Each dev kit's path, keyed to the example it is written from."""
    return {
        example.with_name(example.name.removesuffix(".example")): example
        for example in sorted((repo / "trust").glob(".env.*.development.example"))
    }


def foreign_kits(instance: str, repo: Path = REPO) -> list[Path]:
    """Dev kits on disk that this instance did not write (its kits pin ``OMOP_DB_TAG`` to it).

    The kits carry no instance in their name, so they are the files a developer's own trusts use
    (``trust/.env.<KIT>.development``); overwriting one loses the credentials ``register-trust`` minted.
    """
    return [p for p in kit_examples(repo) if p.exists() and read_env(p).get("OMOP_DB_TAG") != instance]


def write_files(instance: str, offset: int, force: bool, repo: Path = REPO) -> list[Path]:
    """Write the hub env file and every dev kit; refuse to overwrite one unless ``force`` (it may be someone's)."""
    targets = {
        repo / env_file_name(instance): hub_env((repo / ".env.development.example").read_text(), instance, offset)
    }
    for kit, example in kit_examples(repo).items():
        targets[kit] = trust_kit(example.read_text(), instance, offset)
    existing = [p for p in targets if p.exists()]
    if existing and not force:
        names = ", ".join(str(p.relative_to(repo)) for p in existing)
        raise ReleaseError(f"refusing to overwrite {names} (FORCE=1 replaces them)")
    for path, text in targets.items():
        path.write_text(text)
        path.chmod(0o600)
        print(f"📝 wrote {path.relative_to(repo)}")
    return list(targets)


def bring_up(instance: str, repo: Path = REPO) -> None:
    """Provision, build every image at the instance tag and start the stack, each step through its make target."""
    args = instance_make_args(instance)
    steps = [
        ["make", "-C", "fl-services/nvflare", "provision-2-nets"],
        ["make", "-C", "fl-services/flower", "provision", "NET_NUMBER=1"],
        ["make", "-C", "fl-services/flower", "provision", "NET_NUMBER=2"],
        ["make", "build-fl", "FL_BACKEND=nvflare", f"DOCKER_FL_TAG={instance}"],
        ["make", "build-fl", "FL_BACKEND=flower", f"DOCKER_FL_TAG={instance}"],
        ["make", "-C", "trust", "prepare-artifacts"],
        ["make", "up", "BUILD=true", "FL_BACKEND=nvflare", *args],
    ]
    env = instance_env(instance, repo)
    for step in steps:
        run_step(step, repo, env)


# Image builds and artefact downloads are safe to repeat, and on a shared host BuildKit or the registry can
# fail transiently ("frontend grpc server closed unexpectedly"), so they get one retry. Nothing else does.
RETRY_ONCE = ("build-fl", "prepare-artifacts")


def run_step(step: list[str], repo: Path, env: dict[str, str]) -> None:
    """Run one make step, failing on a non-zero exit; a retryable step gets a second attempt first."""
    print(f"▶ {' '.join(step)}", flush=True)
    first = subprocess.run(step, cwd=repo, env=env)
    if first.returncode == 0:
        return
    if not any(target in step for target in RETRY_ONCE):
        raise subprocess.CalledProcessError(first.returncode, step)
    print(f"↻ retrying once: {' '.join(step)}", flush=True)
    subprocess.run(step, cwd=repo, env=env, check=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--instance", default="")
    parser.add_argument("--port-offset", type=int, default=10000)
    parser.add_argument("--pr", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--write-only", action="store_true")
    a = parser.parse_args(argv)
    try:
        instance = a.instance or default_instance(a.tag)
        assert_clean()
        sha = assert_at_pr_head(a.pr or None)
        print(f"🏷  {a.tag} at {sha[:9]} → instance {instance}, ports +{a.port_offset}")
        write_files(instance, a.port_offset, a.force)
        if not a.write_only:
            bring_up(instance)
            print(f"✅ {instance} is up. Next: make release-verify TAG={a.tag} INSTANCE={instance}")
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
