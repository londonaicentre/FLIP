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

"""Shared fixtures: the synthetic DICOM encodings, and the tutorial apps under test."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from dicom_phantom import make_phantom, write_dicom
from tutorial_apps import DICOM_APPS, TutorialApp

# Encodings whose array path through pydicom genuinely differs. MONOCHROME1 and MONOCHROME2 differ
# in polarity (which the reader leaves to the caller, so it must not disturb the orientation
# assertions); RLE Lossless is encapsulated, so the pixels arrive via the decoder rather than
# straight off the little-endian buffer.
_ENCODINGS: dict[str, dict[str, object]] = {
    "monochrome2": {"photometric_interpretation": "MONOCHROME2", "compressed": False},
    "monochrome1": {"photometric_interpretation": "MONOCHROME1", "compressed": False},
    "monochrome2_rle": {"photometric_interpretation": "MONOCHROME2", "compressed": True},
}


@pytest.fixture(scope="session")
def phantom() -> np.ndarray:
    """The reference pixel array every fixture DICOM is written from."""
    return make_phantom()


@pytest.fixture(scope="session", params=sorted(_ENCODINGS), ids=sorted(_ENCODINGS))
def dicom_path(request: pytest.FixtureRequest, phantom: np.ndarray, tmp_path_factory) -> Path:
    """A synthetic single-frame DICOM, one per encoding under test."""
    encoding = request.param
    directory = tmp_path_factory.mktemp("phantom-dicom")
    return write_dicom(directory / f"{encoding}.dcm", phantom, **_ENCODINGS[encoding])  # type: ignore[arg-type]


@pytest.fixture(params=DICOM_APPS, ids=[app.app_id for app in DICOM_APPS])
def dicom_app(request: pytest.FixtureRequest) -> TutorialApp:
    """Each tutorial app that reads 2-D DICOM through MONAI's LoadImaged."""
    return request.param
