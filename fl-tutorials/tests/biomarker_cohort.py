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
"""An invented cohort in the shape the biomarker tutorial's shipped config reads (no ADNI value)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def shipped_shape_cohort(n: int, seed: int = 0) -> pd.DataFrame:
    """Invented rows in the shipped config's columns, every discrete level well populated."""
    rng = np.random.default_rng(seed)
    age = rng.normal(73, 7, n)
    ptau = np.exp(rng.normal(-1.0, 0.5, n) + 0.02 * (age - 73))
    return pd.DataFrame(
        {
            "person_id": np.arange(1, n + 1),
            "age": age,
            "is_female": np.tile([0, 1], n // 2 + 1)[:n],
            "education_years": rng.integers(10, 21, n).astype(float),
            "apoe4_copies": np.tile([0, 1, 2], n // 3 + 1)[:n],
            "plasma_ab42_ab40_ratio": np.exp(rng.normal(-2.5, 0.1, n)),
            "plasma_ptau217": ptau,
            "plasma_nfl": np.exp(rng.normal(3.0, 0.4, n)),
            "plasma_gfap": np.exp(rng.normal(5.0, 0.4, n)),
            "icv_mm3": rng.normal(1.5e6, 1.5e5, n),
            "hippocampus_mm3": rng.normal(7000, 800, n) - 300 * np.log(ptau),
            "adas_cog13": np.clip(rng.normal(15, 7, n) + 3 * np.log(ptau), 0, 85),
        }
    )
