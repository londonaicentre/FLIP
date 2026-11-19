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

"""Unit tests for flip.monai.datalist (FLIP#1106). Every FLIP call is mocked; NIfTIs are synthesised."""

from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

nib = pytest.importorskip("nibabel")

from flip.constants.flip_constants import ResourceType  # noqa: E402
from flip.monai.datalist import DatalistError, build_datalist, pair_siblings, split_cohort, stem_of  # noqa: E402
from flip.monai.overlay import ImagingBinding, OverlayError, parse_overlay  # noqa: E402
from tests.unit.monai.test_overlay import VALID, _with  # noqa: E402


def _binding(resource: str, **patterns: str) -> ImagingBinding:
    return ImagingBinding.model_validate(
        {"resource": resource, "keys": {key: {"pattern": pat} for key, pat in patterns.items()}}
    )


SPLEEN = _binding("NIFTI", image="input_*.nii.gz", label="label_*.nii.gz")


def _nii(path: Path, shape=(8, 8, 4)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.zeros(shape, dtype=np.float32), np.eye(4)), str(path))
    return path


def _flip(folders: Mapping[str, object]) -> MagicMock:
    flip = MagicMock()

    def get(project_id, accession_id, resource_type):
        value = folders[accession_id]
        if isinstance(value, Exception):
            raise value
        return value

    flip.get_by_accession_number.side_effect = get
    return flip


def _cohort(*ids: str) -> pd.DataFrame:
    return pd.DataFrame({"accession_id": list(ids)})


def _spleen_accession(root: Path, acc: str, cases: list[str], with_labels=True, label_shape=None) -> Path:
    """The real converted layout: NIfTIs nested under XNAT's scans/<n>/resources/NIFTI/files/."""
    files = root / acc / "scans" / "1" / "resources" / "NIFTI" / "files"
    for case in cases:
        _nii(files / f"input_{case}.nii.gz")
        if with_labels:
            _nii(files / f"label_{case}.nii.gz", shape=label_shape or (8, 8, 4))
    return root / acc


def test_spleen_layout_pairs_like_the_tutorial(tmp_path: Path):
    folders = {
        "ACC1": _spleen_accession(tmp_path, "ACC1", ["spleen_10", "spleen_12"]),
        "ACC2": _spleen_accession(tmp_path, "ACC2", ["spleen_3"]),
    }
    items, report = build_datalist(_flip(folders), "p", _cohort("ACC1", "ACC2"), SPLEEN)

    # The tutorial's rule, applied to the same tree, gives the same pairs.
    expected = [
        {"image": str(img), "label": str(img).replace("/input_", "/label_")}
        for acc in ("ACC1", "ACC2")
        for img in sorted(folders[acc].rglob("input_*.nii.gz"))
    ]
    assert items == expected
    assert report.items == 3
    assert report.images_found == 3
    assert report.accessions == 2


def test_repeated_and_null_accessions_are_collapsed(tmp_path: Path):
    """A cohort query joining a one-to-many table repeats an accession; it must still fetch once.

    Duplicates would duplicate training samples and inflate the counts, and a NULL accession would
    reach imaging-api as the string "None"/"nan".
    """
    flip = _flip({"ACC1": _spleen_accession(tmp_path, "ACC1", ["spleen_10"])})
    cohort = pd.DataFrame({"accession_id": ["ACC1", "ACC1", None, "ACC1"]})

    items, report = build_datalist(flip, "proj", cohort, SPLEEN)

    assert report.accessions == 1
    assert len(items) == 1
    flip.get_by_accession_number.assert_called_once_with("proj", "ACC1", resource_type=ResourceType.NIFTI)


def test_requests_the_bound_resource(tmp_path: Path):
    flip = _flip({"ACC1": _spleen_accession(tmp_path, "ACC1", ["a"])})
    build_datalist(flip, "proj", _cohort("ACC1"), SPLEEN)
    flip.get_by_accession_number.assert_called_once_with("proj", "ACC1", resource_type=ResourceType.NIFTI)


def test_arbitrary_patterns_pair_by_stem(tmp_path: Path):
    """Not a one-literal-prefix swap: the stem sits between different prefixes AND suffixes."""
    binding = _binding("NIFTI", image="ct_*_0000.nii.gz", label="seg_*.nii.gz", mask="*_body.nii")
    acc = tmp_path / "ACC1"
    _nii(acc / "ct_case7_0000.nii.gz")
    _nii(acc / "seg_case7.nii.gz")
    _nii(acc / "case7_body.nii")
    items, _ = build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), binding)
    assert items == [
        {
            "image": str(acc / "ct_case7_0000.nii.gz"),
            "label": str(acc / "seg_case7.nii.gz"),
            "mask": str(acc / "case7_body.nii"),
        }
    ]


def test_image_only_binding(tmp_path: Path):
    """A classification-style bundle reads one key; no pairing, QC still runs."""
    binding = _binding("NIFTI", image="input_*.nii.gz")
    acc = _spleen_accession(tmp_path, "ACC1", ["a"], with_labels=False)
    items, _ = build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), binding)
    assert list(items[0]) == ["image"]


def test_sibling_must_be_in_the_same_directory(tmp_path: Path):
    acc = tmp_path / "ACC1"
    _nii(acc / "a" / "input_x.nii.gz")
    _nii(acc / "b" / "label_x.nii.gz")  # right name, wrong folder
    with pytest.raises(DatalistError, match="no label for 1"):
        build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), SPLEEN)


def test_drops_are_counted_and_good_items_kept(tmp_path: Path):
    acc = tmp_path / "ACC1"
    _nii(acc / "input_ok.nii.gz")
    _nii(acc / "label_ok.nii.gz")
    _nii(acc / "input_nolabel.nii.gz")
    _nii(acc / "input_2d.nii.gz", shape=(8, 8))
    _nii(acc / "label_2d.nii.gz", shape=(8, 8))
    _nii(acc / "input_mis.nii.gz")
    _nii(acc / "label_mis.nii.gz", shape=(8, 8, 5))
    (acc / "input_bad.nii.gz").write_bytes(b"not a nifti")
    (acc / "label_bad.nii.gz").write_bytes(b"not a nifti")
    folders = {"ACC1": acc, "ACC2": ConnectionError("imaging-api down")}

    items, report = build_datalist(_flip(folders), "p", _cohort("ACC1", "ACC2"), SPLEEN)

    assert [Path(i["image"]).name for i in items] == ["input_ok.nii.gz"]
    assert report.fetch_failed == 1
    assert report.missing_sibling == {"label": 1}
    assert report.wrong_ndim == 1
    assert report.shape_mismatch == 1
    assert report.unreadable == 1
    assert report.images_found == 5


def test_expected_ndim_none_skips_dimensionality(tmp_path: Path):
    acc = tmp_path / "ACC1"
    _nii(acc / "input_x.nii.gz", shape=(8, 8))
    _nii(acc / "label_x.nii.gz", shape=(8, 8))
    items, _ = build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), SPLEEN, expected_ndim=None)
    assert len(items) == 1


def test_missing_labels_fail_loudly_with_the_enrichment_hint(tmp_path: Path):
    acc = _spleen_accession(tmp_path, "ACC1", ["a", "b"], with_labels=False)
    with pytest.raises(DatalistError) as excinfo:
        build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), SPLEEN)
    message = str(excinfo.value)
    assert "0 item(s) from 2 image(s) across 1 accession(s)" in message
    assert "no label for 2" in message
    assert "data-enrichment" in message


def test_empty_cohort_fails_loudly(tmp_path: Path):
    with pytest.raises(DatalistError, match="0 item"):
        build_datalist(_flip({}), "p", _cohort(), SPLEEN)


def test_every_fetch_failing_fails_loudly_without_the_enrichment_hint():
    with pytest.raises(DatalistError) as excinfo:
        build_datalist(_flip({"ACC1": RuntimeError("x")}), "p", _cohort("ACC1"), SPLEEN)
    assert "1 accession(s) failed to download" in str(excinfo.value)
    assert "enrichment" not in str(excinfo.value)


def test_cohort_without_accession_id_refused():
    with pytest.raises(DatalistError, match="accession_id"):
        build_datalist(_flip({}), "p", pd.DataFrame({"person_id": [1]}), SPLEEN)


@pytest.mark.parametrize("resource", [ResourceType.SEGMENTATION, ResourceType.DICOM])
def test_unsupported_resources_refused_by_name(resource):
    binding = _binding(resource.value, image="*.dcm")
    flip = _flip({})
    with pytest.raises(DatalistError, match=f"resource={resource.value} is not supported"):
        build_datalist(flip, "p", _cohort("ACC1"), binding)
    flip.get_by_accession_number.assert_not_called()


@pytest.mark.parametrize(
    ("name", "pattern", "stem"),
    [
        ("input_spleen_10.nii.gz", "input_*.nii.gz", "spleen_10"),
        ("x.nii.gz", "*.nii.gz", "x"),
        ("input_.nii.gz", "input_*.nii.gz", ""),
        ("label_a.nii.gz", "input_*.nii.gz", None),
        ("input_a.nii", "input_*.nii.gz", None),
        ("ab", "ab*b", None),  # prefix and suffix may not overlap
    ],
)
def test_stem_of(name, pattern, stem):
    assert stem_of(name, pattern) == stem


def test_pair_siblings_rejects_a_foreign_file(tmp_path: Path):
    with pytest.raises(ValueError, match="does not match"):
        pair_siblings(tmp_path / "other.nii.gz", SPLEEN)


@pytest.mark.parametrize("pattern", ["input_*_*.nii.gz", "input.nii.gz", "input_?.nii.gz", "input_[ab]*.nii.gz"])
def test_overlay_requires_exactly_one_star(pattern):
    with pytest.raises(OverlayError, match="exactly one"):
        parse_overlay(_with("flip.imaging.keys", {"image": {"pattern": pattern}}))


def test_valid_overlay_binding_drives_the_datalist(tmp_path: Path):
    binding = parse_overlay(VALID).flip.imaging
    acc = _spleen_accession(tmp_path, "ACC1", ["a"])
    items, _ = build_datalist(_flip({"ACC1": acc}), "p", _cohort("ACC1"), binding)
    assert set(items[0]) == {"image", "label"}


def _fraction(value: float) -> ImagingBinding:
    """The spleen binding at another validation_fraction (model_copy skips validation, as a caller could)."""
    return SPLEEN.model_copy(update={"validation_fraction": value})


def test_split_keeps_each_patient_on_one_side():
    """Two studies of one patient must not land in both sets."""
    persons = [f"P{i}" for i in range(10) for _ in range(2)]
    cohort = pd.DataFrame({"person_id": persons, "accession_id": [f"A{n}" for n in range(20)]})

    train, val = split_cohort(cohort, _fraction(0.2))

    assert set(train["person_id"]).isdisjoint(val["person_id"])
    assert val["person_id"].nunique() == 2
    assert len(train) + len(val) == len(cohort)
    # Ranked by a hash of the id, not shuffled: the same cohort always splits the same way.
    assert val.equals(split_cohort(cohort.sample(frac=1, random_state=0), _fraction(0.2))[1].sort_index())


def test_split_reads_the_overlay_fraction():
    binding = parse_overlay(_with("flip.imaging.validation_fraction", 0.5)).flip.imaging
    _, val = split_cohort(_cohort("ACC1", "ACC2", "ACC3", "ACC4"), binding)
    assert len(val) == 2


@pytest.mark.parametrize(
    ("ids", "fraction", "n_val"),
    [
        (("ACC1", "ACC2", "ACC3", None), 0, 0),
        (("ACC1", "ACC2", "ACC3", None), 0.01, 1),
        (("ACC1", "ACC2", "ACC3", None), 0.99, 2),
        (("ACC1",), 0.5, 0),  # one patient: nothing can be held out without emptying training
    ],
)
def test_split_sizes_by_accession_without_person_id(ids, fraction, n_val):
    """At least one patient when non-zero and there are two or more, never all; a NULL id is never held out."""
    train, val = split_cohort(_cohort(*ids), _fraction(fraction))
    assert len(val) == n_val
    assert val["accession_id"].notna().all()
    assert len(train) == len(ids) - n_val


def test_split_falls_back_to_accession_for_null_person_id():
    """An all-NULL person_id column still splits, by accession, rather than silently holding nothing out."""
    cohort = pd.DataFrame({"person_id": [None] * 4, "accession_id": ["A1", "A2", "A3", "A4"]})
    _, val = split_cohort(cohort, _fraction(0.5))
    assert len(val) == 2


def test_split_places_a_partially_null_person_id_row_by_accession():
    """A row with no person_id is grouped by its accession, not sent to training every time."""
    cohort = pd.DataFrame({"person_id": [None, "P1", "P2", "P3"], "accession_id": ["A1", "A2", "A3", "A4"]})
    filled = cohort.assign(person_id=["A1", "P1", "P2", "P3"])
    for frac in (0.25, 0.5, 0.75):
        _, val = split_cohort(cohort, _fraction(frac))
        _, expected = split_cohort(filled, _fraction(frac))
        assert list(val["accession_id"]) == list(expected["accession_id"])
    assert "A1" in set(split_cohort(cohort, _fraction(0.75))[1]["accession_id"])  # was always in training


def test_split_warns_when_no_validation_set(caplog):
    with caplog.at_level("WARNING", logger="flip.monai.datalist"):
        _, val = split_cohort(_cohort("ACC1"), _fraction(0.1))
    assert val.empty
    assert "no validation set" in caplog.text


@pytest.mark.parametrize("fraction", [-0.5, 1, 1.5])
def test_split_refuses_a_fraction_outside_the_range(fraction):
    with pytest.raises(ValueError, match="validation_fraction"):
        split_cohort(_cohort("ACC1", "ACC2", "ACC3", "ACC4"), _fraction(fraction))
