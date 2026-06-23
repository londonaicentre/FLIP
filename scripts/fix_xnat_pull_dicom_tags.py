#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
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
"""Hot-fix: backfill XNAT-required study tags on DICOMs already in a trust's Orthanc.

WHY THIS EXISTS
---------------
imaging-api's ``Study`` model (the XNAT DQR response,
``trust/imaging-api/imaging_api/routers/schemas.py``) currently marks
``StudyDescription`` (0008,1030) and ``ReferringPhysicianName`` (0008,0090) as
*required*. DICOM treats these as Type 3 / Type 2 (optional / may be empty), and
synthetic or anonymised studies routinely omit them. When a study is missing
either, imaging-api raises a pydantic validation error while parsing the DQR
result, the study is silently dropped, and the cohort pull ends with
"No studies found to import" — i.e. **the data never reaches XNAT** even though
it queried fine.

This script is the operator-side hot-fix: it walks every study already stored in
the trust's Orthanc and, for any that is missing one of those two tags, rewrites
the study IN PLACE with a sensible default, preserving the Study/Series/SOP
Instance UIDs and the AccessionNumber (so OMOP↔PACS identity is unchanged). After
running it, re-stage the project and the pull will import the studies.

It does NOT need the original DICOM files — it operates on what is already in
Orthanc, so a trust operator can run it standalone against their PACS.

The proper, durable fix is to relax the imaging-api ``Study`` model (make those
two fields ``Optional`` with ``default=""``); this script is the interim
workaround until that ships.

USAGE
-----
    uv run scripts/fix_xnat_pull_dicom_tags.py \
        --orthanc-url http://localhost:8042 \
        --orthanc-user admin --orthanc-password <pw>

Or read Orthanc creds from a trust kit / .env file (ORTHANC_USERNAME /
ORTHANC_PASSWORD; the URL is built from PACS_UI_PORT on localhost unless
--orthanc-url is given):

    uv run scripts/fix_xnat_pull_dicom_tags.py --env-file trust/.env.BDMS.production

Use ``--dry-run`` to list what would change without modifying anything, and
``--limit N`` to process only the first N studies (a safe first pass).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

# imaging-api Study-model-required tags that synthetic/anonymised DICOMs often omit.
# Only backfilled when absent/empty — a study that already has a real value is left alone.
DEFAULTS: dict[str, str] = {
    "StudyDescription": "Chest X-ray",
    "ReferringPhysicianName": "Unknown^Referring",
    # imaging-api's Patient model also requires a (non-empty) sex; anonymised
    # DICOMs often blank PatientSex (0010,0040). 'O' = Other (valid DICOM value).
    "PatientSex": "O",
}

# Preserve the StudyInstanceUID + AccessionNumber that XNAT DQR / the cohort pull
# key on (AccessionNumber is untouched by Replace). Series/SOP UIDs are regenerated:
# Orthanc rejects keeping all three with KeepSource=false ("you must set KeepSource
# to true"), and they aren't used to match the study on import.
KEEP_UIDS = ["StudyInstanceUID"]


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        if key.strip():
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--orthanc-url", default=None, help="Orthanc base URL (e.g. http://localhost:8042).")
    p.add_argument("--orthanc-user", default=None)
    p.add_argument("--orthanc-password", default=None)
    p.add_argument("--orthanc-host", default="localhost", help="Host for the env-built URL (default localhost).")
    p.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Read ORTHANC_USERNAME/PASSWORD/PACS_UI_PORT from a kit/.env file.",
    )
    p.add_argument("--study-description", default=DEFAULTS["StudyDescription"])
    p.add_argument("--referring-physician", default=DEFAULTS["ReferringPhysicianName"])
    p.add_argument("--patient-sex", default=DEFAULTS["PatientSex"], help="DICOM PatientSex when blank (M/F/O).")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N studies.")
    p.add_argument("--dry-run", action="store_true", help="Report what would change without modifying.")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    env = parse_env_file(args.env_file) if args.env_file and args.env_file.exists() else {}
    if args.env_file and not args.env_file.exists():
        print(f"ERROR: --env-file not found: {args.env_file}", file=sys.stderr)
        return 2

    user = args.orthanc_user or env.get("ORTHANC_USERNAME") or "admin"
    password = args.orthanc_password or env.get("ORTHANC_PASSWORD") or "admin"
    base = args.orthanc_url
    if not base and env.get("PACS_UI_PORT"):
        base = f"http://{args.orthanc_host}:{env['PACS_UI_PORT']}"
    if not base:
        print("ERROR: provide --orthanc-url (or PACS_UI_PORT via --env-file).", file=sys.stderr)
        return 2
    base = base.rstrip("/")
    auth = (user, password)

    wanted = {
        "StudyDescription": args.study_description,
        "ReferringPhysicianName": args.referring_physician,
        "PatientSex": args.patient_sex,
    }

    try:
        resp = requests.get(f"{base}/studies", auth=auth, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot reach Orthanc at {base}: {exc}", file=sys.stderr)
        return 2
    study_ids = resp.json()
    if args.limit is not None:
        study_ids = study_ids[: args.limit]

    # CodeQL (py/clear-text-logging) classifies the field names we operate on
    # (``PatientSex``, ``AccessionNumber``, …) and any value read from the
    # authenticated Orthanc response as sensitive/private. Logs therefore carry
    # only opaque progress counters — never the field-name list, the study id,
    # the accession number, or any tag value. The fields being backfilled are
    # documented in DEFAULTS and the module docstring.
    total = len(study_ids)
    mode = "DRY-RUN " if args.dry_run else ""
    print(f"{mode}Scanning {total} studies for missing required study tags ...")

    fixed = skipped = failed = 0
    for idx, sid in enumerate(study_ids, start=1):
        try:
            tags = requests.get(f"{base}/studies/{sid}", auth=auth, timeout=30).json().get("MainDicomTags", {})
        except Exception as exc:  # noqa: BLE001
            print(f"  ! study {idx}/{total}: cannot read tags: {exc}", file=sys.stderr)
            failed += 1
            continue

        replace = {tag: val for tag, val in wanted.items() if not str(tags.get(tag, "")).strip()}
        if not replace:
            skipped += 1
            continue

        if args.dry_run:
            print(f"  would fix study {idx}/{total} (missing one or more required tags)")
            fixed += 1
            continue
        try:
            r = requests.post(
                f"{base}/studies/{sid}/modify",
                auth=auth,
                json={"Replace": replace, "Keep": KEEP_UIDS, "Force": True, "KeepSource": False},
                timeout=300,
            )
            r.raise_for_status()
            fixed += 1
            if fixed % 100 == 0:
                print(f"  ... fixed {fixed}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! study {idx}/{total}: modify failed: {exc}", file=sys.stderr)
            failed += 1

    print("\n── Summary ──────────────────────────────────────────")
    print(f"  fixed   = {fixed} (missing tags backfilled)")
    print(f"  skipped = {skipped} (already had both tags)")
    print(f"  failed  = {failed}")
    if not args.dry_run and fixed:
        print("\nNext: re-stage the project on the hub — the pull will now import these studies.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
