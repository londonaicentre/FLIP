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

"""labels.json for flip.add_mask, and add_mask itself on both FLIP implementations (FLIP#1945)."""

import json
import re
from unittest.mock import patch

import pytest

from flip.constants import MASK_RESOURCE
from flip.core.masks import MAX_LABELS, labels_json
from flip.core.standard import FLIPStandardDev, FLIPStandardProd

LIVER = {
    "name": "liver",
    "category": ["123037004", "SCT", "Anatomical Structure"],
    "type": ["10200004", "SCT", "Liver"],
    "color": [40, 140, 40],
}


def test_a_name_alone_is_an_entry_and_keys_become_strings() -> None:
    assert labels_json({5: LIVER, 88: "iliopsoas_left"}) == {"5": LIVER, "88": {"name": "iliopsoas_left"}}


def test_tuples_are_written_as_lists() -> None:
    assert labels_json({1: {"name": "x", "color": (1, 2, 3)}})["1"]["color"] == [1, 2, 3]


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        ({}, "expect 1 to"),
        ({0: "background"}, "from 1 to 65535"),
        ({65536: "x"}, "from 1 to 65535"),
        ({"5": "liver"}, "from 1 to 65535"),
        ({True: "x"}, "from 1 to 65535"),
        ({5: {}}, "needs a name"),
        ({5: {"name": ""}}, "needs a name"),
        ({5: {"name": "liver", "notes": "x"}}, "unknown keys"),
        ({5: {"name": "liver", "type": ["10200004", "SCT"]}}, "code, scheme, meaning"),
        ({5: {"name": "liver", "type": ["10200004", "SCT", 3]}}, "code, scheme, meaning"),
        ({5: {"name": "liver", "color": [256, 0, 0]}}, "r, g, b"),
        ({i: "x" for i in range(1, MAX_LABELS + 2)}, "expect 1 to"),
    ],
    ids=[
        "empty",
        "background",
        "over-16-bit",
        "string-key",
        "bool-key",
        "no-name",
        "empty-name",
        "unknown-key",
        "short-code",
        "non-string-code",
        "color-out-of-range",
        "too-many",
    ],
)
def test_a_malformed_label_map_is_refused(labels, message) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        labels_json(labels)


def test_prod_writes_labels_json_beside_the_mask_and_uploads_it_last(tmp_path) -> None:
    (tmp_path / "net-1" / "upload" / "ACC1").mkdir(parents=True)
    flip = FLIPStandardProd()
    with (
        patch("flip.core.base.FlipConstants") as constants,
        patch.object(FLIPStandardProd, "add_resource") as add_resource,
    ):
        constants.IMAGES_DIR, constants.NET_ID = str(tmp_path), "net-1"
        flip.add_mask("proj", "ACC1", "1", "ACC1/pred_1.nii.gz", {5: LIVER})

    add_resource.assert_called_once_with("proj", "ACC1", "1", MASK_RESOURCE, ["ACC1/pred_1.nii.gz", "ACC1/labels.json"])
    assert json.loads((tmp_path / "net-1" / "upload" / "ACC1" / "labels.json").read_text()) == {"5": LIVER}


def test_a_mask_that_is_not_nifti_is_refused_before_anything_is_written(tmp_path) -> None:
    flip = FLIPStandardProd()
    with patch.object(FLIPStandardProd, "add_resource") as add_resource, pytest.raises(ValueError, match="NIfTI"):
        flip.add_mask("proj", "ACC1", "1", "ACC1/pred_1.png", {5: LIVER})
    add_resource.assert_not_called()


def test_dev_checks_the_labels_but_uploads_nothing(tmp_path) -> None:
    flip = FLIPStandardDev()
    with patch.object(FLIPStandardDev, "add_resource") as add_resource:
        flip.add_mask("proj", "ACC1", "1", "ACC1/pred_1.nii.gz", {5: LIVER})
        with pytest.raises(ValueError, match="background"):
            flip.add_mask("proj", "ACC1", "1", "ACC1/pred_1.nii.gz", {0: "background"})
    add_resource.assert_not_called()
