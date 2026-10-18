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

"""Tests for ``nvflare/image_classification/arkplus_fine_tuning/app_files/data_utils.py``."""

from __future__ import annotations

from arkplus_sim_cap_contract import CLASS_COLUMNS, NORMAL, ArkplusSimCapContract, cohort, load_app


class TestSimMaxSamples(ArkplusSimCapContract):
    """The simulator-only ``MAX_SAMPLES`` cap (see :mod:`arkplus_sim_cap_contract`)."""

    APP_ID = "nvflare_arkplus_fine_tuning"

    def test_capped_split_keeps_a_positive_per_lesion_in_train_and_val(self) -> None:
        """The default cap (128) still gives the label-aware split a positive of every lesion in val."""
        module = load_app(self.APP_ID)
        cfg = module.load_config()
        lesions = module.get_lesions(cfg)

        capped = module.cap_dataframe(cohort(rows_per_class=300), 128, CLASS_COLUMNS, ["Yes"], seed=42)
        datalist = [
            {
                "image": row["accession_id"],
                **module.get_labels_from_radiology_row(row, lesions, cfg["value_to_numerical"], NORMAL),
            }
            for _, row in capped.iterrows()
        ]
        train, val = module._label_aware_split(datalist, lesions.get_lesion_list(), float(cfg["VAL_SPLIT"]), seed=42)

        assert val, "the capped cohort left the validation split empty"
        for split in (train, val):
            for lesion in lesions.get_lesion_list():
                assert any(item[lesion] == 1 for item in split), f"no {lesion} positive in a split"
