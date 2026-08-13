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

"""Manifest-driven upload of enrichment files (typically segmentation labels) into XNAT.

The unit of work is a *manifest*: rows of ``accession_id, file_path`` naming a local file to place
alongside the image FLIP already pulled for that accession. This is deliberately project-agnostic —
how a manifest is built is specific to a dataset, but uploading one is not.

**The naming contract.** FL apps pair an image with its label by filename, e.g. the spleen apps do
``str(image).replace("/input_", "/label_")``. So by default the target filename is derived from the
image already sitting in the scan's resource, swapping ``input_`` for ``label_``. That keeps the
uploaded name in lock-step with whatever DICOM-to-NIfTI conversion produced, instead of guessing.
"""

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from flip.exceptions import XnatError
from flip.xnat.client import XnatClient, XnatScan

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE = "NIFTI"
"""XNAT resource enrichment files are written into. Matches what the FL client downloads."""

DEFAULT_RENAME = ("input_", "label_")
"""Default (source prefix, target prefix) used to derive a label's name from its image's name."""


@dataclass(frozen=True)
class EnrichmentItem:
    """One file to upload, addressed by the accession whose scan it belongs to.

    Attributes:
        accession_id (str): Accession number of the study the file belongs to.
        file_path (Path): Local path of the file to upload.
        target_filename (str | None): Explicit name to give the file in XNAT. When ``None`` the
            name is derived from the image already in the scan's resource.
    """

    accession_id: str
    file_path: Path
    target_filename: str | None = None


@dataclass
class EnrichmentSummary:
    """Outcome of an :func:`upload_enrichment_files` run.

    Entries are recorded **per destination scan**, not per accession, and each list holds the
    accession id of the scan concerned. An accession that maps to several scans therefore
    contributes one entry per scan — and can appear under two different outcomes if, say, one of
    its scans already had the file and another did not. That is deliberate: the counts describe
    files written, which is the work actually done, and collapsing them to one entry per accession
    would under-report a multi-scan upload and hide the mixed outcome.

    Attributes:
        uploaded (list[str]): One entry per scan whose file was uploaded (or would be, on a dry run).
        skipped_no_scan (list[str]): Accessions with no matching scan in the XNAT project.
        skipped_no_resource (list[str]): One entry per scan with no image to derive a name from.
        skipped_exists (list[str]): One entry per scan where the target file was already present.
        failed (list[tuple[str, str]]): ``(accession, reason)`` for each failure.
        dry_run (bool): Whether the run was a dry run.
    """

    uploaded: list[str] = field(default_factory=list)
    skipped_no_scan: list[str] = field(default_factory=list)
    skipped_no_resource: list[str] = field(default_factory=list)
    skipped_exists: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """bool: True when nothing failed outright."""
        return not self.failed

    def render(self) -> str:
        """Render a one-line-per-outcome summary suitable for CLI output.

        Returns:
            str: The rendered summary.
        """
        verb = "would upload" if self.dry_run else "uploaded"
        # Counts are per destination scan (see the class docstring), so name the unit rather than
        # leaving the reader to assume one line per accession.
        lines = [
            f"  {verb}: {len(self.uploaded)} file(s)",
            f"  skipped (no matching scan): {len(self.skipped_no_scan)} accession(s)",
            f"  skipped (no image in resource): {len(self.skipped_no_resource)} scan(s)",
            f"  skipped (already present): {len(self.skipped_exists)} scan(s)",
            f"  failed: {len(self.failed)}",
        ]
        lines.extend(f"    ✗ {accession}: {reason}" for accession, reason in self.failed)
        return "\n".join(lines)


def read_manifest(path: str | Path) -> list[EnrichmentItem]:
    """Read a manifest CSV of files to upload.

    The CSV must have a header with ``accession_id`` and ``file_path`` columns, and may have an
    optional ``target_filename`` column. Relative paths resolve against the manifest's directory,
    so a manifest stays portable alongside the files it names.

    Args:
        path (str | Path): Path to the manifest CSV.

    Returns:
        list[EnrichmentItem]: The parsed rows.

    Raises:
        XnatError: If the file is unreadable, or a required column or value is missing.
    """
    manifest_path = Path(path)
    try:
        text = manifest_path.read_text()
    except OSError as err:
        raise XnatError(f"Could not read manifest {manifest_path}: {err}") from err

    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise XnatError(f"Manifest {manifest_path} is empty")

    missing_columns = {"accession_id", "file_path"} - set(reader.fieldnames)
    if missing_columns:
        raise XnatError(
            f"Manifest {manifest_path} is missing column(s): {', '.join(sorted(missing_columns))} "
            f"(found: {', '.join(reader.fieldnames)})"
        )

    items: list[EnrichmentItem] = []
    for line_number, row in enumerate(reader, start=2):
        accession_id = (row.get("accession_id") or "").strip()
        file_path = (row.get("file_path") or "").strip()
        if not accession_id or not file_path:
            raise XnatError(f"Manifest {manifest_path} line {line_number}: accession_id and file_path are required")

        resolved = Path(file_path)
        if not resolved.is_absolute():
            resolved = manifest_path.parent / resolved

        target_filename = (row.get("target_filename") or "").strip() or None
        items.append(EnrichmentItem(accession_id, resolved, target_filename))

    return items


def _derive_target_filename(filenames: list[str], rename: tuple[str, str]) -> str | None:
    """Derive an enrichment file's name from the image already in the resource.

    Args:
        filenames (list[str]): Filenames currently in the scan's resource.
        rename (tuple[str, str]): ``(source prefix, target prefix)``.

    Returns:
        str | None: The derived name, or None if no filename carries the source prefix.
    """
    source_prefix, target_prefix = rename
    for filename in sorted(filenames):
        if filename.startswith(source_prefix):
            return f"{target_prefix}{filename[len(source_prefix) :]}"
    return None


def upload_enrichment_files(
    client: XnatClient,
    project_id: str,
    items: list[EnrichmentItem],
    resource: str = DEFAULT_RESOURCE,
    rename: tuple[str, str] = DEFAULT_RENAME,
    overwrite: bool = False,
    dry_run: bool = False,
) -> EnrichmentSummary:
    """Upload each manifest item into the XNAT scan matching its accession.

    Args:
        client (XnatClient): An authenticated XNAT client.
        project_id (str): The XNAT project id (see
            :meth:`XnatClient.resolve_project_by_flip_project_id`).
        items (list[EnrichmentItem]): The files to upload.
        resource (str): Resource to write into, e.g. ``NIFTI``.
        rename (tuple[str, str]): ``(source prefix, target prefix)`` used to derive target names.
        overwrite (bool): Replace files that are already present.
        dry_run (bool): Resolve and report everything, but upload nothing.

    Returns:
        EnrichmentSummary: Per-accession outcomes.

    Raises:
        XnatError: If ``rename`` prefixes are equal (which would overwrite the image), or if the
            project's scans cannot be listed at all.
    """
    if rename[0] == rename[1]:
        raise XnatError(f"rename prefixes must differ, both are {rename[0]!r} — this would overwrite the image")

    scans_by_accession: dict[str, list[XnatScan]] = {}
    for scan in client.list_scans(project_id):
        scans_by_accession.setdefault(scan.accession_id, []).append(scan)

    summary = EnrichmentSummary(dry_run=dry_run)
    for item in items:
        scans = scans_by_accession.get(item.accession_id)
        if not scans:
            logger.info(f"{item.accession_id}: no matching scan in project {project_id}")
            summary.skipped_no_scan.append(item.accession_id)
            continue

        if not item.file_path.is_file():
            summary.failed.append((item.accession_id, f"local file not found: {item.file_path}"))
            continue

        for scan in scans:
            _upload_one(client, project_id, scan, item, resource, rename, overwrite, dry_run, summary)

    return summary


def _upload_one(
    client: XnatClient,
    project_id: str,
    scan: XnatScan,
    item: EnrichmentItem,
    resource: str,
    rename: tuple[str, str],
    overwrite: bool,
    dry_run: bool,
    summary: EnrichmentSummary,
) -> None:
    """Upload one item into one scan, recording the outcome in ``summary``.

    Args:
        client (XnatClient): An authenticated XNAT client.
        project_id (str): The XNAT project id.
        scan (XnatScan): The destination scan.
        item (EnrichmentItem): The file to upload.
        resource (str): Resource to write into.
        rename (tuple[str, str]): ``(source prefix, target prefix)``.
        overwrite (bool): Replace a file that is already present.
        dry_run (bool): Report only.
        summary (EnrichmentSummary): Mutated with this item's outcome.
    """
    existing = client.list_resource_files(scan, resource)
    target_filename = item.target_filename or _derive_target_filename(existing, rename)
    if target_filename is None:
        logger.info(
            f"{item.accession_id}: scan {scan.scan_id} has no {rename[0]}* file in its {resource} resource — "
            f"has DICOM-to-NIfTI conversion run?"
        )
        summary.skipped_no_resource.append(item.accession_id)
        return

    if target_filename in existing and not overwrite:
        logger.info(f"{item.accession_id}: {target_filename} already present, skipping")
        summary.skipped_exists.append(item.accession_id)
        return

    if dry_run:
        logger.info(f"{item.accession_id}: would upload {item.file_path.name} as {target_filename}")
        summary.uploaded.append(item.accession_id)
        return

    try:
        client.upload_scan_resource_file(
            scan=scan,
            project_id=project_id,
            resource=resource,
            local_path=item.file_path,
            target_filename=target_filename,
            overwrite=overwrite,
        )
    except XnatError as err:
        summary.failed.append((item.accession_id, str(err)))
        return

    summary.uploaded.append(item.accession_id)
