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

"""The cxr OMOP conversion: pathology concepts, the observation table, and the per-trust split."""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
SCRIPT_PATH = DATASETS_DIR / "cxr" / "omop_convert_cxr.py"

# Every column transform_dicom_metadata_to_omop_tables() reads. `conditioning` and `pathologies` are
# the two that make this dataset different from spleen: they carry the synthetic radiology report
# that becomes the image_feature and observation rows.
METADATA_COLUMNS = [
    "FileName", "PatientID", "PatientSex", "PatientBirthDate", "AccessionNumber",
    "Modality", "StudyDate", "StudyTime", "StudyDescription",
    "StudyInstanceUID", "SeriesInstanceUID", "conditioning", "pathologies",
]


@pytest.fixture(autouse=True)
def _isolate_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test's output inside tmp_path.

    ``transform_dicom_metadata_to_omop_tables`` writes ``<omop_root>/omop/<project>/*.csv``, so a
    test that calls it without chdir'ing first leaves those files in the repository. Autouse rather
    than per-test, so a new test cannot forget.

    Every call below passes ``omop_root="."`` because that is what the CLI passes: the function's
    own default is ``""``, which makes the path absolute (``/omop/cxr_project``) and unusable. Only
    ``parse_args()``'s ``--omop_root`` default of ``"."`` saves the real entry point. Pinned as-is
    rather than corrected — the published export came through the CLI path.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture(scope="module")
def converter() -> ModuleType:
    """Import the converter with datasets/ on sys.path so `utils` resolves."""
    sys.path.insert(0, str(DATASETS_DIR))
    module_name = "fl_tutorials_under_test.omop_convert_cxr"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _write_metadata_csv(path: Path, rows: list[tuple[str, str]]) -> Path:
    """Write a metadata CSV, one row per (pathologies, conditioning) pair."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for index, (pathologies, conditioning) in enumerate(rows):
            nhs = f"{100000000 + index:09d}"
            writer.writerow(
                {
                    "FileName": f"sample_{index}", "PatientID": f"{nhs[:3]} {nhs[3:6]} {nhs[6:]}",
                    "PatientSex": "M", "PatientBirthDate": "19500101", "AccessionNumber": nhs,
                    "Modality": "CR", "StudyDate": "20200101", "StudyTime": "120000",
                    "StudyDescription": "Chest X-ray", "StudyInstanceUID": f"1.2.3.{index}",
                    "SeriesInstanceUID": f"1.2.3.{index}.1",
                    "conditioning": conditioning, "pathologies": pathologies,
                }
            )
    return path


@pytest.mark.parametrize(
    ("pathologies", "conditioning", "location", "negative"),
    [
        ("pleural_effusion", "Right-sided pleural effusion.", "right lung", 0),
        ("pleural_effusion", "Right sided pleural effusion.", "right lung", 0),
        ("pleural_effusion", "Left-sided pleural effusion.", "left lung", 0),
        ("no_pleural_effusion", "No pleural effusion.", "both lungs", 1),
        ("edema", "Congestive heart failure.", "heart", 0),
    ],
)
def test_laterality_and_negation_come_from_the_free_text(
    converter: ModuleType, pathologies: str, conditioning: str, location: str, negative: int
) -> None:
    """Location is inferred from the report text, not from a structured field.

    Pins the documented assumption: this synthetic dataset writes laterality as "right-sided" /
    "left sided" and nothing else, so a substring match is sufficient here and nowhere else.
    """
    from utils.omop_mappings import MAPPING_ANATOMIC_SITE

    (entry,) = converter.get_concepts_from_pathologies(pathologies, conditioning)

    assert entry["location"] == MAPPING_ANATOMIC_SITE[location]
    assert entry["negative"] == negative


def test_edema_variants_collapse_to_pulmonary_edema(converter: ModuleType) -> None:
    """Any pathology containing "edema" maps to the one pulmonary-edema concept."""
    from utils.omop_mappings import MAPPING_FINDING

    (entry,) = converter.get_concepts_from_pathologies("pulmonary_edema", "Pulmonary edema.")

    assert entry["concept"] == MAPPING_FINDING["pulmonary edema"]


def test_no_finding_maps_to_normal_lungs(converter: ModuleType) -> None:
    """"no_finding" is the healthy label, NOT a negated finding — negative stays 0."""
    from utils.omop_mappings import MAPPING_FINDING

    (entry,) = converter.get_concepts_from_pathologies("no_finding", "No abnormality.")

    assert entry["concept"] == MAPPING_FINDING["normal lungs"]


def test_an_unmapped_pathology_yields_an_empty_entry(converter: ModuleType, capsys) -> None:
    """Documented as-is: the upstream `except` swallows the KeyError and appends `{}`.

    The caller then reads entry["concept"] and dies with a KeyError naming the wrong thing, so an
    unmapped finding surfaces far from its cause. Pinned rather than fixed — changing it is a
    behaviour change to vendored code, and no published row exercises it.
    """
    assert converter.get_concepts_from_pathologies("pneumothorax", "Pneumothorax.") == [{}]
    assert "pneumothorax" in capsys.readouterr().out


def test_transform_produces_the_expected_tables(converter: ModuleType, tmp_path: Path) -> None:
    csv_path = _write_metadata_csv(
        tmp_path / "cxr_metadata.csv",
        [("pleural_effusion", "Right-sided pleural effusion.")] * 4,
    )

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path), omop_root=".")

    for name in ("person", "visit_occurrence", "procedure_occurrence", "image_occurrence"):
        assert name in tables, f"{name} is a canonical table the importer requires"
        assert len(tables[name]) == 4
        assert "trust" in tables[name].columns, "the trust column drives the split"
    assert "observation" in tables, "cxr publishes observation where spleen publishes measurement"
    assert "measurement" not in tables


def test_surrogate_ids_come_from_the_cxr_block(converter: ModuleType, tmp_path: Path) -> None:
    """cxr_project owns 1,000,000-1,999,999; spleen owns the next block up."""
    from utils.omop_ids import PROJECT_ID_BLOCKS

    csv_path = _write_metadata_csv(
        tmp_path / "cxr_metadata.csv", [("no_finding", "No abnormality.")] * 3
    )

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path), omop_root=".")

    base = PROJECT_ID_BLOCKS["cxr_project"]
    assert list(tables["visit_occurrence"]["visit_occurrence_id"]) == [base + 1, base + 2, base + 3]
    assert list(tables["image_occurrence"]["image_occurrence_id"]) == [base + 1, base + 2, base + 3]


def test_image_feature_and_observation_are_paired_by_a_derived_id(
    converter: ModuleType, tmp_path: Path
) -> None:
    """One image_feature and one observation per finding, sharing an id derived from the occurrence.

    The id is the image_occurrence_id with a two-digit finding index appended, which puts it well
    outside cxr_project's reserved block — see the note at that line in the converter.
    """
    csv_path = _write_metadata_csv(
        tmp_path / "cxr_metadata.csv",
        [("no_pleural_effusion,edema", "Right-sided pulmonary edema.")],
    )

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path), omop_root=".")

    occurrence_id = tables["image_occurrence"]["image_occurrence_id"].iloc[0]
    expected = [int(f"{occurrence_id}00"), int(f"{occurrence_id}01")]
    assert list(tables["image_feature"]["image_feature_id"]) == expected
    assert list(tables["observation"]["observation_id"]) == expected


def test_observation_value_records_presence_and_absence(converter: ModuleType, tmp_path: Path) -> None:
    """A negated finding is published as an explicit "no", not as a missing row."""
    from utils.omop_mappings import MAPPING_YES_NO

    csv_path = _write_metadata_csv(
        tmp_path / "cxr_metadata.csv",
        [("no_pleural_effusion,edema", "Right-sided pulmonary edema.")],
    )

    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path), omop_root=".")
    observation = tables["observation"]

    assert list(observation["value_as_concept_id"]) == [MAPPING_YES_NO["no"], MAPPING_YES_NO["yes"]]
    assert list(observation["value_as_number"]) == [0.0, 1.0]


def test_split_writes_one_directory_per_trust_without_the_trust_column(
    converter: ModuleType, tmp_path: Path
) -> None:
    csv_path = _write_metadata_csv(
        tmp_path / "cxr_metadata.csv", [("no_finding", "No abnormality.")] * 4
    )
    tables = converter.transform_dicom_metadata_to_omop_tables(str(csv_path), omop_root=".")

    converter.split_data_into_trusts_and_copy_dicoms(tables, omop_root=".", copy_dicom=False)

    for trust in converter.TRUSTS:
        person_csv = tmp_path / "omop" / trust / converter.PROJECT / "person.csv"
        assert person_csv.is_file(), f"{trust} must get its own person.csv"
        header = person_csv.read_text().splitlines()[0]
        assert "trust" not in header.split(","), "the trust column is internal to the split"
