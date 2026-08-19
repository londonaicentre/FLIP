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

"""Command-line entry point for the FLIP data enrichment upload.

Installed as ``flip-xnat``; also runnable as ``python -m flip.xnat``.
"""

import argparse
import logging
import sys

from flip.exceptions import XnatError
from flip.xnat.client import XnatClient
from flip.xnat.enrichment import DEFAULT_RENAME, DEFAULT_RESOURCE, read_manifest, run_enrichment


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="flip-xnat",
        description=(
            "Upload data-enrichment files (e.g. segmentation labels) into a FLIP project's XNAT. "
            "Run this on the Trust network, after the image pull and after DICOM-to-NIfTI conversion."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload = subparsers.add_parser("upload", help="Upload the files listed in a manifest CSV.")
    upload.add_argument(
        "--manifest",
        required=True,
        help="CSV with columns accession_id, file_path and optionally target_filename.",
    )

    project = upload.add_mutually_exclusive_group(required=True)
    project.add_argument(
        "--flip-project-id",
        help="FLIP Central Hub project id; the XNAT project is resolved by matching secondary_ID.",
    )
    project.add_argument("--project-id", help="XNAT project id, when you already know it.")

    upload.add_argument(
        "--credentials-file",
        action="append",
        dest="credentials_files",
        metavar="PATH",
        help='JSON file of {"server": ..., "user": ..., "password": ...}. '
        "Repeat once per Trust to enrich the whole roster in a single run — each Trust's XNAT holds "
        "only its own studies, so the manifest is self-selecting and nothing is uploaded twice. "
        "Defaults to the XNAT_HOST / XNAT_USER / XNAT_PASS environment variables.",
    )
    upload.add_argument("--resource", default=DEFAULT_RESOURCE, help=f"XNAT resource (default: {DEFAULT_RESOURCE}).")
    upload.add_argument(
        "--rename",
        default=":".join(DEFAULT_RENAME),
        metavar="SOURCE:TARGET",
        help=f"Prefix swap used to derive the target filename from the image (default: {':'.join(DEFAULT_RENAME)}).",
    )
    upload.add_argument("--overwrite", action="store_true", help="Replace files that are already present.")
    upload.add_argument("--dry-run", action="store_true", help="Resolve and report, but upload nothing.")
    upload.add_argument(
        "--allow-no-op",
        action="store_true",
        help="Exit 0 even when no destination was resolved. By default that is an error, because "
        "the commonest way enrichment silently achieves nothing is every scan being skipped.",
    )
    upload.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Also fail unless every scan in the visited project(s) now carries its enrichment file.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code; 0 on success, 1 on any failure.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)

    source_prefix, separator, target_prefix = args.rename.partition(":")
    if not separator or not source_prefix or not target_prefix:
        print(f"❌ --rename must look like SOURCE:TARGET, got {args.rename!r}", file=sys.stderr)
        return 1

    try:
        clients = (
            [XnatClient.from_config_file(path) for path in args.credentials_files]
            if args.credentials_files
            else [XnatClient.from_env()]
        )
        report = run_enrichment(
            clients,
            read_manifest(args.manifest),
            flip_project_id=args.flip_project_id,
            project_id=args.project_id,
            resource=args.resource,
            rename=(source_prefix, target_prefix),
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except XnatError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    print(report.render())
    return report.exit_code(allow_no_op=args.allow_no_op, require_full_coverage=args.require_full_coverage)


if __name__ == "__main__":
    raise SystemExit(main())
