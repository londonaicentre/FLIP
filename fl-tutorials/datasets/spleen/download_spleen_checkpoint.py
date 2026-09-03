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
"""Fetch the spleen evaluation-tutorial checkpoint from Hugging Face.

This used to also download a pre-built "FLIP-format" spleen tree — a fixed 6-case snapshot that
ignored ``NUM_CASES`` — which the Flower tutorials read while the NVFLARE ones read the MSD build.
The two trees were structurally identical (``subject_N/scans/{input,label}_spleen_N.nii.gz``), so
the snapshot was a small duplicate of data ``download_spleen_dataset.py`` already produces at up to
41 cases, and it capped a partitioned Flower simulation at 3 cases per site. Both backends now read
the one MSD build (FLIP#1158), leaving this script the checkpoint alone.
"""

import argparse
import os
import shutil

from huggingface_hub import snapshot_download

REPO_ID = "aicentreflip/flip-fl-base-test-data"
# The repo's internal layout nests content under a folder matching the repo's own name.
REPO_CHECKPOINT_SUBDIR = "flip-fl-base-test-data/checkpoints"


def download_spleen_checkpoint(cache_dir, checkpoint_dir, repo_id=REPO_ID):
    """Download the evaluation-tutorial checkpoint and place it as ``model.pt``.

    Args:
        cache_dir (str): Directory the raw Hugging Face snapshot is fetched into.
        checkpoint_dir (str): Directory the checkpoint is copied into as ``model.pt``.
        repo_id (str): Hugging Face dataset repo to pull from.
    """
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=cache_dir,
        allow_patterns=[f"{REPO_CHECKPOINT_SUBDIR}/*"],
    )
    os.makedirs(checkpoint_dir, exist_ok=True)
    shutil.copy(os.path.join(cache_dir, REPO_CHECKPOINT_SUBDIR, "model.pt"), os.path.join(checkpoint_dir, "model.pt"))
    print(f"✅ spleen evaluation checkpoint ready at {os.path.join(checkpoint_dir, 'model.pt')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True, help="directory to fetch the raw Hugging Face snapshot into.")
    parser.add_argument("--checkpoint-dir", required=True, help="directory the checkpoint is copied into as model.pt.")
    parser.add_argument("--repo-id", default=REPO_ID, help="Hugging Face dataset repo to pull from.")
    args = parser.parse_args()
    download_spleen_checkpoint(
        cache_dir=args.cache_dir, checkpoint_dir=args.checkpoint_dir, repo_id=args.repo_id
    )
