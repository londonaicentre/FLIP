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
"""trust/xnat/Makefile's in-place upgrade: where the directories are checked, and a backup that only counts
when complete (FLIP#1204).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from make_dry_run import KIT, dry_run, run_target  # noqa: E402


class UpgradeXnat(unittest.TestCase):
    def test_upgrade_xnat_checks_ownership_where_the_directories_are(self):
        """With a remote (ssh://) docker context the XNAT dirs live on the trust host (FLIP#1204).

        upgrade-trust-ec2 drives upgrade-xnat from the admin workstation, so a bare `stat -c` reads
        this machine's filesystem (and fails outright on BSD stat) instead of the trust host's —
        the same split xnat-reset already makes.
        """
        out = dry_run("upgrade-xnat", subdir="trust/xnat")
        assert "ssh://*)" in out, f"upgrade-xnat has no remote-context branch:\n{out}"
        assert 'ssh "$xnat_ctx" "stat -c' in out, f"the remote branch must stat over ssh:\n{out}"
        assert "Fix: ssh $xnat_ctx sudo chown" in out, f"the remote remedy must be runnable on the host:\n{out}"

    def test_upgrade_xnat_requires_the_backup(self):
        """A live site is never migrated without a dump: upgrade-xnat makes a missing xnat-db fatal."""
        out = dry_run("upgrade-xnat", subdir="trust/xnat")
        assert "xnat-backup KIT=SCR XNAT_BACKUP_REQUIRED=1" in out, out


# A stand-in for the docker CLI, just enough of it for xnat-backup: `ps` finds the db, `exec` is
# pg_dumpall (DUMP=ok|fail|truncated), and `run alpine …` runs the command locally with each
# `-v src:dst` mount rewritten to its host path.
STUB_DOCKER = r"""#!/bin/bash
case "$1" in
  ps) [ -n "$NO_DB" ] || echo "stub-xnat-db"; exit 0 ;;
  exec)
    printf -- '--\n-- PostgreSQL database cluster dump\n--\nCREATE ROLE xnat;\n'
    [ "$DUMP" = truncated ] && exit 0
    printf -- '--\n-- PostgreSQL database cluster dump complete\n--\n\n'
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
    exec "${args[@]}" ;;
esac
exit 0
"""


class XnatBackup(unittest.TestCase):
    """xnat-backup, run for real against a stub docker: only a complete dump counts as a backup."""

    def _backup(self, *extra: str, **env: str) -> tuple[int, str, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(STUB_DOCKER)
            docker.chmod(0o755)
            data = Path(tmp) / "xnat"
            data.mkdir()
            kit = KIT.replace("XNAT_DATA_DIR=/nonexistent/xnat", f"XNAT_DATA_DIR={data}")
            assert kit != KIT, "scratch kit no longer carries XNAT_DATA_DIR"
            result = run_target(
                "xnat-backup",
                *extra,
                subdir="trust/xnat",
                kit=kit,
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", **env},
            )
            backups = sorted((data / "backups").glob("*.sql.gz")) if (data / "backups").exists() else []
            return result.returncode, result.stdout + result.stderr, [b.name for b in backups]

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

    def test_no_db_is_a_note_for_a_first_install_and_fatal_for_an_upgrade(self):
        code, out, _ = self._backup(NO_DB="1")
        assert code == 0, out
        assert "nothing to back up" in out, out
        code, out, _ = self._backup("XNAT_BACKUP_REQUIRED=1", NO_DB="1")
        assert code != 0, out
        assert "refusing to upgrade XNAT without a dump" in out, out


if __name__ == "__main__":
    if not shutil.which("make"):
        print("make not installed — skipping", file=sys.stderr)
        sys.exit(0)
    unittest.main()
