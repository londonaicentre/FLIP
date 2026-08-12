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

"""Synthetic chest-radiograph-shaped DICOMs, and the dihedral algebra the tests assert with.

The fixture is synthesised rather than committed: a phantom is ~12 KB against ~640 KB for a
downsampled real study, it raises no provenance or PHI question, and it exercises the identical
code path — the transpose these tests pin is a property of the reader's axis convention, wholly
independent of pixel content.

Two properties of the phantom are load-bearing, and :func:`assert_dihedrally_asymmetric` checks
both rather than leaving them to inspection:

* **Non-square.** A square phantom makes a transpose shape-preserving, so the very defect under
  test would slip through anything that looks at shapes.
* **Asymmetric under all eight dihedral transforms**, not merely non-square. A mirror-symmetric
  phantom passes a ``Flipd`` unchanged; a monotone gradient correlates almost perfectly with its
  own transpose. The glyph below is an "F" — the conventional orientation marker, chosen because
  no rotation or reflection of it maps onto any other.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, RLELossless, generate_uid

# Secondary Capture, matching the synthetic radiographs the trust PACS serves in dev.
SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.7"

# Deliberately not square, and deliberately portrait: (rows, columns) with rows > columns, so a
# transposed read is a different shape as well as different values.
PHANTOM_ROWS = 96
PHANTOM_COLUMNS = 64


def make_phantom(rows: int = PHANTOM_ROWS, columns: int = PHANTOM_COLUMNS) -> np.ndarray:
    """Return a ``(rows, columns)`` uint16 array with no dihedral symmetry.

    Args:
        rows (int): Number of rows (the first axis, as DICOM ``PixelData`` is indexed).
        columns (int): Number of columns (the second axis).

    Returns:
        np.ndarray: uint16 array shaped ``(rows, columns)``.
    """
    row_index, column_index = np.mgrid[0:rows, 0:columns]
    # A soft, non-separable background so the phantom is not a flat field with a glyph on it.
    image = 4000.0 * (row_index / rows) + 9000.0 * (column_index / columns) ** 2

    def fill(row_span: tuple[float, float], column_span: tuple[float, float], value: float) -> None:
        row_slice = slice(int(row_span[0] * rows), int(row_span[1] * rows))
        column_slice = slice(int(column_span[0] * columns), int(column_span[1] * columns))
        image[row_slice, column_slice] = value

    # An "F": stem, top arm, shorter middle arm. Chirally asymmetric, so it distinguishes a
    # transpose from a rotation — which a Rotate90/Flip "fix" does not.
    fill((0.15, 0.85), (0.20, 0.32), 58000.0)
    fill((0.15, 0.24), (0.20, 0.78), 58000.0)
    fill((0.45, 0.53), (0.20, 0.58), 58000.0)
    # One bright marker off-axis, so the phantom stays asymmetric even where the glyph is absent.
    fill((0.88, 0.96), (0.70, 0.92), 40000.0)

    return np.clip(image, 0, 65535).astype(np.uint16)


def dihedral_variants(image: np.ndarray) -> OrderedDict[str, np.ndarray]:
    """Return the eight dihedral (D4) variants of a 2-D array, keyed by name.

    ``identity`` is first. The names are what a failure message quotes back, so they say what the
    defect actually is ("looks like transpose") instead of only that two arrays differ.

    Args:
        image (np.ndarray): A 2-D array.

    Returns:
        OrderedDict[str, np.ndarray]: Name -> transformed array. Rotations of a non-square input
        have swapped axes, as does the transpose pair.
    """
    return OrderedDict(
        (
            ("identity", image),
            ("rot90", np.rot90(image, 1)),
            ("rot180", np.rot90(image, 2)),
            ("rot270", np.rot90(image, 3)),
            ("flip_vertical", image[::-1, :]),
            ("flip_horizontal", image[:, ::-1]),
            ("transpose", image.T),
            ("anti_transpose", np.rot90(image, 2).T),
        )
    )


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Return the absolute Pearson correlation of two equally-shaped arrays.

    Absolute, not signed: a ``MONOCHROME1`` inversion negates the correlation without changing the
    orientation, and orientation is what the caller is asking about. Intensity scaling and offset
    fall out of the normalisation, which is why this survives a resize and an ImageNet
    normalisation that :func:`np.array_equal` could not.

    Args:
        left (np.ndarray): First array.
        right (np.ndarray): Second array, same shape as ``left``.

    Returns:
        float: ``|r|`` in ``[0, 1]``; ``0.0`` when either array is constant.
    """
    a = np.asarray(left, dtype=np.float64).ravel()
    b = np.asarray(right, dtype=np.float64).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return abs(float(a @ b) / denominator)


def assert_dihedrally_asymmetric(image: np.ndarray, max_correlation: float = 0.9) -> None:
    """Fail unless ``image`` is distinguishable from every non-identity dihedral variant of itself.

    Args:
        image (np.ndarray): The 2-D fixture array to check.
        max_correlation (float): Highest ``|r|`` a non-identity variant may reach.

    Raises:
        AssertionError: If the array is square, or if some variant is too close to the original for
            the orientation assertions built on it to mean anything.
    """
    assert image.ndim == 2, f"expected a 2-D fixture, got shape {image.shape}"
    assert image.shape[0] != image.shape[1], (
        f"fixture is square {image.shape}: a transposed read would keep its shape, so any test "
        "built on it could pass on shape alone"
    )
    for name, variant in dihedral_variants(image).items():
        if name == "identity":
            continue
        if variant.shape != image.shape:
            # A rotation/transpose of a non-square array already differs in shape — asymmetric by
            # construction, and nothing to correlate against.
            continue
        score = correlation(image, variant)
        assert score < max_correlation, (
            f"fixture is nearly symmetric under {name} (|r| = {score:.4f}): a chain that applied "
            f"{name} to it would pass the orientation assertions anyway"
        )


def write_dicom(
    path: Path,
    pixels: np.ndarray,
    photometric_interpretation: str = "MONOCHROME2",
    compressed: bool = False,
) -> Path:
    """Write ``pixels`` out as a single-frame 16-bit greyscale DICOM.

    Args:
        path (Path): Destination file.
        pixels (np.ndarray): 2-D uint16 array, indexed ``(row, column)``.
        photometric_interpretation (str): ``MONOCHROME2`` or ``MONOCHROME1``.
        compressed (bool): Encapsulate as RLE Lossless. Chosen over JPEG variants because pydicom
            decodes (and encodes) RLE natively, so the compressed case needs no codec dependency
            while still exercising a genuinely different array path.

    Returns:
        Path: ``path``, for chaining.
    """
    dataset = Dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = SOP_CLASS_UID
    dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()

    dataset.SOPClassUID = SOP_CLASS_UID
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.Modality = "CR"
    dataset.PatientName = "Phantom^Orientation"
    dataset.PatientID = "PHANTOM-1"
    dataset.AccessionNumber = "PHANTOM-ACC-1"

    dataset.Rows, dataset.Columns = int(pixels.shape[0]), int(pixels.shape[1])
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = photometric_interpretation
    dataset.BitsAllocated = 16
    dataset.BitsStored = 16
    dataset.HighBit = 15
    dataset.PixelRepresentation = 0
    dataset.PixelData = pixels.tobytes()

    if compressed:
        dataset.compress(RLELossless, pixels)

    path.parent.mkdir(parents=True, exist_ok=True)
    dataset.save_as(str(path), enforce_file_format=True)
    return path


def read_pixel_data(path: Path) -> np.ndarray:
    """Return the reference array: ``PixelData`` as pydicom decodes it, no MONAI in the loop.

    Args:
        path (Path): A DICOM file.

    Returns:
        np.ndarray: 2-D array indexed ``(row, column)``.
    """
    return pydicom.dcmread(str(path)).pixel_array
