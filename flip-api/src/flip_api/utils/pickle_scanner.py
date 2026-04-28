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

"""Detect malicious globals in pickle-bearing model artifacts.

PyTorch's ``.pt`` / ``.pth`` files (and raw ``.pkl`` blobs) use Python pickle,
which is RCE-by-design: ``__reduce__`` can resolve to any callable on import.
Signature-based malware scanners (ClamAV, GuardDuty Malware Protection for S3)
do not parse pickle opcodes, so they cannot flag a hand-crafted payload that
calls ``os.system`` on load. This module wraps `picklescan` to close that gap
in the upload-scan pipeline.
"""

import io
from dataclasses import dataclass

from flip_api.utils.logger import logger

# Extensions whose contents are pickle-bearing and therefore worth scanning.
# ``.bin`` is intentionally excluded: too generic to assume pickle. ``.zip``
# and ``.tar`` style PyTorch checkpoints are handled by picklescan internally
# when fed the bytes via ``scan_bytes``.
PICKLE_EXTENSIONS: frozenset[str] = frozenset({"pt", "pth", "pkl", "ckpt"})


@dataclass(frozen=True)
class PickleScanResult:
    """Outcome of a pickle scan."""

    is_safe: bool
    threats: tuple[str, ...]
    scanner_error: str | None = None


def is_pickle_bearing(extension: str) -> bool:
    """Return True if files with this extension should be pickle-scanned.

    Args:
        extension: Lowercase extension without the leading dot.
    """
    return extension.lower() in PICKLE_EXTENSIONS


def scan_pickle_bytes(data: bytes, file_name: str) -> PickleScanResult:
    """Scan a pickle-bearing payload for unsafe globals.

    Args:
        data: Raw bytes of the file as stored in S3.
        file_name: Original file name (used by picklescan to dispatch on
            extension when handling PyTorch zip/tar wrappers).

    Returns:
        A ``PickleScanResult`` whose ``is_safe`` is ``True`` only when the
        scanner ran cleanly and found zero infected files. Scanner errors
        (e.g. corrupt pickle) are surfaced via ``scanner_error`` and treated
        as ``is_safe = False`` — better to fail closed than promote a file
        we could not parse.
    """
    try:
        from picklescan.scanner import scan_bytes  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.error(f"picklescan import failed; cannot scan {file_name}: {exc}")
        return PickleScanResult(
            is_safe=False,
            threats=(),
            scanner_error=f"picklescan import failed: {exc}",
        )

    try:
        # picklescan's scan_bytes expects IO[bytes], not raw bytes; wrapping in
        # BytesIO matches the documented signature.
        result = scan_bytes(io.BytesIO(data), file_name)
    except Exception as exc:
        logger.error(f"picklescan raised while scanning {file_name}: {exc}")
        return PickleScanResult(
            is_safe=False,
            threats=(),
            scanner_error=f"picklescan raised: {exc}",
        )

    threats: list[str] = []
    for entry in getattr(result, "globals", []) or []:
        module = getattr(entry, "module", None)
        name = getattr(entry, "name", None)
        safety = getattr(entry, "safety", None)
        # picklescan tags each global with a Safety enum; only the unsafe ones
        # are reported. The fallback covers older versions that emit raw issues.
        is_unsafe = safety is not None and getattr(safety, "name", str(safety)) != "Safe"
        if module and name and is_unsafe:
            threats.append(f"{module}.{name}")
    if not threats:
        for issue in getattr(result, "issues", []) or []:
            module = getattr(issue, "module", "?")
            name = getattr(issue, "name", "?")
            threats.append(f"{module}.{name}")

    infected = int(getattr(result, "infected_files", 0) or 0)
    scan_err = bool(getattr(result, "scan_err", False))

    if scan_err:
        return PickleScanResult(
            is_safe=False,
            threats=tuple(threats),
            scanner_error="picklescan reported scan_err=True",
        )

    return PickleScanResult(is_safe=infected == 0, threats=tuple(threats))
