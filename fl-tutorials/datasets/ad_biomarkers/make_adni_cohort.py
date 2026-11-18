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

"""Build the biomarker_causal_discovery cohort from a licence holder's own ADNI download.

ADNI (the Alzheimer's Disease Neuroimaging Initiative) is access-controlled — what its Data Use
Agreement requires is in the tutorial README's "Data" section — so this repository ships **code,
not data**: this script reads the five study tables a licence holder downloads from the LONI Image
and Data Archive (IDA) and writes the twelve-column CSV the tutorial's ``query.sql`` returns on the
platform, so the simulator and a deployed run read the same thing. The output must stay out of git: the default
location is the gitignored ``fl-tutorials/data/ad_biomarkers/`` both simulators look in.

The cohort rules are those of the centralised analysis the tutorial federates (N. Glazman,
``adni_fci``, ``main`` setting), so the result is the cohort behind the README's real-ADNI numbers
up to the ADNI release downloaded:

* the MRI anchors each participant: UCSF cross-sectional FreeSurfer 7 volumes, scans with 25 or
  more missing values dropped, the **earliest** scan with a matching plasma panel kept;
* plasma Aβ42/40, pTau217 (Fujirebio Lumipulse), NfL and GFAP (Quanterix Simoa) from the
  baseline (``VISCODE2 == 'bl'``) UPenn panel, within ±3 months of the scan, the closest draw
  if several; ``-4`` (failed assay / below detection) and any other non-positive value count as missing;
* demographics (first record per participant), APOE ε4 allele count from the genotype string,
  ADAS-Cog13 at baseline; ADNI's negative missing-value codes count as missing;
* age = scan year − birth year (ADNI publishes month/year of birth only); ``is_female`` from
  ``PTGENDER`` (1 = male, 2 = female);
* intracranial volume is FreeSurfer ``ST10CV``; hippocampal volume is left + right
  (``ST29SV`` + ``ST88SV``), missing if either side is;
* participants missing any of the eleven variables are dropped.

``person_id`` is a 1-based row number over participants sorted by RID — a stable surrogate that
carries no ADNI identifier. The per-site CSVs split it by ``person_id`` modulo the site count,
the same split the simulator's per-site files and the trust loader use.

Tables (IDA → *Download* → *Study Data*; file names carry the download date, e.g.
``PTDEMOG_14Oct2026.csv``, and are matched here by prefix):

====================  ==========================================  ===============================
flag                  IDA table                                   columns read
====================  ==========================================  ===============================
``--demographics``    Subjects → Demographics: ``PTDEMOG``          RID, PTGENDER, PTEDUCAT, PTDOB
``--apoe``            Biospecimen → ApoE Genotyping: ``APOERES``    RID, GENOTYPE
``--adas``            Assessments → Neuropsychological: ``ADAS``    RID, VISCODE2, TOTAL13
``--freesurfer``      Imaging → UCSF Cross-Sectional FreeSurfer     RID, EXAMDATE, ST10CV,
                      (7.x): ``UCSFFSX7``                           ST29SV, ST88SV (+ QC count)
``--plasma``          Biospecimen → Biospecimen Results: UPENN      RID, VISCODE2, EXAMDATE,
                      Plasma Biomarkers (Fujirebio & Quanterix):    AB42_F, AB40_F, pT217_F,
                      ``UPENN_PLASMA_FUJIREBIO_QUANTERIX``          NfL_Q, GFAP_Q
====================  ==========================================  ===============================

    make -C fl-tutorials convert-adni-cohort ADNI_DIR=~/adni_tables [OUT=/secure/flip-data/ad_biomarkers]

or directly:

    uv run --project ad_biomarkers python ad_biomarkers/make_adni_cohort.py \\
        --adni-dir ~/adni_tables --output-dir ../data/ad_biomarkers

Nothing here contacts the network, and the script prints counts only — never a row.

The cohort reaches OMOP through the trust loader (``trust/omop-db``'s ``omop_db_tools.adni_cohort``,
``make -C trust load-adni-cohort``), whose tests run the tutorial's actual ``query.sql`` over the rows
it writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

#: The column contract shared with query.sql (and the trust loader).
COLUMNS = [
    "person_id",
    "age",
    "is_female",
    "education_years",
    "apoe4_copies",
    "plasma_ab42_ab40_ratio",
    "plasma_ptau217",
    "plasma_nfl",
    "plasma_gfap",
    "icv_mm3",
    "hippocampus_mm3",
    "adas_cog13",
]

#: IDA table name → the prefix its CSV download carries (``<prefix>_<DDMonYYYY>.csv``).
TABLE_PREFIXES = {
    "demographics": "PTDEMOG",
    "apoe": "APOERES",
    "adas": "ADAS",
    "freesurfer": "UCSFFSX7",
    "plasma": "UPENN_PLASMA_FUJIREBIO_QUANTERIX",
}

#: FreeSurfer columns of the two volumes (UCSF cross-sectional dictionary: aparc Icv; aseg
#: Left/RightHippocampus). Summed with ``min_count`` so a missing side makes the volume missing.
ICV_COLUMNS = ["ST10CV"]
HIPPOCAMPUS_COLUMNS = ["ST29SV", "ST88SV"]
#: Scans with at least this many missing values across the whole FreeSurfer row are dropped
#: (the centralised analysis's QC rule).
MAX_MISSING_FREESURFER_VALUES = 25
#: UPenn plasma columns; ``-4`` marks a failed assay / value below detection, and no real assay value is
#: zero or negative, so every non-positive value counts as missing (the markers are log-transformed downstream).
PLASMA_MARKERS = ["AB42_F", "AB40_F", "pT217_F", "NfL_Q", "GFAP_Q"]
#: Baseline visit code, in the harmonised ``VISCODE2`` column every ADNI table carries.
BASELINE_VISIT = "bl"


class AdniTableError(ValueError):
    """A table is missing, ambiguous, or lacks a column the cohort needs."""


def find_table(adni_dir: Path, name: str) -> Path:
    """Locate one IDA download in ``adni_dir`` by its table prefix.

    Args:
        adni_dir (Path): Directory holding the downloaded CSVs.
        name (str): Key of :data:`TABLE_PREFIXES`.

    Returns:
        Path: The one matching CSV.

    Raises:
        AdniTableError: No file, or more than one (two download dates), matches the prefix.
    """
    prefix = TABLE_PREFIXES[name]
    matches = sorted(p for p in adni_dir.glob(f"{prefix}*.csv") if p.name.upper().startswith(prefix))
    if name == "adas":  # ADAS_… must not swallow the phase-split ADASSCORES table or similar
        matches = [p for p in matches if p.stem.upper() == prefix or p.stem.upper().startswith(prefix + "_")]
    if not matches:
        raise AdniTableError(f"no {prefix}*.csv in {adni_dir} (the IDA table {name!r}); pass --{name} explicitly")
    if len(matches) > 1:
        raise AdniTableError(f"several {prefix}*.csv in {adni_dir}: {[p.name for p in matches]}; pass --{name}")
    return matches[0]


def _read(path: Path, required: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise AdniTableError(f"{path.name} lacks the columns {missing} (has {len(frame.columns)})")
    return frame


def load_mri(path: Path) -> pd.DataFrame:
    """One row per FreeSurfer scan with the two volumes, earliest scan first."""
    raw = _read(path, ["RID", "EXAMDATE", *ICV_COLUMNS, *HIPPOCAMPUS_COLUMNS])
    mri = raw[raw.isnull().sum(axis=1) < MAX_MISSING_FREESURFER_VALUES]
    mri = mri.dropna(subset=["EXAMDATE", "RID"]).assign(EXAMDATE=lambda m: pd.to_datetime(m["EXAMDATE"]))
    mri = mri.sort_values("EXAMDATE", kind="stable")  # as dates, so a non-ISO export still orders correctly
    out = pd.DataFrame(
        {
            "RID": mri["RID"].astype(int),
            "EXAMDATE": mri["EXAMDATE"],
            "icv_mm3": mri[ICV_COLUMNS].sum(axis=1, min_count=len(ICV_COLUMNS)),
            "hippocampus_mm3": mri[HIPPOCAMPUS_COLUMNS].sum(axis=1, min_count=len(HIPPOCAMPUS_COLUMNS)),
        }
    )
    return out.reset_index(drop=True)


def load_plasma(path: Path) -> pd.DataFrame:
    """Baseline UPenn plasma panel: Aβ42/40 ratio, pTau217 (Fujirebio), NfL and GFAP (Quanterix)."""
    raw = _read(path, ["RID", "VISCODE2", "EXAMDATE", *PLASMA_MARKERS])
    plasma = raw[raw["VISCODE2"] == BASELINE_VISIT].copy()
    markers = plasma[PLASMA_MARKERS].apply(pd.to_numeric, errors="coerce")
    markers = markers.mask(markers <= 0)  # -4 (failed assay) and any other non-positive value are missing
    out = pd.DataFrame(
        {
            "RID": plasma["RID"].astype(int),
            "_date": pd.to_datetime(plasma["EXAMDATE"]),
            "plasma_ab42_ab40_ratio": markers["AB42_F"] / markers["AB40_F"],
            "plasma_ptau217": markers["pT217_F"],
            "plasma_nfl": markers["NfL_Q"],
            "plasma_gfap": markers["GFAP_Q"],
        }
    )
    return out.dropna().reset_index(drop=True)


def match_to_scan(mri: pd.DataFrame, plasma: pd.DataFrame, window_months: int) -> pd.DataFrame:
    """Keep each participant's earliest scan that has a plasma draw within ±``window_months``.

    If several draws qualify for that scan, the one closest in time is taken.
    """
    merged = mri.assign(_row=np.arange(len(mri))).merge(plasma, on="RID")
    offset = pd.DateOffset(months=window_months)
    in_window = (merged["_date"] >= merged["EXAMDATE"] - offset) & (merged["_date"] <= merged["EXAMDATE"] + offset)
    merged = merged[in_window].assign(_gap=lambda m: (m["_date"] - m["EXAMDATE"]).abs())
    merged = merged.sort_values(["_row", "_gap"], kind="stable").drop_duplicates("RID")
    return merged.drop(columns=["_row", "_date", "_gap"]).reset_index(drop=True)


def _first_valid(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Drop ADNI's negative missing-value codes and keep the first row per participant."""
    out = pd.DataFrame({"RID": frame["RID"].astype(int), column: pd.to_numeric(frame[column], errors="coerce")})
    out[column] = out[column].mask(out[column] < 0)
    return out.dropna().drop_duplicates("RID")


def load_demographics(path: Path) -> pd.DataFrame:
    """Sex, years of education and month/year of birth: the first record per participant."""
    raw = _read(path, ["RID", "PTGENDER", "PTEDUCAT", "PTDOB"]).drop_duplicates("RID")
    out = pd.DataFrame({"RID": raw["RID"].astype(int)})
    for column in ("PTGENDER", "PTEDUCAT"):
        values = pd.to_numeric(raw[column], errors="coerce")
        out[column] = values.mask(values < 0).to_numpy()
    out["birth_year"] = pd.to_datetime(raw["PTDOB"], format="%m/%Y", errors="coerce").dt.year.to_numpy()
    return out


def load_apoe(path: Path) -> pd.DataFrame:
    """APOE ε4 allele count from the genotype string (``'3/4'`` → 1, ``'4/4'`` → 2).

    Anything that is not an ``'a/b'`` pair of alleles 2–4 (ADNI's negative missing-value codes such as
    ``'-4'``) is missing, not counted: ``'-4'.count('4')`` would make an unknown genotype a carrier.
    """
    raw = _read(path, ["RID", "GENOTYPE"]).dropna(subset=["GENOTYPE"])
    raw = raw[raw["GENOTYPE"].astype(str).str.strip().str.fullmatch(r"[234]/[234]")]
    out = pd.DataFrame({"RID": raw["RID"].astype(int), "apoe4_copies": raw["GENOTYPE"].astype(str).str.count("4")})
    return out.drop_duplicates("RID")


def load_adas(path: Path) -> pd.DataFrame:
    """ADAS-Cog13 total at the baseline visit."""
    raw = _read(path, ["RID", "VISCODE2", "TOTAL13"])
    return _first_valid(raw[raw["VISCODE2"] == BASELINE_VISIT], "TOTAL13").rename(columns={"TOTAL13": "adas_cog13"})


def build_cohort(
    demographics: Path, apoe: Path, adas: Path, freesurfer: Path, plasma: Path, window_months: int = 3
) -> pd.DataFrame:
    """Assemble the twelve-column cohort from the five IDA tables.

    Returns:
        pd.DataFrame: :data:`COLUMNS`, one row per participant with every variable observed,
        sorted by RID with ``person_id`` numbered 1..n in that order.
    """
    cohort = match_to_scan(load_mri(freesurfer), load_plasma(plasma), window_months)
    for table in (load_demographics(demographics), load_apoe(apoe), load_adas(adas)):
        cohort = cohort.merge(table, on="RID", how="inner")

    cohort["age"] = cohort["EXAMDATE"].dt.year - cohort["birth_year"]
    cohort["is_female"] = cohort["PTGENDER"].map({1: 0, 2: 1})
    cohort = cohort.rename(columns={"PTEDUCAT": "education_years"})
    variables = [c for c in COLUMNS if c != "person_id"]
    cohort = cohort.dropna(subset=variables).sort_values("RID", kind="stable").reset_index(drop=True)
    cohort["person_id"] = np.arange(1, len(cohort) + 1)
    for column in ("age", "is_female", "education_years", "apoe4_copies"):
        cohort[column] = cohort[column].astype(int)
    return cohort[COLUMNS]


def write_outputs(frame: pd.DataFrame, output_dir: Path, num_sites: int) -> dict[str, int]:
    """Write dataframe.csv, the per-site splits and a counts-only summary; return the counts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "dataframe.csv", index=False)
    sizes = {}
    for s in range(1, num_sites + 1):
        site_dir = output_dir / f"site{s}"
        site_dir.mkdir(exist_ok=True)
        part = frame[frame["person_id"] % num_sites == (s - 1)]
        part.to_csv(site_dir / "dataframe.csv", index=False)
        sizes[f"site{s}"] = int(len(part))
    summary = {"participants": int(len(frame)), "sites": sizes, "columns": COLUMNS}
    (output_dir / "cohort_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def refuse_unignored_output(output_dir: Path) -> None:
    """Refuse an output directory inside a git checkout unless git ignores it: ADNI rows are never committed.

    The path is resolved first, so a symlink into a checkout is judged by where it lands. The
    checkout is the one holding the nearest existing ancestor, so a sibling directory whose name
    merely starts with the checkout's is not mistaken for part of it.
    """
    target = output_dir.expanduser().resolve()
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        top = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:  # no git on this host: nothing here can be committed from it
        return
    if top.returncode != 0:  # not inside a git checkout
        return
    root = Path(top.stdout.strip()).resolve()
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(target)], check=False)
    if ignored.returncode != 0:
        raise SystemExit(
            f"❌ --output-dir {target} is inside the git checkout {root} and not gitignored: ADNI data must never be "
            "committed. Write it under fl-tutorials/data/ (gitignored) or outside the checkout."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adni-dir", type=Path, help="directory of IDA downloads, tables found by prefix")
    for name, prefix in TABLE_PREFIXES.items():
        parser.add_argument(f"--{name}", type=Path, help=f"the {prefix} CSV (overrides --adni-dir)")
    parser.add_argument("--output-dir", type=Path, required=True, help="where to write (keep it outside the repo)")
    parser.add_argument("--num-sites", type=int, default=2, help="how many site splits to write (default 2)")
    parser.add_argument(
        "--match-window-months", type=int, default=3, help="plasma draw must lie within ± this of the scan (default 3)"
    )
    args = parser.parse_args(argv)
    refuse_unignored_output(args.output_dir)

    paths: dict[str, Path] = {}
    for name in TABLE_PREFIXES:
        explicit = getattr(args, name)
        if explicit is not None:
            paths[name] = explicit
        elif args.adni_dir is not None:
            paths[name] = find_table(args.adni_dir, name)
        else:
            parser.error(f"--{name} is required when --adni-dir is not given")

    frame = build_cohort(
        paths["demographics"],
        paths["apoe"],
        paths["adas"],
        paths["freesurfer"],
        paths["plasma"],
        args.match_window_months,
    )
    if frame.empty:
        raise SystemExit("the tables yielded no participant with every variable observed; check the file set")
    summary = write_outputs(frame, args.output_dir, args.num_sites)
    print(f"wrote {summary['participants']} participants to {args.output_dir} (per site: {summary['sites']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
