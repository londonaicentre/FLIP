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
"""trust/xnat/Makefile's in-place upgrade: where the directories are checked, and a backup that only
counts when complete (FLIP#1204).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))

from make_dry_run import KIT, dry_run, kit_with, main, run_target  # noqa: E402


class UpgradeXnat(unittest.TestCase):
    recipe: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.recipe = dry_run("upgrade-xnat", subdir="trust/xnat")

    def test_upgrade_xnat_checks_ownership_where_the_directories_are(self):
        """With a remote (ssh://) docker context the XNAT dirs live on the trust host (FLIP#1204).

        An EC2 node's upgrade drives upgrade-xnat from the admin workstation, so a bare `stat -c` reads
        this machine's filesystem (and fails outright on BSD stat) instead of the trust host's —
        the same split xnat-reset already makes.
        """
        out = self.recipe
        assert "ssh://*)" in out, f"upgrade-xnat has no remote-context branch:\n{out}"
        assert 'ssh "$xnat_ctx" "stat -c' in out, f"the remote branch must stat over ssh:\n{out}"
        assert "Fix: ssh $xnat_ctx sudo chown" in out, f"the remote remedy must be runnable on the host:\n{out}"

    def test_upgrade_xnat_creates_the_mount_sources_before_checking_them(self):
        """A site installed before FLIP#1818 has no prearchive dir; Swarm fails a task whose mount source is missing."""
        out = self.recipe
        assert out.index("xnat-web-dirs KIT=SCR") < out.index("owned="), out

    def test_upgrade_xnat_backs_up_before_redeploying(self):
        out = self.recipe
        assert "xnat-backup KIT=SCR" in out, out
        assert out.index("xnat-backup KIT=SCR") < out.index("docker stack deploy"), out

    def test_upgrade_xnat_takes_the_migration_dump_while_the_old_db_still_runs(self):
        """The new image cannot read the old cluster, so its dump has to exist before the deploy (FLIP#1812)."""
        out = self.recipe
        assert out.index("xnat-backup KIT=SCR") < out.index("xnat-db-migrate-dump KIT=SCR"), out
        assert out.index("xnat-db-migrate-dump KIT=SCR") < out.index("docker stack deploy"), out


# A stand-in for docker, for the xnat-db major-upgrade targets: every call is logged to $CALLS, `ps`
# finds the db, `exec … cat …PG_VERSION` answers $CURRENT, and `exec -i` logs which script came on stdin.
STUB_DOCKER_DB = r"""#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in
  ps) [[ "$*" == *"service.name="*"_xnat-db"* ]] && echo stub-xnat-db; exit 0 ;;
  exec)
    if [ "$2" = -i ]; then
      script=$(cat)
      [[ "$script" == *"pg_dump -Fc"* ]] && echo "stdin: migrate-dump" >> "$CALLS"
      [[ "$script" == *"-exec rm -rf"* ]] && echo "stdin: remove-old-cluster" >> "$CALLS"
      exit 0
    fi
    echo "$CURRENT"; exit 0 ;;
esac
exit 0
"""


class XnatDbMajorUpgrade(unittest.TestCase):
    """xnat-db-migrate-dump and xnat-db-remove-old-cluster, run for real against a stub docker (FLIP#1812)."""

    def _run(self, target: str, *extra: str, current: str = "12") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            (bin_dir / "docker").write_text(STUB_DOCKER_DB)
            (bin_dir / "docker").chmod(0o755)
            calls = Path(tmp) / "calls"
            result = run_target(
                target,
                *extra,
                subdir="trust/xnat",
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls), "CURRENT": current},
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return calls.read_text().splitlines() if calls.exists() else []

    def test_an_older_cluster_is_dumped_after_xnat_web_stops(self):
        calls = self._run("xnat-db-migrate-dump", current="12")
        scale = next(i for i, c in enumerate(calls) if c.startswith("service scale") and c.endswith("_xnat-web=0"))
        dump = next(i for i, c in enumerate(calls) if c.startswith("exec -i stub-xnat-db sh -s 16"))
        assert scale < dump, calls
        assert calls[dump + 1] == "stdin: migrate-dump", calls

    def test_a_cluster_already_on_the_major_is_left_alone(self):
        calls = self._run("xnat-db-migrate-dump", current="16")
        assert not any(c.startswith(("service scale", "exec -i")) for c in calls), calls

    def test_removing_the_old_cluster_needs_yes(self):
        for extra, expected in (
            ((), "exec -i stub-xnat-db sh -s --"),
            (("YES=1",), "exec -i stub-xnat-db sh -s -- --delete"),
        ):
            with self.subTest(extra=extra):
                calls = self._run("xnat-db-remove-old-cluster", *extra)
                assert expected in [c.strip() for c in calls], calls
                assert "stdin: remove-old-cluster" in calls, calls


# A stand-in for the docker CLI, just enough of it for xnat-backup: `ps` finds the db by its service
# label (NO_DB hides it), `exec` is the db container's `pg_dumpall | gzip` (DUMP=ok|fail|truncated),
# `context` is a local daemon, and `run alpine …` runs the command here — under bash, as the
# containers' busybox sh has pipefail and dash does not — with each `-v src:dst` mount rewritten
# to its host path.
STUB_DOCKER = r"""#!/bin/bash
case "$1" in
  context)
    [ "$2" = show ] && echo default
    [ "$2" = inspect ] && echo unix:///var/run/docker.sock
    exit 0 ;;
  ps)
    [ -z "$NO_DB" ] && [[ "$*" == *"service.name="*"_xnat-db"* ]] && echo "stub-xnat-db"
    exit 0 ;;
  exec)
    {
      printf -- '--\n-- PostgreSQL database cluster dump\n--\nCREATE ROLE xnat;\n'
      [ "$DUMP" = truncated ] || printf -- '--\n-- PostgreSQL database cluster dump complete\n--\n\n'
    } | gzip -c
    # fail: the stream looks complete but pg_dumpall's exit status says otherwise — only
    # pipefail can see that, the completion-trailer check cannot.
    [ "$DUMP" = fail ] && { echo "pg_dumpall: error: query failed" >&2; exit 1; }
    exit 0 ;;
  run)
    shift; mounts=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --rm|-i) shift ;;
        --user) [ -n "$RUN_USERS" ] && echo "$2" >> "$RUN_USERS"; shift 2 ;;
        -v) mounts+=("$2"); shift 2 ;;
        alpine:*) shift; break ;;
        *) shift ;;
      esac
    done
    args=()
    for a in "$@"; do
      for m in "${mounts[@]}"; do
        src="${m%%:*}"; dst="${m#*:}"
        a="${a//$dst\//$src/}"; [ "$a" = "$dst" ] && a="$src"
      done
      args+=("$a")
    done
    [ "${args[0]}" = sh ] && args[0]=bash
    exec "${args[@]}" ;;
esac
exit 0
"""


class XnatBackup(unittest.TestCase):
    """xnat-backup, run for real against a stub docker: only a complete dump counts as a backup."""

    def _backup(self, *, locked_backups: bool = False, locked_data: bool = False, extra_kit: str = "", **env: str):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(STUB_DOCKER)
            docker.chmod(0o755)
            data = Path(tmp) / "xnat"
            data.mkdir()
            if locked_backups:  # a backups/ an earlier upgrade made as root: this uid cannot write it
                (data / "backups").mkdir(mode=0o555)
            if locked_data:  # no backups/ yet, in a data dir this uid cannot write
                data.chmod(0o555)
            users = Path(tmp) / "run-users"
            try:
                result = run_target(
                    "xnat-backup",
                    subdir="trust/xnat",
                    kit=kit_with("XNAT_DATA_DIR=/nonexistent/xnat", f"XNAT_DATA_DIR={data}") + extra_kit,
                    env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUN_USERS": str(users), **env},
                )
                backups = data / "backups"
                kept = sorted(b.name for b in backups.glob("*.sql.gz")) if backups.exists() else []
            finally:
                if locked_backups:
                    (data / "backups").chmod(0o755)
                data.chmod(0o755)
            self.run_users = users.read_text().split() if users.exists() else []
            return result.returncode, result.stdout + result.stderr, kept

    def test_a_complete_dump_is_kept(self):
        code, out, kept = self._backup(DUMP="ok")
        assert code == 0, out
        assert len(kept) == 1, (kept, out)

    def test_a_failed_dump_fails_and_leaves_no_file(self):
        """pg_dumpall's exit status fails the backup even when its stream looks complete (pipefail)."""
        code, out, kept = self._backup(DUMP="fail")
        assert code != 0, out
        assert kept == [], kept
        assert "no backup was kept" in out, out

    def test_a_dump_without_its_completion_trailer_is_not_a_backup(self):
        code, out, kept = self._backup(DUMP="truncated")
        assert code != 0, out
        assert kept == [], kept

    def test_every_container_writes_as_the_xnat_uid(self):
        """Not root: the backups stay the operator's under FLIP_UID, and xnat-reset still owns the tree (FLIP#1731)."""
        code, out, _ = self._backup(DUMP="ok")
        assert code == 0, out
        assert self.run_users, "no container was run with --user"
        assert set(self.run_users) == {"1001:1001"}, self.run_users
        code, out, _ = self._backup(DUMP="ok", extra_kit="FLIP_UID=4242\nFLIP_GID=4243\n")
        assert code == 0, out
        assert set(self.run_users) == {"4242:4243"}, self.run_users

    def test_a_backups_dir_the_uid_cannot_write_is_refused_before_dumping(self):
        """Upgrades before FLIP#1731 made backups/ as root: refuse, naming the chown, and dump nothing."""
        if os.getuid() == 0:
            self.skipTest("root writes a 0555 directory anyway")
        code, out, kept = self._backup(DUMP="ok", locked_backups=True)
        assert code != 0, out
        assert "chown -R 1001:1001" in out, out
        assert "Backing up" not in out, "refused before dumping"
        assert kept == [], kept

    def test_a_data_dir_the_uid_cannot_create_backups_in_names_that_dir(self):
        """No backups/ yet: the fix is to create it for the uid, not to chown a path that is not there."""
        if os.getuid() == 0:
            self.skipTest("root writes a 0555 directory anyway")
        code, out, kept = self._backup(DUMP="ok", locked_data=True)
        assert code != 0, out
        assert "is not writable by uid 1001:1001, which creates backups/ in it" in out, out
        assert "&& chown 1001:1001" in out, out
        assert "chown -R" not in out, out
        assert "Backing up" not in out, "refused before dumping"

    def test_no_running_db_is_a_refusal(self):
        """Its one caller is upgrade-xnat: no db to dump means no upgrade, never a skipped backup."""
        code, out, _ = self._backup(NO_DB="1")
        assert code != 0, out
        assert "refusing to upgrade XNAT without a dump" in out, out


class XnatWebDirs(unittest.TestCase):
    """xnat-web-dirs, run for real against a stub docker: a live site gets the mount sources it lacks (FLIP#1818)."""

    def _run(self, data: Path, tmp: str) -> subprocess.CompletedProcess[str]:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(STUB_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.users = Path(tmp) / "run-users"
        return run_target(
            "xnat-web-dirs",
            subdir="trust/xnat",
            kit=kit_with("XNAT_DATA_DIR=/nonexistent/xnat", f"XNAT_DATA_DIR={data}"),
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUN_USERS": str(self.users)},
        )

    def test_a_site_without_a_prearchive_gets_one_as_the_xnat_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "xnat"
            study = data / "xnat-data" / "archive" / "study.dcm"
            study.parent.mkdir(parents=True)
            study.write_text("x")
            result = self._run(data, tmp)
            assert result.returncode == 0, result.stdout + result.stderr
            assert (data / "xnat-data" / "prearchive").is_dir()
            assert study.exists(), "an upgrade keeps the data"
            assert self.users.read_text().split() == ["1001:1001"], "made as xnat-web's uid, not root"

    def test_an_xnat_data_dir_the_uid_cannot_write_names_the_chown(self):
        if os.getuid() == 0:
            self.skipTest("root writes a 0555 directory anyway")
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "xnat"
            (data / "xnat-data").mkdir(parents=True, mode=0o555)
            try:
                result = self._run(data, tmp)
            finally:
                (data / "xnat-data").chmod(0o755)
            assert result.returncode != 0, result.stdout
            assert f"chown 1001:1001 {data}/xnat-data" in result.stdout, result.stdout


class XnatImageRegistry(unittest.TestCase):
    """The registry `docker stack deploy` substitutes into the XNAT images, from make's environment (FLIP#1480)."""

    ECR = "111122223333.dkr.ecr.eu-west-2.amazonaws.com/ghcr/londonaicentre/"
    STACK = (Path(__file__).resolve().parents[1] / "docker-compose-stack.yml").read_text()

    def _images(self, *extra: str, **env: str) -> list[str]:
        result = run_target(
            "__registry",
            "-s",
            *extra,
            subdir="trust/xnat",
            probe="__registry: ; @echo registry=[$$DOCKER_REGISTRY]\n",
            kit=KIT + f"DOCKER_REGISTRY={self.ECR}\n",
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        match = re.search(r"^registry=\[(.*)\]$", result.stdout, re.MULTILINE)
        assert match, result.stdout
        images = re.findall(r"image: \$\{DOCKER_REGISTRY\}(\S+)", self.STACK)
        assert images, "the stack no longer takes its registry from DOCKER_REGISTRY"
        return [match.group(1) + image for image in images]

    def test_a_deployed_trust_pulls_xnat_from_ghcr_whatever_the_kit_says(self):
        """An older kit and the admin's exported hub env can both still name an LZA hub's ECR cache."""
        for image in self._images(DOCKER_REGISTRY=self.ECR):
            assert image.startswith("ghcr.io/londonaicentre/xnat-"), image

    def test_development_with_an_empty_registry_runs_local_images(self):
        for image in self._images("HUB=", HUB="", DOCKER_REGISTRY=""):
            assert image.startswith("xnat-"), image


class UpXnatBuild(unittest.TestCase):
    """`make up BUILD=true` builds the XNAT images from this checkout and deploys exactly those.

    The swarm agent re-pulls a tag on every deploy and keeps a local image only when that pull
    fails, so the build goes to the unpublished `dev` tag: tagged `stag`, it would be replaced
    by the registry's `stag` and the stack would run images built from another commit.
    """

    def test_build_true_builds_then_deploys_the_local_tag(self):
        out = dry_run("up-xnat", "BUILD=true", subdir="trust/xnat")
        for image in ("xnat-web", "xnat-db", "xnat-nginx"):
            assert f"{image}:dev " in out, f"{image} is not built at :dev:\n{out}"
        assert "XNAT_TAG=dev" in out, out
        assert '--resolve-image "never"' in out, out
        # Built before the running stack is torn down, so a failed build leaves it up.
        assert out.index("docker build") < out.index("down-xnat"), out

    def test_a_command_line_xnat_tag_names_the_build(self):
        """Tags are daemon-wide: a second stack on the host (a release check) keeps off the shared :dev."""
        out = dry_run("up-xnat", "BUILD=true", "XNAT_TAG=rel0130", subdir="trust/xnat")
        for image in ("xnat-web", "xnat-db", "xnat-nginx"):
            assert f"{image}:rel0130 " in out, f"{image} is not built at :rel0130:\n{out}"
        assert "XNAT_TAG=rel0130" in out, out
        assert ":dev" not in out, out
        assert '--resolve-image "never"' in out, out

    def test_a_kit_xnat_tag_is_a_pull_pin_not_a_build_tag(self):
        out = dry_run("up-xnat", "BUILD=true", subdir="trust/xnat", kit=KIT + "XNAT_TAG=stag\n")
        assert "xnat-web:dev " in out, out
        assert "XNAT_TAG=dev" in out, out

    def test_default_up_xnat_builds_nothing_and_deploys_the_kit_tag(self):
        out = dry_run("up-xnat", subdir="trust/xnat")
        assert "docker build" not in out, out
        assert "XNAT_TAG=sha-badcff1" in out, out

    def test_an_inherited_xnat_tag_does_not_beat_the_kit(self):
        """`make -C trust/xnat test` exports XNAT_TAG=stag (no kit there) into every test's environment."""
        with mock.patch.dict(os.environ, {"XNAT_TAG": "stag"}):
            out = dry_run("up-xnat", subdir="trust/xnat")
        assert "XNAT_TAG=sha-badcff1" in out, out


# Neither sudo nor a root container may be reached on the non-root path (FLIP#1723).
REFUSING = {
    "sudo": '#!/bin/sh\necho "STUB: sudo called" >&2\nexit 1\n',
    "docker": '#!/bin/sh\ncase "$1" in\n  context)\n    [ "$2" = show ] && echo default\n'
    '    [ "$2" = inspect ] && echo unix:///var/run/docker.sock\n    exit 0 ;;\nesac\n'
    'echo "STUB: docker $1 called" >&2\nexit 1\n',
}


XNAT_SUBDIRS = ("tomcat_logs", "archive", "build", "cache", "prearchive", "../xnat-db-data")


class XnatDirsWithoutRoot(unittest.TestCase):
    """xnat-reset's local prod branch and xnat-prepare-dirs, run for real as a non-root user: no sudo, no
    root container (FLIP#1723). As the container uid (a kit's FLIP_UID, FLIP#1731) the reset provisions the
    tree itself; as anyone else it deletes nothing and only checks."""

    def _run(self, target: str, data: Path, tmp: str, uid: int | None = None) -> subprocess.CompletedProcess[str]:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name, body in REFUSING.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        return run_target(
            target,
            f"XNAT_DATA_DIR={data}",
            f"XNAT_CONTAINER_UID={os.getuid() if uid is None else uid}",
            f"XNAT_CONTAINER_GID={os.getgid() if uid is None else uid}",
            subdir="trust/xnat",
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )

    def setUp(self):
        if os.getuid() == 0:
            self.skipTest("running as root takes the direct branch")
        self.other_uid = os.getuid() + 1

    def test_prepare_dirs_then_reset_completes_without_escalating(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "xnat"
            prepared = self._run("xnat-prepare-dirs", data, tmp)
            assert prepared.returncode == 0, prepared.stdout + prepared.stderr
            for name in XNAT_SUBDIRS:
                assert (data / "xnat-data" / name).is_dir(), name
            reset = self._run("xnat-reset", data, tmp)
            assert reset.returncode == 0, reset.stdout + reset.stderr
            assert "STUB:" not in reset.stderr, reset.stderr

    def test_the_container_uid_provisions_and_wipes_the_tree_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "xnat"
            reset = self._run("xnat-reset", data, tmp)
            assert reset.returncode == 0, reset.stdout + reset.stderr
            assert "resetting it without root (FLIP#1731)" in reset.stdout, reset.stdout
            for name in XNAT_SUBDIRS:
                assert (data / "xnat-data" / name).is_dir(), name
            left = data / "xnat-data" / "archive" / "study.dcm"
            left.write_text("x")
            again = self._run("xnat-reset", data, tmp)
            assert again.returncode == 0, again.stdout + again.stderr
            assert not left.exists(), "a first install starts XNAT fresh"
            assert "STUB:" not in reset.stderr + again.stderr, reset.stderr + again.stderr

    def test_another_uids_tree_with_files_is_refused_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "xnat"
            left = data / "xnat-data" / "archive" / "study.dcm"
            left.parent.mkdir(parents=True)
            left.write_text("x")
            reset = self._run("xnat-reset", data, tmp, uid=self.other_uid)
            assert reset.returncode != 0, reset.stdout
            assert "from an earlier install" in reset.stdout, reset.stdout
            assert f"uid {os.getuid()} cannot wipe and re-create the tree for uid {self.other_uid}." in reset.stdout, (
                reset.stdout
            )
            assert f"rm -rf {data} && make -C trust prepare-host-dirs KIT=SCR HUB=prod" in reset.stdout, reset.stdout
            assert left.exists(), "a non-root reset must delete nothing"
            assert "STUB:" not in reset.stderr, reset.stderr

    def test_another_uids_missing_tree_names_prepare_host_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            reset = self._run("xnat-reset", Path(tmp) / "absent", tmp, uid=self.other_uid)
            assert reset.returncode != 0, reset.stdout
            assert "make -C trust prepare-host-dirs KIT=SCR HUB=prod" in reset.stdout, reset.stdout
            assert not (Path(tmp) / "absent").exists(), "another uid's tree is not created"
            assert "STUB:" not in reset.stderr, reset.stderr


if __name__ == "__main__":
    main()
