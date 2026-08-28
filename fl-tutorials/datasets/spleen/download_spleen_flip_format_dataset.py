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
REPO_SUBDIR = "flip-fl-base-test-data/spleen"
REPO_CHECKPOINT_SUBDIR = "flip-fl-base-test-data/checkpoints"
# The two outputs this script owns under output_dir. Only these are removed before a re-download:
# the MSD build (images/, dataframe.csv) shares data/spleen/ and must survive a re-run of this variant.
OUTPUTS = ("accession-resources", "sample_get_dataframe_response.csv")


def download_spleen_flip_format_dataset(cache_dir, output_dir, checkpoint_dir, repo_id=REPO_ID):
    """
    Downloads the pre-built FLIP-format spleen tree (fixed 6-case snapshot) and the evaluation-tutorial checkpoint
    from Hugging Face and normalises them into the layout the Flower compose stack mounts.

    Args:
        cache_dir (str): Directory the raw Hugging Face snapshot is fetched into.
        output_dir (str): The shared spleen data directory. Only this variant's own outputs (accession-resources/,
            sample_get_dataframe_response.csv) are removed and recreated, so an MSD build beside them survives.
        checkpoint_dir (str): Directory the evaluation checkpoint is copied into as model.pt.
        repo_id (str): Hugging Face dataset repo to pull from.
    """
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=cache_dir,
        allow_patterns=[f"{REPO_SUBDIR}/*", f"{REPO_CHECKPOINT_SUBDIR}/*"],
    )

    src_dir = os.path.join(cache_dir, REPO_SUBDIR)

    for output in OUTPUTS:
        target = os.path.join(output_dir, output)
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.exists(target):
            os.remove(target)
    os.makedirs(output_dir, exist_ok=True)

    shutil.copytree(os.path.join(src_dir, "accession-resources"), os.path.join(output_dir, "accession-resources"))
    shutil.copy(
        os.path.join(src_dir, "sample_get_dataframe_response.csv"),
        os.path.join(output_dir, "sample_get_dataframe_response.csv"),
    )

    os.makedirs(checkpoint_dir, exist_ok=True)
    shutil.copy(os.path.join(cache_dir, REPO_CHECKPOINT_SUBDIR, "model.pt"), os.path.join(checkpoint_dir, "model.pt"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-dir", required=True, help="directory to fetch the raw Hugging Face snapshot into."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="the shared spleen data directory (gitignored); only this variant's own outputs are replaced.",
    )
    parser.add_argument(
        "--checkpoint-dir", required=True, help="directory the evaluation checkpoint is copied into as model.pt."
    )
    parser.add_argument("--repo-id", default=REPO_ID, help="Hugging Face dataset repo id.")
    args = parser.parse_args()

    print(f"⬇️  Downloading FLIP-format spleen dataset from Hugging Face ({args.repo_id})...")
    download_spleen_flip_format_dataset(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        checkpoint_dir=args.checkpoint_dir,
        repo_id=args.repo_id,
    )
    print(
        f"✅ FLIP-format spleen data ready at {os.path.abspath(args.output_dir)}/ (+ checkpoint) — "
        "make -C fl-tutorials run-tutorial TUTORIAL=3d_spleen_segmentation FL_BACKEND=flower"
    )
