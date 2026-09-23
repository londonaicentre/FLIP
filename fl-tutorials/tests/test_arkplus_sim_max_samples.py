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

"""Pin the Ark+ tutorials' simulator-only ``MAX_SAMPLES`` cap.

The three Ark+ apps cap how many dataframe rows each simulated site reads so a plain ``make sim`` is
a quick smoke test. Two properties matter and are pinned here for every app:

* the cap never reaches a deployed job: only the ``LOCAL_DEV`` branch of ``_load_dataframe`` applies
  it, so a cohort served by the trust's data-access-api is returned whole whatever ``MAX_SAMPLES``
  says;
* the capped subset is deterministic and keeps every class represented, so the fine-tuning
  label-aware split still places a positive of every lesion in both train and val, and the
  per-lesion AUCs stay defined.

Fixtures are synthesised in-process; no dataset, GPU or FL image is needed.
"""

from __future__ import annotations

from types import ModuleType

import pandas as pd
import pytest
from tutorial_apps import DICOM_APPS

ARKPLUS_APPS = [app for app in DICOM_APPS if "arkplus" in app.app_id]

LESIONS = ["Effusion", "Consolidation", "Infiltration", "Lung Nodule or Mass", "Pneumothorax"]
NORMAL = "Lungs in normal arrangement"


def _cohort(rows_per_class: int = 40) -> pd.DataFrame:
    """A DECAF-shaped dataframe: ``rows_per_class`` single-positive rows per lesion, then normals."""
    records = []
    for index, positive in enumerate([*LESIONS, NORMAL] * rows_per_class):
        record = {"accession_id": f"ACC{index:05d}"}
        record.update({column: "Yes" if column == positive else "No" for column in [*LESIONS, NORMAL]})
        records.append(record)
    return pd.DataFrame.from_records(records)


@pytest.fixture(params=ARKPLUS_APPS, ids=[app.app_id for app in ARKPLUS_APPS])
def data_utils(request: pytest.FixtureRequest) -> ModuleType:
    """Each Ark+ app's ``data_utils`` module."""
    return request.param.load_module()


def test_all_three_arkplus_apps_are_covered() -> None:
    assert len(ARKPLUS_APPS) == 3


@pytest.mark.parametrize(("raw", "expected"), [(None, 0), ("", 0), ("  ", 0), ("0", 0), ("64", 64), (" 8 ", 8)])
def test_max_samples_env_parsing(
    data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int
) -> None:
    if raw is None:
        monkeypatch.delenv("MAX_SAMPLES", raising=False)
    else:
        monkeypatch.setenv("MAX_SAMPLES", raw)
    assert data_utils.get_sim_max_samples() == expected


@pytest.mark.parametrize("raw", ["-1", "abc", "1.5"])
def test_max_samples_env_rejects_garbage(data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("MAX_SAMPLES", raw)
    with pytest.raises(ValueError, match="MAX_SAMPLES"):
        data_utils.get_sim_max_samples()


@pytest.mark.parametrize("max_samples", [0, 240, 1000])
def test_cap_is_a_no_op_when_off_or_not_binding(data_utils: ModuleType, max_samples: int) -> None:
    df = _cohort()
    assert data_utils.cap_dataframe(df, max_samples, [*LESIONS, NORMAL], ["Yes"], seed=42) is df


def test_cap_is_deterministic_balanced_and_order_preserving(data_utils: ModuleType) -> None:
    df = _cohort()
    first = data_utils.cap_dataframe(df, 30, [*LESIONS, NORMAL], ["Yes"], seed=42)
    second = data_utils.cap_dataframe(df, 30, [*LESIONS, NORMAL], ["Yes"], seed=42)

    assert len(first) == 30
    pd.testing.assert_frame_equal(first, second)
    assert list(first.index) == sorted(first.index)
    assert (first[[*LESIONS, NORMAL]] == "Yes").sum().to_dict() == dict.fromkeys([*LESIONS, NORMAL], 5)


def test_cap_draws_rows_positive_for_nothing_as_their_own_class(data_utils: ModuleType) -> None:
    """A skewed cohort still hands a small cap every class, including rows no column marks positive."""
    df = pd.concat([_cohort(rows_per_class=2), _cohort(rows_per_class=50)[LESIONS[:1] + ["accession_id"]]])
    df = df.fillna("No").reset_index(drop=True)
    capped = data_utils.cap_dataframe(df, 12, [*LESIONS, NORMAL], ["Yes"], seed=0)

    counts = (capped[[*LESIONS, NORMAL]] == "Yes").sum()
    assert (counts >= 1).all(), counts.to_dict()
    assert len(capped) == 12


def _write_config_and_csv(data_utils: ModuleType, tmp_path, df: pd.DataFrame):
    csv = tmp_path / "dataframe.csv"
    df.to_csv(csv, index=False)
    return data_utils.load_config(), data_utils.SiteDataConfig(site_name="site-1", dataframe=str(csv))


def test_simulator_path_applies_the_cap(data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cfg, site_cfg = _write_config_and_csv(data_utils, tmp_path, _cohort())
    monkeypatch.setattr(data_utils, "_is_local_dev", lambda: True)

    monkeypatch.setenv("MAX_SAMPLES", "18")
    assert len(data_utils._load_dataframe(site_cfg, config=cfg)) == 18

    monkeypatch.setenv("MAX_SAMPLES", "0")
    assert len(data_utils._load_dataframe(site_cfg, config=cfg)) == 240


def test_deployed_path_is_never_capped(data_utils: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    cohort = _cohort()

    class _TrustAPI:
        def get_dataframe(self, project_id: str, query: str) -> pd.DataFrame:
            return cohort

    monkeypatch.setattr(data_utils, "_is_local_dev", lambda: False)
    monkeypatch.setattr(data_utils, "FLIP", _TrustAPI)
    monkeypatch.setenv("MAX_SAMPLES", "5")

    site_cfg = data_utils.SiteDataConfig(site_name="site-1")
    assert data_utils._load_dataframe(site_cfg, project_id="p", query="q", config=data_utils.load_config()) is cohort


def test_capped_fine_tuning_split_keeps_a_positive_per_lesion_in_train_and_val() -> None:
    """The fine-tuning default cap still gives the label-aware split a positive of every lesion in val."""
    (app,) = [app for app in ARKPLUS_APPS if app.app_id == "nvflare_arkplus_fine_tuning"]
    module = app.load_module()
    cfg = module.load_config()
    lesions = module.get_lesions(cfg)

    capped = module.cap_dataframe(_cohort(rows_per_class=300), 64, [*LESIONS, NORMAL], ["Yes"], seed=42)
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
