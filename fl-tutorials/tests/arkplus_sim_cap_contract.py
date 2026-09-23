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

"""The Ark+ apps' simulator-only ``MAX_SAMPLES`` contract, shared by their ``test_data_utils.py``.

The three Ark+ apps cap how many dataframe rows each simulated site reads so a plain ``make sim`` is
a quick smoke test. Each app's own ``tests/nvflare/.../app_files/test_data_utils.py`` subclasses
:class:`ArkplusSimCapContract`, so the same two properties are pinned for every copy of
``data_utils.py``:

* the cap never reaches a deployed job: only the ``LOCAL_DEV`` branch of ``_load_dataframe`` applies
  it, so a cohort served by the trust's data-access-api is returned whole whatever ``MAX_SAMPLES``
  says;
* the capped subset is deterministic and keeps every class represented, so the per-lesion AUCs stay
  defined (and, for fine-tuning, the label-aware split still has a positive of every lesion to place).

The class is not named ``Test*``, so pytest collects it only through the subclasses. Fixtures are
synthesised in-process; no dataset, GPU or FL image is needed.
"""

from __future__ import annotations

from types import ModuleType

import pandas as pd
import pytest
from tutorial_apps import DICOM_APPS

LESIONS = ["Effusion", "Consolidation", "Infiltration", "Lung Nodule or Mass", "Pneumothorax"]
NORMAL = "Lungs in normal arrangement"
CLASS_COLUMNS = [*LESIONS, NORMAL]


def cohort(rows_per_class: int = 40) -> pd.DataFrame:
    """A DECAF-shaped dataframe: ``rows_per_class`` single-positive rows per lesion and per normal."""
    records = []
    for index, positive in enumerate(CLASS_COLUMNS * rows_per_class):
        record = {"accession_id": f"ACC{index:05d}"}
        record.update({column: "Yes" if column == positive else "No" for column in CLASS_COLUMNS})
        records.append(record)
    return pd.DataFrame.from_records(records)


def load_app(app_id: str) -> ModuleType:
    """Import the ``data_utils`` module of the ``DICOM_APPS`` entry ``app_id``."""
    (app,) = [app for app in DICOM_APPS if app.app_id == app_id]
    return app.load_module()


class ArkplusSimCapContract:
    """``MAX_SAMPLES`` tests every Ark+ ``data_utils.py`` must pass; subclasses set ``APP_ID``."""

    APP_ID: str

    @pytest.fixture
    def data_utils(self) -> ModuleType:
        return load_app(self.APP_ID)

    @pytest.mark.parametrize(("raw", "expected"), [(None, 0), ("", 0), ("  ", 0), ("0", 0), ("64", 64), (" 8 ", 8)])
    def test_max_samples_env_parsing(
        self, data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int
    ) -> None:
        if raw is None:
            monkeypatch.delenv("MAX_SAMPLES", raising=False)
        else:
            monkeypatch.setenv("MAX_SAMPLES", raw)
        assert data_utils.get_sim_max_samples() == expected

    @pytest.mark.parametrize("raw", ["-1", "abc", "1.5"])
    def test_max_samples_env_rejects_garbage(
        self, data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("MAX_SAMPLES", raw)
        with pytest.raises(ValueError, match="MAX_SAMPLES"):
            data_utils.get_sim_max_samples()

    @pytest.mark.parametrize("max_samples", [0, 240, 1000])
    def test_cap_is_a_no_op_when_off_or_not_binding(self, data_utils: ModuleType, max_samples: int) -> None:
        df = cohort()
        assert data_utils.cap_dataframe(df, max_samples, CLASS_COLUMNS, ["Yes"], seed=42) is df

    def test_cap_is_deterministic_balanced_and_order_preserving(self, data_utils: ModuleType) -> None:
        df = cohort()
        first = data_utils.cap_dataframe(df, 30, CLASS_COLUMNS, ["Yes"], seed=42)
        second = data_utils.cap_dataframe(df, 30, CLASS_COLUMNS, ["Yes"], seed=42)

        assert len(first) == 30
        pd.testing.assert_frame_equal(first, second)
        assert list(first.index) == sorted(first.index)
        assert (first[CLASS_COLUMNS] == "Yes").sum().to_dict() == dict.fromkeys(CLASS_COLUMNS, 5)

    def test_cap_draws_rows_positive_for_nothing_as_their_own_class(self, data_utils: ModuleType) -> None:
        """A skewed cohort still hands a small cap every class, including rows no column marks positive."""
        df = pd.concat([cohort(rows_per_class=2), cohort(rows_per_class=50)[LESIONS[:1] + ["accession_id"]]])
        df = df.fillna("No").reset_index(drop=True)
        capped = data_utils.cap_dataframe(df, 12, CLASS_COLUMNS, ["Yes"], seed=0)

        counts = (capped[CLASS_COLUMNS] == "Yes").sum()
        assert (counts >= 1).all(), counts.to_dict()
        assert len(capped) == 12

    def test_simulator_path_applies_the_cap(
        self, data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        csv = tmp_path / "dataframe.csv"
        cohort().to_csv(csv, index=False)
        cfg = data_utils.load_config()
        site_cfg = data_utils.SiteDataConfig(site_name="site-1", dataframe=str(csv))
        monkeypatch.setattr(data_utils, "_is_local_dev", lambda: True)

        monkeypatch.setenv("MAX_SAMPLES", "18")
        assert len(data_utils._load_dataframe(site_cfg, config=cfg)) == 18

        monkeypatch.setenv("MAX_SAMPLES", "0")
        assert len(data_utils._load_dataframe(site_cfg, config=cfg)) == 240

    def test_deployed_path_is_never_capped(self, data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        full = cohort()

        class _TrustAPI:
            def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
                return full

        monkeypatch.setattr(data_utils, "_is_local_dev", lambda: False)
        monkeypatch.setattr(data_utils, "FLIP", _TrustAPI)
        monkeypatch.setenv("MAX_SAMPLES", "5")

        site_cfg = data_utils.SiteDataConfig(site_name="site-1")
        loaded = data_utils._load_dataframe(site_cfg, project_id="p", query="q", config=data_utils.load_config())
        assert loaded is full
