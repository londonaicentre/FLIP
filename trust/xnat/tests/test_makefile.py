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

        upgrade-trust-ec2 drives upgrade-xnat from the admin workstation, so a bare `stat -c` reads
        this machine's filesystem (and fails outright on BSD stat) instead of the trust host's —
        the same split xnat-reset already makes.
        """
        out = self.recipe
        assert "ssh://*)" in out, f"upgrade-xnat has no remote-context branch:\n{out}"
        assert 'ssh "$xnat_ctx" "stat -c' in out, f"the remote branch must stat over ssh:\n{out}"
        assert "Fix: ssh $xnat_ctx sudo chown" in out, f"the remote remedy must be runnable on the host:\n{out}"

    def test_upgrade_xnat_backs_up_before_redeploying(self):
        out = self.recipe
        assert "xnat-backup KIT=SCR" in out, out
        assert out.index("xnat-backup KIT=SCR") < out.index("docker stack deploy"), out


# A stand-in for the docker CLI, just enough of it for xnat-backup: `ps` finds the db by its service
# label (NO_DB hides it), `exec` is the db container's `pg_dumpall | gzip` (DUMP=ok|fail|truncated),
# and `run alpine …` runs the command here — under bash, as the containers' busybox sh has pipefail
# and dash does not — with each `-v src:dst` mount rewritten to its host path.
STUB_DOCKER = r"""#!/bin/bash
case "$1" in
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

    def _backup(self, **env: str) -> tuple[int, str, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(STUB_DOCKER)
            docker.chmod(0o755)
            data = Path(tmp) / "xnat"
            data.mkdir()
            result = run_target(
                "xnat-backup",
                subdir="trust/xnat",
                kit=kit_with("XNAT_DATA_DIR=/nonexistent/xnat", f"XNAT_DATA_DIR={data}"),
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", **env},
            )
            kept = sorted(b.name for b in (data / "backups").glob("*.sql.gz")) if (data / "backups").exists() else []
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

    def test_no_running_db_is_a_refusal(self):
        """Its one caller is upgrade-xnat: no db to dump means no upgrade, never a skipped backup."""
        code, out, _ = self._backup(NO_DB="1")
        assert code != 0, out
        assert "refusing to upgrade XNAT without a dump" in out, out


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
        for image in self._images("PROD=", PROD="", DOCKER_REGISTRY=""):
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


if __name__ == "__main__":
    main()
