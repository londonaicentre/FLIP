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

"""Client-API evaluator for the 3D spleen segmentation evaluation tutorial.

This is the NVFLARE Client-API counterpart of the legacy ``FLIP_EVALUATOR(Executor)``. The server
(``CrossSiteModelEval`` + ``EvaluationModelLocator``, wired by ``FlipEvalRecipe``) loads the uploaded
checkpoint and broadcasts it to every client as a single ``FLModel``; this script implements the
canonical ``is_evaluate()`` loop — receive the global model, score it on the local cohort, and send back
**aggregate-only** metrics. There is no ``Executor``/``Shareable`` plumbing and no multi-model
``COLLECTION`` unwrapping; the weights arrive as ``input_model.params``.

Only cohort-mean metrics are returned (``mean_dice``, ``mean_hausdorff_95``, ``mean_surface_dice``,
``mean_iou``). Per-sample (row-level) scores are deliberately never produced or sent: a per-patient list
would leak the exact evaluation cohort size and be linkable to individual patients.
"""

import argparse
import json
import logging
from pathlib import Path

import nibabel as nib
import nvflare.client as flare
import torch
from flip import FLIP
from flip.constants import ResourceType
from models import get_model
from monai.data import DataLoader, Dataset, decollate_batch
from monai.metrics import DiceMetric, HausdorffDistanceMetric, MeanIoU, SurfaceDiceMetric
from monai.networks.utils import one_hot
from monai.transforms import AsDiscrete
from tqdm import tqdm
from transforms import get_eval_transforms, get_sliding_window_inferer

logger = logging.getLogger(__name__)

# Surface tolerance in voxels for the normalized Surface Dice metric: the boundary deviation treated as
# acceptable, one entry per foreground class (spleen). Tune per dataset/voxel spacing.
SURFACE_DICE_TOLERANCE_VOXELS = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    NVFlare's TaskScriptRunner whitespace-splits ``task_script_args``, so the SQL query (which can
    contain spaces) is plumbed via the top-level ``query`` key in ``config/config_fed_client.json``
    rather than as a CLI flag. In dev/simulator mode this is ignored by ``flip.get_dataframe``.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def build_datalist(flip: FLIP, dataframe, project_id: str) -> list[dict]:
    """Return MONAI-compatible {image, label} items for every matched input/label pair in the cohort.

    Evaluation-only: every matched image/label pair in this client's cohort is scored (no held-out split).
    """
    datalist: list[dict] = []
    images_found = 0

    for accession_id in tqdm(dataframe["accession_id"], desc="Preparing dataset", unit="accession"):
        try:
            accession_folder_path = flip.get_by_accession_number(
                project_id, accession_id, resource_type=[ResourceType.NIFTI]
            )
        except Exception as err:
            logger.info("⚠️ Could not fetch images for accession_id=%s: %s", accession_id, err)
            continue

        # get all images in the accession folder that match the pattern "input_*.nii.gz"
        all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
        images_found += len(all_images)

        for img in all_images:
            seg = str(img).replace("/input_", "/label_")

            if not Path(seg).exists():
                logger.info("⚠️ No matching segmentation for %s", img.name)
                continue

            try:
                img_header = nib.load(str(img))
                seg_header = nib.load(seg)
            except nib.filebasedimages.ImageFileError as err:
                logger.info("⚠️ Invalid image pair for %s: %s", img.name, err)
                continue

            # Some QC checks to ensure the image and segmentation are valid and match
            # check is 3D and at least 128x128x128 in size and seg is the same shape as the image
            if len(img_header.shape) != 3:
                logger.info("⚠️ Skipping non-3D image %s", img.name)
                continue

            if img_header.shape != seg_header.shape:
                logger.info(
                    "⚠️ Shape mismatch for %s: image=%s label=%s",
                    img.name,
                    img_header.shape,
                    seg_header.shape,
                )
                continue

            datalist.append({"image": str(img), "label": seg})

    logger.info("Dataset ready: %d image/label pairs - evaluating all of them.", len(datalist))

    # Fail here rather than letting an empty dataset surface downstream as torch's opaque
    # "num_samples should be a positive integer value, but got num_samples=0".
    if not datalist:
        raise RuntimeError(
            f"No image/label pairs found: {images_found} input_*.nii.gz image(s) across "
            f"{len(dataframe['accession_id'])} accession(s), none with a matching label_*.nii.gz. "
            "Supervised evaluation needs a label beside each image in XNAT — was the data-enrichment step "
            "run, and did it run after DICOM-to-NIfTI conversion? See the Data Enrichment user guide."
        )

    return datalist


def build_metrics() -> dict:
    """Build the aggregate (cohort-mean) evaluation metrics.

    Each MONAI metric uses ``include_background=False`` (score the foreground spleen class) and
    ``reduction='mean'``, so ``aggregate()`` yields a single cohort-level scalar. No per-sample
    (row-level) values are produced or exported.
    """
    return {
        "mean_dice": DiceMetric(include_background=False, reduction="mean"),
        "mean_hausdorff_95": HausdorffDistanceMetric(include_background=False, percentile=95, reduction="mean"),
        "mean_surface_dice": SurfaceDiceMetric(
            class_thresholds=[SURFACE_DICE_TOLERANCE_VOXELS], include_background=False, reduction="mean"
        ),
        "mean_iou": MeanIoU(include_background=False, reduction="mean"),
    }


def evaluate(model: torch.nn.Module, loader: DataLoader, device: torch.device, num_classes: int) -> dict:
    """Score the model over the local cohort and return aggregate (cohort-mean) metrics only."""
    model.eval()
    metrics = build_metrics()
    post_pred = AsDiscrete(argmax=True, to_onehot=num_classes)
    swi = get_sliding_window_inferer(sw_device=device)

    num_images = 0
    with torch.no_grad():
        for i, batch in enumerate(loader):
            images, labels = batch["image"].to(device), batch["label"].to(device)
            # Sliding-window inference over the whole volume, then softmax + one-hot for metric input.
            output = swi(inputs=images, network=model)
            output = torch.softmax(output, dim=1)
            output = torch.stack([post_pred(o) for o in decollate_batch(output)], 0)
            labels_one_hot = one_hot(labels, num_classes=num_classes)
            for metric in metrics.values():
                metric(output, labels_one_hot)
            num_images += images.shape[0]
            logger.info(f"Evaluation iteration {i}, images seen: {num_images}")

    # Aggregate each metric to a single cohort-level scalar (aggregate-only — never per-sample).
    results = {name: float(metric.aggregate().item()) for name, metric in metrics.items()}
    logger.info("Evaluation finished: " + ", ".join(f"{k}={v:.4f}" for k, v in results.items()))
    return results


def load_global_weights(model: torch.nn.Module, input_model: flare.FLModel) -> None:
    """Load the server-provided weights (sent over the validate task) onto the local model."""
    torch_weights = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
    model.load_state_dict(torch_weights)


def main() -> None:
    args = parse_args()
    config = load_config()
    num_classes = config["num_classes"]
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    model = get_model().to(device)

    flip = FLIP()
    dataframe = flip.get_dataframe(args.project_id, load_query())
    if "accession_id" not in dataframe.columns:
        raise ValueError("The dataframe must contain an 'accession_id' column.")

    datalist = build_datalist(flip, dataframe, args.project_id)
    loader = DataLoader(Dataset(datalist, transform=get_eval_transforms()), batch_size=1, shuffle=False)

    flare.init()
    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_evaluate():
            load_global_weights(model, input_model)
            metrics = evaluate(model, loader, device, num_classes)
            flare.send(flare.FLModel(metrics=metrics))
        else:
            logger.warning("Received a non-evaluation task; ignoring.")


if __name__ == "__main__":
    main()
