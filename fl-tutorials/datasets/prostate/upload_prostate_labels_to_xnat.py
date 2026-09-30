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

"""Upload the PI-CAI prostate labels into a FLIP project's XNAT (the data-enrichment step).

The trust PACS supplies the bpMRI *images* only; a segmentation mask has nowhere to live in a
cohort query, so the whole-gland (Bosma22b) and zonal PZ/TZ (HeviAI23) masks reach a project the
way the spleen labels do: uploaded into each scan's existing ``NIFTI`` resource, next to the
``input_*.nii.gz`` dcm2niix wrote at image pull.

**The mapping is the identity.** XNAT experiments are labelled by accession number, and
``convert_mha_to_dicom.py`` writes PI-CAI's ``<patient>_<study>`` as the AccessionNumber — which is
exactly how ``picai_labels`` names its files (``labels/<patient>_<study>.nii.gz``). So unlike the
spleen uploader nothing is fetched from the dataset: every label file on disk is an item, addressed
by its own stem.

Two files per study, so two passes with two target prefixes: the whole-gland mask lands as
``label_<stem>.nii.gz`` (the convention the spleen apps established) and the zonal mask as
``zonal_<stem>.nii.gz``. A prostate study has three scans (t2w, adc, hbv) and ``flip.xnat`` uploads
an item into every scan of its accession, so each scan carries both masks; the masks are on the
T2W grid and any consumer reorients and resamples by affine (see ``dataset.py`` in the tutorial —
dcm2niix and the label writer store the same voxels in opposite row order).

Run **after** the image pull and the DICOM-to-NIfTI conversion; the target filename is derived from
the converted image, so running early skips every scan. Enrich every trust in the project — each
XNAT holds only its own studies — with a repeated ``--xnat-url``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from flip.exceptions import XnatError
from flip.xnat import EnrichmentItem, XnatClient, run_enrichment

logger = logging.getLogger(__name__)

WHOLE_GLAND_RENAME = ("input_", "label_")
ZONAL_RENAME = ("input_", "zonal_")


def build_manifest(labels_dir: Path, accessions: set[str] | None = None) -> list[EnrichmentItem]:
    """One item per ``<accession>.nii.gz`` in a labels directory.

    Args:
        labels_dir (Path): ``labels/`` or ``zonal_labels/`` from the PI-CAI download.
        accessions (set[str] | None): Keep only these accessions (``<patient>_<study>``); all when None.

    Returns:
        list[EnrichmentItem]: Sorted by accession.

    Raises:
        SystemExit: If the directory is missing or holds no label file.
    """
    if not labels_dir.is_dir():
        raise SystemExit(
            f"❌ Labels directory not found: {labels_dir} — run `make -C fl-tutorials download-prostate-data`"
        )
    items = []
    for path in sorted(labels_dir.glob("*.nii.gz")):
        accession = path.name.removesuffix(".nii.gz")
        if accessions is not None and accession not in accessions:
            continue
        items.append(EnrichmentItem(accession_id=accession, file_path=path))
    if not items:
        raise SystemExit(f"❌ No <patient>_<study>.nii.gz under {labels_dir}")
    logger.info(f"   {len(items)} label file(s) in {labels_dir}")
    return items


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--flip-project-id", required=True, help="FLIP Central Hub project id (a UUID).")
    parser.add_argument("--labels-dir", type=Path, required=True, help="picai_labels whole-gland masks (labels/).")
    parser.add_argument(
        "--zonal-labels-dir",
        type=Path,
        default=None,
        help="picai_labels zonal PZ/TZ masks (zonal_labels/). Omit to upload the whole-gland masks only.",
    )
    parser.add_argument(
        "--accessions-file",
        type=Path,
        default=None,
        help="Optional text file, one <patient>_<study> per line: upload these studies only.",
    )
    parser.add_argument(
        "--credentials-file",
        action="append",
        dest="credentials_files",
        metavar="PATH",
        help='JSON file of {"server": ..., "user": ..., "password": ...}. Repeat once per Trust.',
    )
    parser.add_argument(
        "--xnat-url",
        action="append",
        dest="xnat_urls",
        metavar="URL",
        help="XNAT base URL, repeatable — one per Trust, enriching the whole roster in one run. "
        "Credentials come from --xnat-user/--xnat-password.",
    )
    parser.add_argument("--xnat-user", default=os.environ.get("XNAT_USER"), help="Username for --xnat-url.")
    parser.add_argument("--xnat-password", default=os.environ.get("XNAT_PASS"), help="Password for --xnat-url.")
    parser.add_argument("--overwrite", action="store_true", help="Replace labels that are already present.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, but upload nothing.")
    parser.add_argument("--allow-no-op", action="store_true", help="Exit 0 even when no destination was resolved.")
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Also fail unless every scan in the visited project(s) received its label.",
    )
    return parser


def build_clients(args: argparse.Namespace) -> list[XnatClient]:
    """One client per XNAT server named on the command line (the spleen uploader's precedence)."""
    if args.xnat_urls:
        if not args.xnat_user or not args.xnat_password:
            raise XnatError("--xnat-url needs --xnat-user and --xnat-password (or XNAT_USER / XNAT_PASS)")
        return [XnatClient(server=url, user=args.xnat_user, password=args.xnat_password) for url in args.xnat_urls]
    if args.credentials_files:
        return [XnatClient.from_config_file(path) for path in args.credentials_files]
    return [XnatClient.from_env()]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    accessions = None
    if args.accessions_file is not None:
        accessions = {line.strip() for line in args.accessions_file.read_text().splitlines() if line.strip()}

    passes = [("whole-gland", build_manifest(args.labels_dir.resolve(), accessions), WHOLE_GLAND_RENAME)]
    if args.zonal_labels_dir is not None:
        passes.append(("zonal PZ/TZ", build_manifest(args.zonal_labels_dir.resolve(), accessions), ZONAL_RENAME))

    exit_code = 0
    try:
        clients = build_clients(args)
        for name, items, rename in passes:
            logger.info(f"▶ {name} masks → {rename[1]}<image>.nii.gz")
            report = run_enrichment(
                clients,
                items,
                flip_project_id=args.flip_project_id,
                rename=rename,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            print(report.render())
            exit_code = max(
                exit_code,
                report.exit_code(allow_no_op=args.allow_no_op, require_full_coverage=args.require_full_coverage),
            )
    except XnatError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
