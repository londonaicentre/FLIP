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


"""Draw the README figure: one CT slice, and the same slice with TotalSegmentator's mask over it.

Run after a simulator run (``make run``), which leaves the masks in ``DEV_MASKS_DIR``:

    make overlay-figure

The figure reads the voxels with nibabel directly and turns both volumes to the same orientation for
display. It is a picture of the data and of the stored mask, not of what the model was given. The
script refuses a mask that is not on its image's voxel grid, so a misplaced overlay fails here and is
not drawn.
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import nibabel as nib  # noqa: E402
import numpy as np  # noqa: E402


def first_pair(images_dir: Path, masks_dir: Path) -> tuple[Path, Path]:
    """The first mask in name order, and the image it was predicted from."""
    for mask in sorted(masks_dir.rglob("pred_*.nii.gz")):
        name = mask.name.replace("pred_", "input_", 1)
        images = sorted((images_dir / mask.parent.name).rglob(name))
        if images:
            return images[0], mask
    raise SystemExit(f"No pred_*.nii.gz with a matching image under {masks_dir}. Run `make run` first.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--masks-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "ct_and_mask.png")
    args = parser.parse_args()

    image_path, mask_path = first_pair(args.images_dir, args.masks_dir)
    image, mask = nib.load(str(image_path)), nib.load(str(mask_path))
    if image.shape != mask.shape or not np.allclose(image.affine, mask.affine, atol=1e-3):
        raise SystemExit(f"{mask_path.name} is not on the voxel grid of {image_path.name}")

    # Same orientation for both, then the coronal slice that holds the most labelled voxels.
    ct = np.asanyarray(nib.as_closest_canonical(image).dataobj)
    labels = np.asanyarray(nib.as_closest_canonical(mask).dataobj).astype(int)
    slice_index = int((labels > 0).sum(axis=(0, 2)).argmax())
    ct_slice = np.rot90(ct[:, slice_index, :])
    label_slice = np.rot90(labels[:, slice_index, :])
    spacing = nib.as_closest_canonical(image).header.get_zooms()

    figure, axes = plt.subplots(1, 2, figsize=(8, 5), dpi=110)
    for axis, title in zip(axes, ("CT (input)", f"Mask over the CT ({len(np.unique(labels)) - 1} structures)")):
        axis.imshow(ct_slice, cmap="gray", vmin=-200, vmax=300, aspect=spacing[2] / spacing[0])
        axis.set_title(title, fontsize=10)
        axis.axis("off")
    # Twenty colours reused in turn, so neighbouring label values do not get neighbouring shades.
    axes[1].imshow(
        np.ma.masked_where(label_slice == 0, label_slice % 20),
        cmap="tab20",
        alpha=0.6,
        interpolation="nearest",
        aspect=spacing[2] / spacing[0],
    )
    figure.tight_layout()
    figure.savefig(args.output)
    print(f"Wrote {args.output} from {mask_path.parent.name}")


if __name__ == "__main__":
    main()
