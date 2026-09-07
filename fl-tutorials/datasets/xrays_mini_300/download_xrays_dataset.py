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

REPO_ID = "aicentreflip/flip-fl-base-test-data"
# The repo's internal layout nests the dataset under a folder matching the repo's own name.
REPO_SUBDIR = "flip-fl-base-test-data/xrays_mini_300"


def download_xrays_dataset(cache_dir, output_dir, repo_id=REPO_ID):
    """
    Downloads the x-ray reference dataset from Hugging Face and normalises it into the layout the FL tutorial
    harnesses read (accession-resources/, dataframe.csv).

    Args:
        cache_dir (str): Directory the raw Hugging Face snapshot is fetched into.
        output_dir (str): Target directory for the normalised dataset. Removed and recreated on each run.
        repo_id (str): Hugging Face dataset repo to pull from.
    """
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=cache_dir,
        allow_patterns=[f"{REPO_SUBDIR}/*"],
    )

    src_dir = os.path.join(cache_dir, REPO_SUBDIR)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    shutil.copytree(os.path.join(src_dir, "accession-resources"), os.path.join(output_dir, "accession-resources"))
    shutil.copy(
        os.path.join(src_dir, "sample_get_dataframe_response.csv"), os.path.join(output_dir, "dataframe.csv")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir", required=True, help="directory to fetch the raw Hugging Face snapshot into."
    )
    parser.add_argument(
        "--output-dir", required=True, help="target directory for the normalised dataset (gitignored)."
    )
    parser.add_argument("--repo-id", default=REPO_ID, help="Hugging Face dataset repo id.")
    args = parser.parse_args()

    print(f"⬇️  Downloading x-ray dataset from Hugging Face ({args.repo_id})...")
    download_xrays_dataset(cache_dir=args.cache_dir, output_dir=args.output_dir, repo_id=args.repo_id)
    print(
        f"✅ x-ray data ready at {os.path.abspath(args.output_dir)}/ — "
        "make -C fl-tutorials run-tutorial TUTORIAL=xray_classification"
    )
