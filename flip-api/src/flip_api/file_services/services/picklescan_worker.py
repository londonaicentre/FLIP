# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Out-of-process picklescan worker for the model-file scan pipeline (#1743).

``malware_scan_service`` runs this script with the *same interpreter* that
runs the API (``sys.executable``, hence the same venv and the same
``picklescan`` install) and by absolute file path, so the script never needs
``flip_api`` on the import path and imports nothing from it. Keeping it
import-free of the application also keeps the child small: the only thing it
loads under the resource limits below is ``picklescan`` itself.

Usage::

    python picklescan_worker.py <file> <address_space_bytes> <cpu_seconds>

The limits are applied with ``setrlimit`` *before* picklescan is imported, so
every allocation the scan makes — including the ones picklescan makes while
walking a hostile archive — is charged against them. Exceeding the address
space raises ``MemoryError`` in the child and exceeding the CPU budget kills
it with ``SIGXCPU``; either way the parent sees a non-zero exit and no
parseable verdict, which it treats as a failed scan (fail-closed). The API
process itself is never the one that grows.

On success a single JSON object is written to stdout::

    {"issues_count": 0, "infected_files": 0, "scan_err": false}

Any other outcome is a non-zero exit, deliberately with no verdict to parse.
"""

import json
import resource
import sys

# Exit codes. Anything non-zero is a failed scan to the caller; the distinct
# values only make a failure legible in the logs.
EXIT_USAGE = 2
EXIT_SCAN_ERROR = 3


def _apply_limits(address_space_bytes: int, cpu_seconds: int) -> None:
    """Cap the child's address space and CPU time.

    Args:
        address_space_bytes (int): Hard cap on the virtual address space
            (``RLIMIT_AS``). Allocations beyond it raise ``MemoryError``.
        cpu_seconds (int): Hard cap on consumed CPU time (``RLIMIT_CPU``).
            The kernel sends ``SIGXCPU`` when it is reached, which bounds a
            scan that spins without allocating (the wall-clock timeout in the
            parent covers a child that blocks instead).
    """
    resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def main(argv: list[str]) -> int:
    """Scan one file and print its raw picklescan counters as JSON.

    The verdict mapping stays in the parent: this script reports only what
    picklescan found, so that the policy (dangerous outranks a scan error,
    an inconclusive scan fails closed) lives in one place.

    Args:
        argv (list[str]): ``[script, file, address_space_bytes, cpu_seconds]``.

    Returns:
        int: 0 when the scan completed and a verdict was printed, non-zero
        otherwise.
    """
    if len(argv) != 4:
        print("usage: picklescan_worker.py <file> <address_space_bytes> <cpu_seconds>", file=sys.stderr)
        return EXIT_USAGE
    path = argv[1]
    try:
        address_space_bytes = int(argv[2])
        cpu_seconds = int(argv[3])
    except ValueError:
        print("limits must be integers", file=sys.stderr)
        return EXIT_USAGE

    _apply_limits(address_space_bytes, cpu_seconds)

    # Imported after the limits are in place, and inside main() so that
    # importing this module (e.g. for its exit codes) never pulls picklescan in.
    from picklescan.scanner import scan_file_path

    try:
        result = scan_file_path(path)
    except BaseException as e:  # noqa: BLE001 - MemoryError is a BaseException subclass in spirit here
        # Deliberately broad, and deliberately not re-raised: a traceback on
        # stderr says nothing the parent can use, and under a hit address-space
        # limit even formatting one can fail. The exit code is the signal.
        print(f"picklescan failed: {type(e).__name__}", file=sys.stderr)
        return EXIT_SCAN_ERROR
    json.dump(
        {
            "issues_count": int(result.issues_count),
            "infected_files": int(result.infected_files),
            "scan_err": bool(result.scan_err),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
