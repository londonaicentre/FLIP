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
"""The chart's pre-upgrade dump hook, run for real against two released xnat-db images (FLIP#2071).

Everything else about the hook is checked against a render or a stand-in API server. This is the
part that cannot be: whether the file the dump script leaves on the volume is the file the *next*
image's entrypoint accepts and restores — its name, its place, its owner, and its age relative to
the old server's ``postmaster.opts``. The two images are released artefacts rather than fixtures:
``v0.14.0`` keeps a PostgreSQL 12 cluster at the volume root, ``v0.16.0`` keeps PostgreSQL 16 in
``pg16/`` below it and restores ``xnat-migrate.dump`` on first initdb (FLIP#1812).

So the sequence here is the upgrade, without Kubernetes: old server up with rows in it, the hook's
decide and dump scripts run from the NEW image over the network, old server stopped, new image
started on the same directory — and the rows have to be there.

Skipped without a reachable Docker daemon or the images (an air-gapped checkout); set
``FLIP_SKIP_DOCKER_TESTS=1`` to skip it anyway.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "trust" / "deploy" / "helm" / "config"
OLD_IMAGE = os.environ.get("FLIP_XNAT_DB_OLD_IMAGE", "ghcr.io/londonaicentre/xnat-db:v0.14.0")
NEW_IMAGE = os.environ.get("FLIP_XNAT_DB_NEW_IMAGE", "ghcr.io/londonaicentre/xnat-db:v0.16.0")
# Long enough for a password guard to be satisfied and obviously a fixture.
SUPERUSER_PASSWORD = "Fl1p-Test-Superuser-Pw"  # pragma: allowlist secret — test fixture only
APP_PASSWORD = "Fl1p-Test-Datasource-Pw"  # pragma: allowlist secret — test fixture only
ROWS = [("ACC-001", "spleen"), ("ACC-002", "prostate")]


def _run(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=check, timeout=600, **kwargs)


def _docker_available() -> bool:
    if os.environ.get("FLIP_SKIP_DOCKER_TESTS") == "1" or shutil.which("docker") is None:
        return False
    return _run("docker", "info", "--format", "{{.ServerVersion}}", check=False).returncode == 0


def _images_available() -> bool:
    for image in (OLD_IMAGE, NEW_IMAGE):
        if _run("docker", "image", "inspect", image, check=False).returncode != 0 and (
            _run("docker", "pull", "-q", image, check=False).returncode != 0
        ):
            return False
    return True


pytestmark = [
    pytest.mark.skipif(not _docker_available(), reason="no reachable Docker daemon"),
    pytest.mark.skipif(not _images_available(), reason="the released xnat-db images are not available"),
]


class _Stack:
    """A docker network, a data directory and the containers started on them."""

    def __init__(self, tmp_path: Path) -> None:
        self.tag = f"flip-2071-{uuid.uuid4().hex[:8]}"
        self.data = tmp_path / "xnat-db-data"
        self.data.mkdir()
        # The containers run as uid 70; this directory stands in for a volume root they own.
        self.data.chmod(0o777)
        # The Job's emptyDir: how the three containers hand over to each other.
        self.plan = tmp_path / "plan"
        self.plan.mkdir()
        self.plan.chmod(0o777)
        self.containers: list[str] = []
        _run("docker", "network", "create", self.tag)

    def close(self) -> None:
        for name in self.containers:
            _run("docker", "rm", "-f", name, check=False)
        _run("docker", "network", "rm", self.tag, check=False)
        # initdb leaves the data directory 0700 and owned by the image's postgres user, which the
        # host user can then neither read nor remove — pytest's tmp_path cleanup would warn about
        # it on every later run. Emptied and handed back from inside a container.
        _run(
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{self.data}:/data",
            NEW_IMAGE,
            "-c",
            "rm -rf /data/..?* /data/.[!.]* /data/*; chmod 0777 /data",
            check=False,
        )

    def server(self, image: str, name: str) -> str:
        container = f"{self.tag}-{name}"
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--network",
            self.tag,
            "--network-alias",
            "xnat-db",
            "-v",
            f"{self.data}:/var/lib/postgresql/data",
            "-e",
            f"POSTGRES_PASSWORD={SUPERUSER_PASSWORD}",
            "-e",
            "POSTGRES_DB=xnat",
            "-e",
            "XNAT_DATASOURCE_USERNAME=xnat",
            "-e",
            f"XNAT_DATASOURCE_PASSWORD={APP_PASSWORD}",
            image,
        )
        self.containers.append(container)
        return container

    def wait_ready(self, container: str, seconds: int = 120) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            # Over TCP, not the socket: during initdb the entrypoint runs a temporary server with
            # listen_addresses='' (and, on a first start after a major move, runs the restore on
            # it), so only a TCP probe means the real server is up.
            probe = _run(
                "docker",
                "exec",
                container,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "postgres",
                "-d",
                "xnat",
                check=False,
            )
            if probe.returncode == 0:
                return
            if _run("docker", "inspect", "-f", "{{.State.Running}}", container, check=False).stdout.strip() != "true":
                break
            time.sleep(1)
        logs = _run("docker", "logs", "--tail", "40", container, check=False)
        pytest.fail(f"{container} never became ready:\n{logs.stdout}\n{logs.stderr}")

    def sh(self, command: str) -> str:
        """Look at the volume from inside a container as uid 70.

        The cluster directory is 0700 and owned by the image's postgres user, so the host user
        running pytest cannot even stat what the hook wrote — and on a real node, nor should it.
        """
        result = _run(
            "docker",
            "run",
            "--rm",
            "--user",
            "70:70",
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{self.data}:/var/lib/postgresql/data",
            NEW_IMAGE,
            "-c",
            command,
            check=False,
        )
        return result.stdout.strip()

    def psql(self, container: str, sql: str) -> str:
        return _run("docker", "exec", container, "psql", "-XqAt", "-U", "postgres", "-d", "xnat", "-c", sql).stdout

    def hook(self, script: str, *env: str) -> subprocess.CompletedProcess[str]:
        """Run one of the hook's scripts from the NEW image, as the Job's pod does."""
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.tag,
            "--user",
            "70:70",
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{self.data}:/var/lib/postgresql/data",
            "-v",
            f"{CONFIG_DIR}:/scripts:ro",
            "-e",
            "PLAN_DIR=/plan-dir",
            "-e",
            "PGHOST=xnat-db",
            "-e",
            "POSTGRES_DB=xnat",
            "-e",
            "XNAT_DATASOURCE_USERNAME=xnat",
            "-e",
            f"POSTGRES_PASSWORD={SUPERUSER_PASSWORD}",
            "-e",
            f"XNAT_DATASOURCE_PASSWORD={APP_PASSWORD}",
            "-v",
            f"{self.plan}:/plan-dir",
        ]
        for item in env:
            args += ["-e", item]
        args += [NEW_IMAGE, f"/scripts/{script}"]
        return _run(*args, check=False)


@pytest.fixture
def stack(tmp_path: Path):
    stack = _Stack(tmp_path)
    try:
        yield stack
    finally:
        stack.close()


def test_the_hook_leaves_a_dump_the_next_image_restores(stack):
    """The whole point, end to end: PostgreSQL 12 rows survive into the PostgreSQL 16 cluster."""
    old = stack.server(OLD_IMAGE, "old")
    stack.wait_ready(old)
    assert stack.psql(old, "show server_version").startswith("12"), "the old image must be the older major"
    stack.psql(old, "create table accession (id text primary key, part text)")
    for accession, part in ROWS:
        stack.psql(old, f"insert into accession values ('{accession}', '{part}')")

    decide = stack.hook("xnat-db-migrate-decide.sh")
    assert decide.returncode == 0, decide.stderr
    assert (stack.plan / "plan").read_text().strip() == "dump", decide.stdout
    assert "PostgreSQL 12 to 16" in decide.stdout

    # The scaler's half, which has no cluster here: xnat-web is stopped, so the dump may start.
    (stack.plan / "go").write_text("")
    dump = stack.hook("xnat-db-migrate-dump.sh")
    assert dump.returncode == 0, f"{dump.stdout}\n{dump.stderr}"
    assert (stack.plan / "dump-result").read_text().strip() == "ok"
    root = "/var/lib/postgresql/data"
    owner, size = stack.sh(f'stat -c "%u %s" {root}/xnat-migrate.dump').split()
    assert owner == "70", "the new image reads the dump as uid 70 on its first start"
    assert int(size) > 0
    assert stack.sh(f"[ {root}/xnat-migrate.dump -nt {root}/postmaster.opts ] && echo newer") == "newer", (
        "the image's guard refuses a dump older than the last start of the server it came from"
    )

    _run("docker", "stop", old)
    new = stack.server(NEW_IMAGE, "new")
    stack.wait_ready(new)
    assert stack.psql(new, "show server_version").startswith("16")
    restored = stack.psql(new, "select id || ':' || part from accession order by id").split()
    assert restored == [f"{accession}:{part}" for accession, part in ROWS]
    marker = _run("docker", "exec", new, "sh", "-c", 'ls "$PGDATA/xnat-restored"')
    assert marker.stdout.strip().endswith("xnat-restored")
    # The old cluster is kept for a rollback, as on the Makefile path.
    assert stack.sh(f"cat {root}/PG_VERSION") == "12"


def test_the_hook_does_nothing_when_the_cluster_is_already_on_the_image_s_major(stack):
    """An ordinary release upgrade: same major, so no dump and nothing stopped."""
    new = stack.server(NEW_IMAGE, "current")
    stack.wait_ready(new)
    decide = stack.hook("xnat-db-migrate-decide.sh")
    assert decide.returncode == 0, decide.stderr
    assert (stack.plan / "plan").read_text().strip() == "skip"
    dump = stack.hook("xnat-db-migrate-dump.sh")
    assert dump.returncode == 0
    assert stack.sh("ls /var/lib/postgresql/data/xnat-migrate.dump 2>/dev/null || echo none") == "none"


def test_a_dump_that_cannot_be_taken_reports_failure_rather_than_half_a_file(stack):
    """No server to dump: the hook must fail loudly, leaving nothing for the new image to restore."""
    (stack.data / "PG_VERSION").write_text("12\n")
    (stack.plan / "plan").write_text("dump\n")
    (stack.plan / "go").write_text("")
    dump = stack.hook("xnat-db-migrate-dump.sh", "TIMEOUT_SECONDS=30")
    assert dump.returncode == 1
    assert (stack.plan / "dump-result").read_text().strip() == "failed"
    assert stack.sh("ls /var/lib/postgresql/data/xnat-migrate.dump* 2>/dev/null || echo none") == "none"


def test_the_decision_refuses_a_layout_the_restore_does_not_implement(stack):
    """A cluster already inside a pg<major>/ cannot be moved on: the dump would land unread."""
    (stack.data / "pg12").mkdir()
    (stack.data / "pg12" / "PG_VERSION").write_text("12\n")
    decide = stack.hook("xnat-db-migrate-decide.sh")
    assert decide.returncode == 1
    assert "not implemented" in decide.stderr
    assert not (stack.plan / "plan").exists()


def test_the_dump_waits_for_xnat_web_and_gives_up_when_it_cannot_be_stopped(stack):
    """`abort` is how the scaler tells the dump container not to sit out its timeout."""
    (stack.plan / "plan").write_text("dump\n")
    (stack.plan / "abort").write_text("")
    dump = stack.hook("xnat-db-migrate-dump.sh", "TIMEOUT_SECONDS=30")
    assert dump.returncode == 1
    assert "xnat-web was not stopped" in dump.stderr
    assert not (stack.plan / "dump-result").exists()


def test_the_released_images_are_the_two_sides_of_the_move():
    """Guards the fixture itself: a retagged image would make the test above vacuous."""
    majors = {}
    for image in (OLD_IMAGE, NEW_IMAGE):
        config = json.loads(_run("docker", "image", "inspect", image).stdout)[0]["Config"]
        env = dict(item.split("=", 1) for item in config["Env"])
        majors[image] = (env["PG_MAJOR"], env["PGDATA"])
    assert majors[OLD_IMAGE][1] == "/var/lib/postgresql/data", "the old image keeps its cluster at the volume root"
    assert majors[NEW_IMAGE][1].endswith(f"/pg{majors[NEW_IMAGE][0]}")
    assert int(majors[OLD_IMAGE][0]) < int(majors[NEW_IMAGE][0])
