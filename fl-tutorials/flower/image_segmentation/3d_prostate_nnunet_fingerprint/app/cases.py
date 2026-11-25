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

"""The cases of this site's prostate cohort, for the fingerprint job type.

The only code the job needs from the researcher: which studies to read and how. Each study is
loaded exactly as the segmentation app loads it (same series selection, same affine-matched masks),
so the fingerprint describes the data that will be trained on. The platform's ``client_app.py``
turns what this yields into histograms and applies the release floors.
"""

from __future__ import annotations

from collections.abc import Iterator

import nibabel as nib
import numpy as np
from flip.flower.identity import partition_cohort
from flwr.app import Context

from app.data_loading import FLIP_BASE
from app.dataset import IMAGE_KEY, PZ_TZ_KEY, WHOLE_GLAND_KEY, build_loader, load_case
from app.models import load_config


def iter_cases(context: Context) -> Iterator[tuple[np.ndarray, np.ndarray, tuple[float, float, float]]]:
    """Yield ``(image, foreground, spacing)`` per study, in nnU-Net's axis order.

    ``image`` is ``(C, d, h, w)``, ``foreground`` the ``(d, h, w)`` boolean whole-gland mask and
    ``spacing`` the voxel size in mm as ``(d, h, w)``.
    """
    flip_utils = FLIP_BASE()
    flip_utils.project_id = str(context.run_config.get("flip-project-id", "prostate-fingerprint-tutorial"))
    flip_utils.query = str(context.run_config.get("flip-cohort-query", "*"))
    flip_utils.fetch_dataframe()
    # Slice the shared dev cohort so the simulated sites really differ; a no-op off LOCAL_DEV.
    flip_utils.dataframe = partition_cohort(flip_utils.dataframe, context)
    # No validation or test split: the fingerprint describes every case the site would train on.
    datalist, _ = flip_utils.get_case_list(load_config()["MODALITY"], val_split=0.0, test_split=0.0)

    loader = build_loader()
    for item in datalist:
        image, mask = load_case({key: item[key] for key in (IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY)}, loader)
        # The loader returns (C, h, w, d) in RAS.
        height, width, depth = nib.affines.voxel_sizes(np.asarray(image.affine, dtype=np.float64))
        yield image.numpy().transpose(0, 3, 1, 2), mask[0].numpy().transpose(2, 0, 1) > 0, (depth, height, width)
