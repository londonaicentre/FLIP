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

import argparse
import os
import shutil

from huggingface_hub import snapshot_download


def download_arkplus_splits(repo_id, cache_dir, output_dir, sites, write_marker):
    """
    Downloads the given Ark+ chest X-ray splits from Hugging Face and normalises each into the layout the
    tutorials' .env.app files expect (<output_dir>/<site>/{accession-resources/, sample_get_dataframe_response.csv}).

    Args:
        repo_id (str): Hugging Face dataset repo to pull from.
        cache_dir (str): Directory the raw Hugging Face snapshot is fetched into.
        output_dir (str): Parent directory the per-site output folders are written under.
        sites (list[str]): Site folder names to fetch, e.g. ["site1", "site2"].
        write_marker (bool): Write a `.download-complete` marker into each site folder once every site has
            copied successfully. `arkplus_fine_tuning/Makefile`'s `reproduce-overhead` reads this marker (not
            just directory existence) so an interrupted download/copy can't be mistaken for complete data.
    """
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=cache_dir,
        allow_patterns=[f"{site}/*" for site in sites],
    )

    for site in sites:
        site_output = os.path.join(output_dir, site)
        if os.path.exists(site_output):
            shutil.rmtree(site_output)
        os.makedirs(site_output)

        src_dir = os.path.join(cache_dir, site)
        shutil.copytree(os.path.join(src_dir, "accession-resources"), os.path.join(site_output, "accession-resources"))
        shutil.copy(
            os.path.join(src_dir, "sample_get_dataframe_response.csv"),
            os.path.join(site_output, "sample_get_dataframe_response.csv"),
        )

    # Written last, only once every site above copied without error — see the write_marker docstring note.
    if write_marker:
        for site in sites:
            open(os.path.join(output_dir, site, ".download-complete"), "w").close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir", required=True, help="directory to fetch the raw Hugging Face snapshot into."
    )
    parser.add_argument(
        "--output-dir", required=True, help="parent directory for the per-site output folders (gitignored)."
    )
    parser.add_argument("--repo-id", required=True, help="Hugging Face dataset repo id.")
    parser.add_argument("--sites", required=True, nargs="+", help="site folder names to fetch, e.g. site1 site2.")
    parser.add_argument(
        "--label", default="", help="human-readable split name for the progress messages, e.g. 'training'."
    )
    parser.add_argument(
        "--next-step", default="", help="hint printed on success, e.g. the run-tutorial command to try next."
    )
    parser.add_argument(
        "--write-marker",
        action="store_true",
        help="write a .download-complete marker into each site folder once every site has copied.",
    )
    args = parser.parse_args()

    label = f"{args.label} " if args.label else ""
    print(f"⬇️  Downloading Ark+ {label}splits from Hugging Face ({args.repo_id})...")
    download_arkplus_splits(
        repo_id=args.repo_id,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        sites=args.sites,
        write_marker=args.write_marker,
    )
    sites_str = "{" + ",".join(args.sites) + "}"
    message = f"✅ Ark+ {label}data ready at {os.path.abspath(args.output_dir)}/{sites_str}/"
    if args.next_step:
        message += f" — {args.next_step}"
    print(message)
