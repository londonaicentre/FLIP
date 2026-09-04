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

"""Prepare an NCI Imaging Data Commons digital-pathology subset for the FLIP nuclei-detection tutorial.

Downloads DICOM Slide Microscopy (SM) whole-slide images and their Pan-Cancer-Nuclei-Seg bulk
annotations (ANN) straight from IDC's public buckets. **No DICOM is ever re-hosted** — IDC is the
source of truth and serves the pixels without credentials.

Site partitioning uses the TCGA **Tissue Source Site** code, the second field of the TCGA barcode
carried in the DICOM ``PatientID`` (``TCGA-<TSS>-<participant>``). TSS describes *tissue provenance*,
not necessarily the scanning institution, so it is a **proxy** for an institutional data partition —
see the tutorial README.

Selection is deterministic: candidates are ordered by (download size, SeriesInstanceUID) and the
smallest are taken, one slide per patient so no patient spans two sites and no patient contributes
twice. The resulting manifest is a **lockfile** — regenerable from the criteria, but pinned so a run
reproduces across IDC releases.

Outputs, under ``--out-dir`` (default ``fl-tutorials/data/idc_pathology``)::

    <out>/manifest.csv                      # the lockfile: every selected slide, its site, its UIDs
    <out>/<site>/dataframe.csv              # the FLIP dataframe for that site (needs accession_id)
    <out>/<site>/accession-resources/<accession_id>/{slide.dcm,annotation.dcm}

Usage::

    # resolve a fresh selection and download it
    python prepare_idc_pathology.py --resolve --sites BH,A2 --slides-per-site 5

    # reproduce a pinned selection (no index query -- exact same slides)
    python prepare_idc_pathology.py --manifest manifest.csv

    # see what would be selected and how big it is, without downloading
    python prepare_idc_pathology.py --resolve --dry-run
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger("prepare_idc_pathology")

# The IDC analysis result carrying nuclei annotations, and the modality we consume from it. ANN
# (Microscopy Bulk Simple Annotations, DICOM Sup 222) is preferred over the SEG form of the same
# result: it encodes nuclei as polygons rather than raster masks and is roughly 4x smaller.
NUCLEI_ANALYSIS_RESULT = "pan_cancer_nuclei_seg_dicom"
NUCLEI_MODALITY = "ANN"

DEFAULT_COLLECTION = "tcga_brca"
DEFAULT_SITES = ("A8", "A7")
DEFAULT_SLIDES_PER_SITE = 5

SLIDE_FILENAME = "slide.dcm"
ANNOTATION_FILENAME = "annotation.dcm"

MANIFEST_COLUMNS = [
    "accession_id",
    "site",
    "tss",
    "patient_id",
    "slide_series_uid",
    "slide_sop_instance_uid",
    "annotation_series_uid",
    "total_pixel_columns",
    "total_pixel_rows",
    "pixel_spacing_mm",
    "slide_instance_mb",
    "annotation_series_mb",
    "idc_index_version",
]


def _candidate_query(collection: str) -> str:
    """SQL selecting every annotated slide in *collection*, with its base-resolution instance.

    Three joins carry the whole design:

    * ``ann_index.referenced_SeriesInstanceUID`` is IDC's **explicit** link from an annotation series
      back to the slide it annotates. This is why the manifest needs no pre-download resolution step —
      the pairing is in the index, not only inside the ANN object's ``ReferencedSeriesSequence``.
    * ``sm_instance_index`` exposes each pyramid level as its own instance, so the base level can be
      downloaded alone. Nuclei detection needs it: the first RESAMPLED level is ~1 um/px, where a
      nucleus is only a handful of pixels.
    * The base level is the VOLUME instance that is *not* RESAMPLED, i.e. ``ImageType`` contains both
      ``VOLUME`` and ``NONE``. LABEL, OVERVIEW and THUMBNAIL instances are excluded by the same test.
    """
    return f"""
    WITH slides AS (
        SELECT SeriesInstanceUID AS slide_series_uid, PatientID AS patient_id
          FROM index
         WHERE collection_id = '{collection}'
           AND Modality = 'SM'
           AND (analysis_result_id IS NULL OR analysis_result_id = '')
    ),
    annotations AS (
        SELECT i.SeriesInstanceUID AS annotation_series_uid,
               a.referenced_SeriesInstanceUID AS slide_series_uid,
               i.series_size_MB AS annotation_series_mb
          FROM index i
          JOIN ann_index a USING (SeriesInstanceUID)
         WHERE i.collection_id = '{collection}'
           AND i.Modality = '{NUCLEI_MODALITY}'
           AND i.analysis_result_id = '{NUCLEI_ANALYSIS_RESULT}'
    ),
    base_level AS (
        SELECT SeriesInstanceUID AS slide_series_uid,
               SOPInstanceUID AS slide_sop_instance_uid,
               TotalPixelMatrixColumns AS total_pixel_columns,
               TotalPixelMatrixRows AS total_pixel_rows,
               PixelSpacing_0 AS pixel_spacing_mm,
               instance_size / 1e6 AS slide_instance_mb
          FROM sm_instance_index
         WHERE list_contains(ImageType, 'VOLUME')
           AND list_contains(ImageType, 'NONE')
    )
    SELECT s.patient_id,
           split_part(s.patient_id, '-', 2) AS tss,
           s.slide_series_uid,
           b.slide_sop_instance_uid,
           n.annotation_series_uid,
           b.total_pixel_columns,
           b.total_pixel_rows,
           b.pixel_spacing_mm,
           b.slide_instance_mb,
           n.annotation_series_mb
      FROM slides s
      JOIN annotations n USING (slide_series_uid)
      JOIN base_level b USING (slide_series_uid)
    """


def resolve_candidates(client, collection: str) -> pd.DataFrame:
    """Query IDC for every annotated slide in *collection* paired with its base-level instance."""
    for index_name in ("ann_index", "sm_instance_index"):
        client.fetch_index(index_name)
    candidates = client.sql_query(_candidate_query(collection))
    logger.info(
        "IDC returned %d annotated slide(s) across %d patient(s) and %d tissue source site(s)",
        len(candidates),
        candidates["patient_id"].nunique(),
        candidates["tss"].nunique(),
    )
    return candidates


def select_subset(
    candidates: pd.DataFrame,
    sites: list[str],
    slides_per_site: int,
    max_slide_mb: float,
    idc_index_version: str,
) -> pd.DataFrame:
    """Pick *slides_per_site* slides for each site, deterministically and one slide per patient.

    Ordering is by (download size, slide_series_uid) rather than a seeded shuffle: the tie-break is a
    UID, so the result is stable without carrying a seed, and preferring small slides keeps the
    tutorial download tractable. That biases the subset toward smaller tissue sections — stated in the
    README, and the reason the cross-site comparison is framed as a demonstration rather than a result.

    One slide per patient matters twice over: it stops a single patient's staining dominating a site's
    score, and it guarantees no patient appears in two sites (which would leak across the federation
    boundary the tutorial is demonstrating).
    """
    missing = sorted(set(sites) - set(candidates["tss"].unique()))
    if missing:
        raise SystemExit(
            f"Tissue source site(s) {missing} have no annotated slides in this collection. "
            f"Available (top 10 by slide count): "
            f"{candidates['tss'].value_counts().head(10).to_dict()}"
        )

    affordable = candidates[candidates["slide_instance_mb"] <= max_slide_mb]
    logger.info(
        "%d of %d candidate slides are within the %.0f MB base-level cap",
        len(affordable),
        len(candidates),
        max_slide_mb,
    )

    selected: list[pd.DataFrame] = []
    for position, tss in enumerate(sites, start=1):
        site_name = f"Trust_{position}"
        pool = affordable[affordable["tss"] == tss]
        # One slide per patient: order within the patient first, then keep that patient's smallest.
        pool = pool.sort_values(["slide_instance_mb", "slide_series_uid"]).drop_duplicates("patient_id")
        chosen = pool.head(slides_per_site).copy()
        if len(chosen) < slides_per_site:
            raise SystemExit(
                f"Site {tss} has only {len(chosen)} eligible slide(s) under the {max_slide_mb:.0f} MB "
                f"cap, fewer than the {slides_per_site} requested. Raise --max-slide-mb, lower "
                f"--slides-per-site, or choose another site."
            )
        chosen["site"] = site_name
        chosen["accession_id"] = chosen["slide_series_uid"]
        selected.append(chosen)
        logger.info(
            "%s <- TSS %s: %d slide(s), %d patient(s), %.0f MB slides + %.0f MB annotations",
            site_name,
            tss,
            len(chosen),
            chosen["patient_id"].nunique(),
            chosen["slide_instance_mb"].sum(),
            chosen["annotation_series_mb"].sum(),
        )

    manifest = pd.concat(selected, ignore_index=True)
    manifest["idc_index_version"] = idc_index_version
    return manifest[MANIFEST_COLUMNS]


def _find_downloaded_file(staging: Path) -> Path | None:
    """Return the largest regular file under *staging*, or ``None`` if there is none.

    idc-index writes the object under its ``dirTemplate`` hierarchy and does **not** give it a
    ``.dcm`` suffix — the leaf is named like ``SM_<SeriesInstanceUID>`` with no extension. Matching on
    extension therefore finds nothing, so select by size instead and let the caller validate that what
    came back is really DICOM.
    """
    files = [p for p in staging.rglob("*") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


def _flatten_download(staging: Path, destination: Path) -> None:
    """Move the file idc-index wrote under its hierarchy to *destination*.

    ``download_dicom_*`` lays downloads out by ``dirTemplate`` (collection/patient/study/series). The
    FLIP dev data contract is flatter — ``get_by_accession_number`` hands the app
    ``<DEV_IMAGES_DIR>/<accession_id>`` and the app globs inside it — so each download is collapsed to
    one predictable filename.
    """
    source = _find_downloaded_file(staging)
    if source is None:
        raise RuntimeError(f"idc-index wrote no file under {staging}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))


def _fetch(staging: Path, destination: Path, download) -> None:
    """Place *destination*, reusing anything already staged before spending bandwidth again.

    A previous interrupted run can leave a complete multi-hundred-MB object in ``_staging``; wiping it
    unconditionally would re-download it. So flatten what is there first and only call *download* when
    the staging area is genuinely empty.
    """
    existing = _find_downloaded_file(staging) if staging.exists() else None
    if existing is not None:
        logger.info("Reusing already-staged download %s", existing.name)
        _flatten_download(staging, destination)
        return
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    download()
    _flatten_download(staging, destination)


def download_manifest(client, manifest: pd.DataFrame, out_dir: Path) -> None:
    """Download each manifest row's base-level slide instance and its annotation series."""
    total = len(manifest)
    for position, row in enumerate(manifest.itertuples(index=False), start=1):
        accession_dir = out_dir / row.site / "accession-resources" / row.accession_id
        slide_path = accession_dir / SLIDE_FILENAME
        annotation_path = accession_dir / ANNOTATION_FILENAME
        if slide_path.exists() and annotation_path.exists():
            logger.info("[%d/%d] %s already present, skipping", position, total, row.accession_id)
            continue

        logger.info(
            "[%d/%d] %s (%s, %.0f MB slide + %.0f MB annotations)",
            position,
            total,
            row.accession_id,
            row.site,
            row.slide_instance_mb,
            row.annotation_series_mb,
        )
        staging = accession_dir / "_staging"
        if not slide_path.exists():
            _fetch(
                staging,
                slide_path,
                lambda: client.download_dicom_instance(
                    sopInstanceUID=row.slide_sop_instance_uid, downloadDir=str(staging), quiet=True
                ),
            )
        if not annotation_path.exists():
            _fetch(
                staging,
                annotation_path,
                lambda: client.download_dicom_series(
                    seriesInstanceUID=row.annotation_series_uid, downloadDir=str(staging), quiet=True
                ),
            )
        shutil.rmtree(staging, ignore_errors=True)


def write_site_dataframes(manifest: pd.DataFrame, out_dir: Path) -> None:
    """Emit one ``dataframe.csv`` per site — the stand-in for the trust's cohort query result.

    ``FLIPStandardDev.get_dataframe`` reads this file and requires an ``accession_id`` column; the
    other columns ride along for the evaluator. In production the same columns would come from the
    OMOP cohort query instead.
    """
    for site, rows in manifest.groupby("site", sort=True):
        destination = out_dir / str(site) / "dataframe.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(destination, index=False)
        logger.info("Wrote %s (%d row(s))", destination, len(rows))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--resolve", action="store_true", help="Query IDC and select a fresh subset.")
    source.add_argument("--manifest", type=Path, help="Reproduce a pinned subset from this manifest CSV.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="IDC collection id.")
    parser.add_argument(
        "--sites",
        default=",".join(DEFAULT_SITES),
        help="Comma-separated TCGA tissue source site codes, one per FLIP site, in Trust_N order.",
    )
    parser.add_argument("--slides-per-site", type=int, default=DEFAULT_SLIDES_PER_SITE)
    parser.add_argument(
        "--max-slide-mb",
        type=float,
        default=400.0,
        help=(
            "Skip slides whose base-resolution instance exceeds this. Base-level instances run to "
            "3 GB (median ~890 MB), so this cap is what keeps the tutorial download tractable -- and "
            "it is why the default sites are A8/A7 rather than the largest ones."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "idc_pathology")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, but download nothing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    out_dir: Path = args.out_dir

    import idc_index_data
    from idc_index import IDCClient

    client = IDCClient.client()
    # The index data release the selection was resolved against. Pinning it is the point of the
    # manifest: IDC issues versioned releases and series come and go, so "the same criteria" can
    # silently resolve to a different subset later.
    idc_version = str(getattr(idc_index_data, "__version__", "unknown"))

    if args.manifest:
        manifest = pd.read_csv(args.manifest, dtype={"tss": str})
        logger.info("Reproducing pinned selection: %d slide(s) from %s", len(manifest), args.manifest)
    else:
        sites = [s.strip().upper() for s in args.sites.split(",") if s.strip()]
        candidates = resolve_candidates(client, args.collection)
        manifest = select_subset(candidates, sites, args.slides_per_site, args.max_slide_mb, idc_version)

    slide_mb = manifest["slide_instance_mb"].sum()
    annotation_mb = manifest["annotation_series_mb"].sum()
    logger.info(
        "Selection: %d slide(s), %d patient(s), %d site(s) -- %.0f MB total download",
        len(manifest),
        manifest["patient_id"].nunique(),
        manifest["site"].nunique(),
        slide_mb + annotation_mb,
    )

    if args.dry_run:
        print(manifest.to_string(index=False))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    logger.info("Wrote %s", manifest_path)

    download_manifest(client, manifest, out_dir)
    write_site_dataframes(manifest, out_dir)
    logger.info("Done. Data root: %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
