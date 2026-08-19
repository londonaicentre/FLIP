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
from typing import Any

from flip.exceptions import XnatError, XnatProjectNotFound
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
        requested (int): How many manifest items this run was handed. Recorded so a caller holding
            several summaries can tell "nothing to do" apart from "asked to do work, did none".
        scans_total (int): How many scans the XNAT project holds, the denominator for coverage.
    """

    uploaded: list[str] = field(default_factory=list)
    skipped_no_scan: list[str] = field(default_factory=list)
    skipped_no_resource: list[str] = field(default_factory=list)
    skipped_exists: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False
    requested: int = 0
    scans_total: int = 0

    @property
    def ok(self) -> bool:
        """bool: True when nothing failed outright."""
        return not self.failed

    @property
    def resolved_any(self) -> bool:
        """bool: True when at least one destination scan was resolved.

        "Resolved" means the file was uploaded or was already there — i.e. this run found a real
        place to put something. ``skipped_no_resource`` deliberately does **not** count: that is
        the scan-exists-but-was-never-converted case, which is the commonest way an enrichment run
        does nothing useful while looking clean, and the one the user guide's troubleshooting table
        leads with. ``ok`` stays "nothing failed"; this is the separate question of whether any
        work landed, and it is what the CLI turns into an exit code.
        """
        return bool(self.uploaded or self.skipped_exists)

    @property
    def resolved(self) -> int:
        """int: Number of destination scans resolved (uploaded plus already-present)."""
        return len(self.uploaded) + len(self.skipped_exists)

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
        if self.scans_total:
            lines.append(f"  coverage: {self.resolved}/{self.scans_total} scan(s) in the project now enriched")
        lines.extend(f"    ✗ {accession}: {reason}" for accession, reason in self.failed)
        if self.requested and not self.resolved_any:
            # The failure this guards against is silent: every item skipped, nothing failed, exit 0,
            # and the truth only emerges minutes later when training finds no labels.
            lines += [
                "",
                f"  ⚠️  Nothing was resolved: {self.requested} item(s) requested, no destination found.",
                "      Likely causes: the wrong XNAT project (check --flip-project-id), the wrong Trust",
                "      (each Trust holds only its own studies), or DICOM-to-NIfTI conversion has not run",
                "      yet (the images must be converted before their labels can be named after them).",
            ]
        return "\n".join(lines)


URL_UNSAFE_FILENAME_CHARS = frozenset("?#\\%")
"""Characters rejected in a manifest ``target_filename`` on top of path separators.

``?`` and ``#`` are legal in POSIX filenames but delimit a URL, and ``requests`` splits on them
before it quotes anything — so they would sever the query string. ``%`` would make an
already-encoded name ambiguous, and ``\\`` is a separator on the operator's side.
"""


def _is_bare_filename(name: str) -> bool:
    """Check that a name addresses one file and survives URL construction intact.

    Args:
        name (str): The candidate filename.

    Returns:
        bool: True when the name is a single path component with no URL-significant characters.
    """
    if name in (".", "..") or name != Path(name).name:
        return False
    return not (URL_UNSAFE_FILENAME_CHARS & set(name))


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
        if target_filename is not None and not _is_bare_filename(target_filename):
            # This value is interpolated into the upload URL's path. A separator would redirect the
            # write elsewhere, and a "?" or "#" would truncate the URL and silently drop
            # inbody=true. The client escapes it too, but rejecting here names the file and line
            # instead of leaving XNAT to answer with a puzzling 404.
            raise XnatError(
                f"Manifest {manifest_path} line {line_number}: target_filename must be a bare filename, "
                f"got {target_filename!r}"
            )
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
    scans_total = 0
    for scan in client.list_scans(project_id):
        scans_by_accession.setdefault(scan.accession_id, []).append(scan)
        scans_total += 1

    summary = EnrichmentSummary(dry_run=dry_run, requested=len(items), scans_total=scans_total)
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


@dataclass
class ServerOutcome:
    """What happened at one XNAT server during a :func:`run_enrichment` pass.

    Exactly one of ``summary`` and ``error`` is set. ``error`` covers both a hard fault (the server
    was unreachable, rejected our credentials, or failed) and the soft "this Trust has not pulled
    this project" case, which :attr:`fatal` tells apart.

    Attributes:
        server (str): Base URL of the XNAT server.
        project_id (str | None): The resolved XNAT project id, if resolution got that far.
        summary (EnrichmentSummary | None): The upload outcome, when the pass ran.
        error (str | None): Why the pass did not run.
        fatal (bool): Whether ``error`` should fail the whole run. False for a project that simply
            is not at this Trust.
    """

    server: str
    project_id: str | None = None
    summary: EnrichmentSummary | None = None
    error: str | None = None
    fatal: bool = False


@dataclass
class EnrichmentReport:
    """Aggregate outcome of an enrichment run across one or more XNAT servers.

    Attributes:
        outcomes (list[ServerOutcome]): One entry per server, in the order they were visited.
    """

    outcomes: list[ServerOutcome] = field(default_factory=list)

    @property
    def summaries(self) -> list[EnrichmentSummary]:
        """list[EnrichmentSummary]: The summaries of every server whose pass actually ran."""
        return [outcome.summary for outcome in self.outcomes if outcome.summary is not None]

    @property
    def requested(self) -> int:
        """int: Items requested at the server that was asked to do the most work.

        The same manifest goes to every server, so this is the size of the manifest rather than a
        sum — summing would multiply it by the roster size and make coverage look impossible.
        """
        return max((summary.requested for summary in self.summaries), default=0)

    @property
    def resolved(self) -> int:
        """int: Destination scans resolved across all servers."""
        return sum(summary.resolved for summary in self.summaries)

    @property
    def resolved_any(self) -> bool:
        """bool: True when at least one server resolved at least one destination."""
        return any(summary.resolved_any for summary in self.summaries)

    @property
    def scans_total(self) -> int:
        """int: Scans held across every project visited — the coverage denominator."""
        return sum(summary.scans_total for summary in self.summaries)

    @property
    def fully_covered(self) -> bool:
        """bool: True when every scan found across the roster now carries its enrichment file."""
        return self.scans_total > 0 and self.resolved >= self.scans_total

    def exit_code(self, allow_no_op: bool = False, require_full_coverage: bool = False) -> int:
        """Decide the process exit code for this run.

        The rules live here, once, rather than at each entry point, because the interesting cases
        are aggregate ones: with a multi-Trust roster a single server legitimately holding none of
        the cohort must **not** fail the run, while *no* server resolving anything must.

        Args:
            allow_no_op (bool): Treat a run that resolved nothing as success. For the legitimately
                empty case — a partial manifest against a Trust that holds none of it.
            require_full_coverage (bool): Also fail when some scan in a visited project did not
                receive its enrichment file.

        Returns:
            int: 0 on success, 1 otherwise.
        """
        if any(outcome.fatal for outcome in self.outcomes):
            return 1
        if any(not summary.ok for summary in self.summaries):
            return 1
        if not allow_no_op and self.requested and not self.resolved_any:
            return 1
        if require_full_coverage and not self.fully_covered:
            return 1
        return 0

    def render(self) -> str:
        """Render a per-server report suitable for CLI output.

        Returns:
            str: The rendered report.
        """
        blocks: list[str] = []
        for outcome in self.outcomes:
            if outcome.summary is not None:
                blocks.append(f"\nXNAT project {outcome.project_id} ({outcome.server}):\n{outcome.summary.render()}")
            else:
                marker = "❌" if outcome.fatal else "↷"
                blocks.append(f"\n{marker} {outcome.server}: {outcome.error}")

        if len(self.outcomes) > 1 and self.scans_total:
            blocks.append(
                f"\nAcross {len(self.outcomes)} server(s): {self.resolved}/{self.scans_total} scan(s) enriched"
            )
        return "\n".join(blocks)


def run_enrichment(
    clients: list[XnatClient],
    items: list[EnrichmentItem],
    flip_project_id: str | None = None,
    project_id: str | None = None,
    **upload_kwargs: Any,
) -> EnrichmentReport:
    """Upload one manifest into every given XNAT server, collecting a per-server outcome.

    Enrichment is inherently per-Trust — each Trust's XNAT holds only its own studies — so a
    complete run means visiting every Trust in the project. Passing the whole manifest to each
    server is self-selecting: an accession exists at exactly one Trust, and the others report it as
    *no matching scan*, so no per-server filtering is needed and nothing can be uploaded twice.

    One server failing does not stop the others; the failure is recorded and surfaces in
    :meth:`EnrichmentReport.exit_code`, so a partial roster failure is neither hidden nor allowed
    to abandon the Trusts that would have succeeded.

    Args:
        clients (list[XnatClient]): One authenticated client per XNAT server.
        items (list[EnrichmentItem]): The files to upload, the same list for every server.
        flip_project_id (str | None): FLIP Central Hub project id, resolved per server via
            ``secondary_ID``. Required unless ``project_id`` is given.
        project_id (str | None): An explicit XNAT project id. Only meaningful for a single server,
            since XNAT project ids differ per Trust.
        **upload_kwargs: Forwarded to :func:`upload_enrichment_files`.

    Returns:
        EnrichmentReport: One outcome per server, in visit order.

    Raises:
        XnatError: If neither ``flip_project_id`` nor ``project_id`` is given, or if
            ``project_id`` is used with more than one server.
    """
    if not flip_project_id and not project_id:
        raise XnatError("one of flip_project_id or project_id is required")
    if project_id and len(clients) > 1:
        raise XnatError(
            "project_id addresses a single XNAT server — XNAT project ids differ per Trust. "
            "Use flip_project_id so each server resolves its own."
        )

    report = EnrichmentReport()
    for client in clients:
        outcome = ServerOutcome(server=client.server)
        report.outcomes.append(outcome)
        try:
            outcome.project_id = project_id or client.resolve_project_by_flip_project_id(str(flip_project_id))
            outcome.summary = upload_enrichment_files(client, outcome.project_id, items, **upload_kwargs)
        except XnatProjectNotFound as err:
            # With a roster, a Trust that has not pulled this project is a legitimate skip; alone,
            # it is the whole run failing and must stay fatal.
            outcome.error, outcome.fatal = str(err), len(clients) == 1
            logger.warning(f"{client.server}: {err}")
        except XnatError as err:
            outcome.error, outcome.fatal = str(err), True
            logger.error(f"{client.server}: {err}")

    return report
