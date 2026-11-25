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


"""Client-API inference script: run TotalSegmentator on each CT in the cohort and keep the masks at the trust.

FLARE's Client API runs this script at each trust. It answers the recipe's one task by predicting a
multi-label mask for every converted CT in the cohort and storing it on the scan in the trust's XNAT.
It replies with counts only. Nothing is scored, because there are no labels: this is inference, not
evaluation.

The model is not part of the app. TotalSegmentator and the weights of its ``total`` task are in the FL
client image, so the ``models`` entry in ``config.json`` is empty and FLIP sends no checkpoint.
TotalSegmentator is imported inside the functions that use it, never at the top of this file: FLARE
also loads app files on the server, whose image does not have it.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import nvflare.client as flare
from flip import FLIP
from flip.constants import FlipConstants, ResourceType

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent.resolve()


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    return json.loads((APP_DIR / "config.json").read_text())


def load_query() -> str:
    """The cohort query. FLIP writes it into the client config, because FLARE splits script
    arguments on spaces. A local run ignores it."""
    client_config = APP_DIR.parent / "config" / "config_fed_client.json"
    try:
        return json.loads(client_config.read_text()).get("query", "")
    except Exception:
        return ""


def find_images(flip: FLIP, dataframe, project_id: str) -> list[dict]:
    """Every converted image in the cohort, as ``{accession_id, image}``."""
    images: list[dict] = []
    for accession_id in dataframe["accession_id"].astype(str):
        try:
            folder = flip.get_by_accession_number(project_id, accession_id, resource_type=[ResourceType.NIFTI])
        except Exception as err:
            logger.info("Could not fetch images for accession_id=%s: %s", accession_id, err)
            continue
        images += [{"accession_id": accession_id, "image": path} for path in sorted(folder.rglob("input_*.nii.gz"))]
    if not images:
        raise RuntimeError(f"No input_*.nii.gz found across {len(dataframe)} accessions.")
    return images


def site_share(images: list[dict]) -> list[dict]:
    """A simulated site's share of the local dataset. At a trust the cohort is already the trust's own."""
    if not FlipConstants.LOCAL_DEV:
        return images
    # The simulator names its sites site-1 … site-N, and every one of them reads the same folder.
    site = flare.get_site_name()
    index = int(site.rsplit("-", 1)[-1]) - 1 if site.rsplit("-", 1)[-1].isdigit() else 0
    return images[index :: int(os.environ.get("DEV_NUM_PARTITIONS", "2"))]


def predict(image_path: Path, options: dict) -> nib.Nifti1Image:
    """TotalSegmentator's multi-label mask for one CT, on the image's own voxel grid."""
    from totalsegmentator.python_api import totalsegmentator

    # Never `fast=True`: it has its own weights, which the FL client image does not hold, and an app
    # cannot download anything at a trust. `fastest` (a 6 mm model that is in the image) is the quick option.
    predicted = totalsegmentator(
        str(image_path),
        None,
        task=options.get("task", "total"),
        roi_subset=options.get("roi_subset"),
        ml=True,
        quiet=True,
        device=options.get("device", "gpu"),
        fastest=bool(options.get("fastest", False)),
    )
    image = nib.load(str(image_path))
    if predicted.shape != image.shape or not np.allclose(predicted.affine, image.affine, atol=1e-3):
        raise ValueError(f"the prediction for {image_path.name} is not on the image's voxel grid")
    header = image.header.copy()
    header.set_data_dtype(np.uint8)
    return nib.Nifti1Image(np.asanyarray(predicted.dataobj).astype(np.uint8), image.affine, header)


def _code(concept: dict | None) -> list[str] | None:
    return [concept["value"], concept["scheme"], concept["meaning"]] if concept else None


def mask_labels(mask: nib.Nifti1Image, task: str) -> dict[int, dict]:
    """What each label in the mask is: its name, SNOMED codes and colour, from TotalSegmentator's own maps.

    flip.add_mask writes it beside the mask as labels.json, for the trust's XNAT to draw the mask in OHIF.
    """
    from totalsegmentator.dicom_utils import load_color_mapping, load_snomed_mapping
    from totalsegmentator.map_to_binary import class_map

    snomed, colors = load_snomed_mapping(), load_color_mapping()
    labels = {}
    for value in np.unique(np.asanyarray(mask.dataobj)):
        name = class_map[task].get(int(value))
        if not name:
            continue
        codes = snomed.get(name, {})
        labels[int(value)] = {
            "name": name,
            "category": _code(codes.get("property_category")),
            "type": _code(codes.get("property_type")),
            "modifier": _code(codes.get("property_modifier")),
            "color": list(colors[name]) if name in colors else None,
            "algorithm": "TotalSegmentator",
        }
    return labels


def save_mask(flip: FLIP, project_id: str, item: dict, mask: nib.Nifti1Image, task: str) -> bool:
    """Keep one mask where the images are. True when it was stored.

    At a trust, flip.add_mask stores it on the image's scan in XNAT, as ``pred_*.nii.gz`` with a labels.json
    naming its labels, which the trust's XNAT can turn into a DICOM-SEG the OHIF viewer draws. FLIP's imaging
    service takes files only from ``<images>/<net>/upload/``, by a path relative to it, and refuses a file name
    the resource already holds, so a second run keeps the first run's mask. A failure to store a mask is
    counted, not raised: the other studies still run.
    """
    relative = f"{item['accession_id']}/{item['image'].name.replace('input_', 'pred_', 1)}"
    try:
        if FlipConstants.LOCAL_DEV:
            # Simulator only: there is no XNAT, so the mask goes to a local folder when one is given.
            if not os.environ.get("DEV_MASKS_DIR"):
                return False
            path = Path(os.environ["DEV_MASKS_DIR"]) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            nib.save(mask, str(path))
            return True
        # .../scans/<id>-<description>/resources/NIFTI/files/input_*.nii.gz
        parts = item["image"].parts
        scan_id = parts[parts.index("scans") + 1].split("-")[0]
        path = Path(os.environ.get("IMAGES_DIR", "/app/data/images")) / os.environ["NET_ID"] / "upload" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        nib.save(mask, str(path))
        flip.add_mask(project_id, item["accession_id"], scan_id, relative, mask_labels(mask, task))
        return True
    except Exception as err:
        logger.warning("The mask for %s was not saved: %s", item["accession_id"], err)
        return False


def infer(flip: FLIP, project_id: str, images: list[dict], config: dict) -> dict[str, float]:
    """Predict and store a mask for every image. Returns the counts sent to the hub."""
    options = config.get("totalsegmentator", {})
    predicted = failed = saved = empty = 0
    for index, item in enumerate(images, start=1):
        started = time.monotonic()
        try:
            mask = predict(item["image"], options)
        except Exception as err:
            failed += 1
            logger.warning("Study %d of %d failed: %s", index, len(images), err)
            continue
        predicted += 1
        if not np.any(np.asanyarray(mask.dataobj)):
            # None of the structures asked for is in this scan (a chest CT and pelvic muscles, say): there is
            # nothing to store, and that is not a failure to store it.
            empty += 1
            logger.info("Study %d of %d holds none of the structures asked for", index, len(images))
            continue
        saved += save_mask(flip, project_id, item, mask, options.get("task", "total"))
        logger.info("Study %d of %d done in %.0f s", index, len(images), time.monotonic() - started)
    if not predicted:
        raise RuntimeError(f"none of the {len(images)} studies could be predicted")
    return {
        "studies_predicted": float(predicted),
        "studies_failed": float(failed),
        "masks_saved": float(saved),
        "masks_empty": float(empty),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    args = parser.parse_args()
    config = load_config()

    flip = FLIP()
    dataframe = flip.get_dataframe(args.project_id, load_query())
    if "accession_id" not in dataframe.columns:
        raise ValueError("The dataframe must contain an 'accession_id' column.")
    images = find_images(flip, dataframe, args.project_id)

    flare.init()
    images = site_share(images)
    logger.info("%d images to predict", len(images))

    while flare.is_running():
        if flare.receive() is None:
            break
        # The recipe reuses the evaluation flow's task, so the inference task arrives as an evaluate task.
        if flare.is_evaluate():
            flare.send(flare.FLModel(metrics=infer(flip, args.project_id, images, config)))
        else:
            logger.warning("Received a task that is not the inference task; ignoring.")


if __name__ == "__main__":
    main()
