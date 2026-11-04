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
"""The converter on a real DICOM-SEG (written by highdicom over synthetic CT slices); XNAT is mocked."""

import json
import sys
from pathlib import Path
from unittest import mock

import highdicom as hd
import numpy as np
import pydicom
import pytest
import requests
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sr.codedict import codes
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, SegmentationStorage, generate_uid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import export_mask  # noqa: E402

ROWS, COLS, SLICES = 6, 5, 4
SPACING = (0.8, 0.8, 2.5)  # x, y, z in mm; the slices stack along +z from the origin


AXIAL = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
# (row direction, column direction) as DICOM ImageOrientationPatient; the slices stack along their cross product.
ORIENTATIONS = {
    "axial": AXIAL,
    "sagittal": ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
    "oblique-flipped": ((0.0, -0.6, 0.8), (-1.0, 0.0, 0.0)),
}


def _source_slices(orientation=AXIAL) -> list[Dataset]:
    row, col = (np.array(v) for v in orientation)
    normal = np.cross(row, col)
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    slices = []
    for z in range(SLICES):
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, generate_uid()
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.FrameOfReferenceUID = study, series, frame
        ds.PatientID, ds.PatientName, ds.PatientBirthDate, ds.PatientSex = "SYNTH", "Synthetic^Patient", "", "O"
        ds.StudyDate, ds.StudyTime, ds.StudyID, ds.AccessionNumber, ds.ReferringPhysicianName = "", "", "1", "", ""
        ds.Modality, ds.SeriesNumber, ds.InstanceNumber = "CT", 1, z + 1
        ds.ImagePositionPatient = (z * SPACING[2] * normal).tolist()
        ds.ImageOrientationPatient = [*row, *col]
        ds.PixelSpacing = [SPACING[1], SPACING[0]]
        ds.SliceThickness = SPACING[2]
        ds.Rows, ds.Columns = ROWS, COLS
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.PixelData = np.zeros((ROWS, COLS), dtype=np.int16).tobytes()
        slices.append(ds)
    return slices


def _labels() -> np.ndarray:
    """(z, y, x) label volume: two segments on different slices, nothing on the first or last."""
    labels = np.zeros((SLICES, ROWS, COLS), dtype=np.uint8)
    labels[1, 1:3, 1:4] = 1
    labels[2, 3:5, 2:4] = 2
    return labels


def _segment(number: int, label: str, organ) -> hd.seg.SegmentDescription:
    return hd.seg.SegmentDescription(
        segment_number=number,
        segment_label=label,
        segmented_property_category=codes.SCT.Organ,
        segmented_property_type=organ,
        algorithm_type=hd.seg.SegmentAlgorithmTypeValues.MANUAL,
    )


def _write_seg(directory: Path, orientation=AXIAL) -> Path:
    seg = hd.seg.Segmentation(
        source_images=_source_slices(orientation),
        pixel_array=_labels(),
        segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
        segment_descriptions=[_segment(1, "Spleen", codes.SCT.Spleen), _segment(2, "Liver", codes.SCT.Liver)],
        series_instance_uid=generate_uid(),
        series_number=100,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="FLIP",
        manufacturer_model_name="export-mask tests",
        software_versions="1",
        device_serial_number="1",
    )
    path = directory / "SEG_1.dcm"
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.save_as(path)
    return path


@pytest.fixture
def dicom_seg(tmp_path: Path) -> Path:
    return _write_seg(tmp_path / "in")


def _source_grid(orientation=AXIAL) -> sitk.Image:
    """The CT the mask was drawn on, as SimpleITK sees it: (x, y, z) along (row, column, normal)."""
    row, col = (np.array(v) for v in orientation)
    image = sitk.Image([COLS, ROWS, SLICES], sitk.sitkInt16)
    image.SetSpacing(SPACING)
    image.SetDirection(np.column_stack([row, col, np.cross(row, col)]).flatten().tolist())
    return image


def _labels_at_world_positions(image: sitk.Image) -> np.ndarray:
    """What the source label volume holds at each voxel of ``image``, by physical position."""
    expected = np.zeros(sitk.GetArrayFromImage(image).shape, dtype=np.uint8)
    for z, y, x in np.ndindex(expected.shape):
        px, py, pz = image.TransformIndexToPhysicalPoint((x, y, z))
        i, j, k = round(pz / SPACING[2]), round(py / SPACING[1]), round(px / SPACING[0])
        if 0 <= i < SLICES and 0 <= j < ROWS and 0 <= k < COLS:
            expected[z, y, x] = _labels()[i, j, k]
    return expected


def test_the_fixture_is_a_real_dicom_seg(dicom_seg: Path) -> None:
    """Guards the fixture: without a genuine SEG every test below would pass vacuously."""
    assert pydicom.dcmread(dicom_seg).SOPClassUID == SegmentationStorage


def test_dicom_seg_converts_to_the_same_labels_in_the_same_place(dicom_seg: Path, tmp_path: Path) -> None:
    out = tmp_path / "mask.nii.gz"
    export_mask.dicom_seg_to_nifti(dicom_seg, out)

    image = sitk.ReadImage(str(out))
    mask = sitk.GetArrayFromImage(image)
    assert image.GetSpacing() == pytest.approx(SPACING)
    assert set(np.unique(mask)) == {0, 1, 2}
    assert np.array_equal(mask, _labels_at_world_positions(image))


def test_resampling_lands_the_mask_on_the_reference_grid(dicom_seg: Path, tmp_path: Path) -> None:
    out = tmp_path / "mask.nii.gz"
    export_mask.dicom_seg_to_nifti(dicom_seg, out)
    # A reference twice as fine in-plane and offset by a quarter mask voxel: every reference
    # voxel falls strictly between mask voxel centres, so nearest-neighbour has one right answer
    # and any blending interpolator invents labels (a 0|2 edge would read as 1).
    reference = sitk.Image([2 * COLS, 2 * ROWS, SLICES], sitk.sitkInt16)
    reference.SetSpacing((SPACING[0] / 2, SPACING[1] / 2, SPACING[2]))
    reference.SetOrigin((SPACING[0] / 4, SPACING[1] / 4, 0.0))
    reference_path = tmp_path / "scan.nii.gz"
    sitk.WriteImage(reference, str(reference_path))

    export_mask.resample_to_reference(out, reference_path)

    resampled = sitk.ReadImage(str(out))
    mask = sitk.GetArrayFromImage(resampled)
    assert mask.shape == (SLICES, 2 * ROWS, 2 * COLS)
    assert np.array_equal(mask, _labels_at_world_positions(resampled))


def test_main_converts_and_uploads_each_seg(dicom_seg: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    xnat = mock.MagicMock()
    with (
        mock.patch.object(export_mask, "connect", return_value=xnat),
        mock.patch.object(export_mask, "find_reference_nifti", return_value=None),
        mock.patch.object(export_mask, "upload") as upload,
    ):
        status = export_mask.main(
            ["-i", str(dicom_seg.parent), "-o", str(output), "--experiment", "E", "--assessor", "A"]
        )

    assert status == 0
    upload.assert_called_once_with(xnat, "E", "A", output / "SEG_1.nii.gz")
    assert (output / "SEG_1.nii.gz").is_file()


def test_main_refuses_segs_whose_masks_would_share_a_name(tmp_path: Path) -> None:
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "SEG_1.dcm").write_bytes(b"")
    with mock.patch.object(export_mask, "connect") as connect:
        status = export_mask.main(["-i", str(tmp_path), "-o", str(tmp_path), "--experiment", "E", "--assessor", "A"])
    assert status == 1
    connect.assert_not_called()


def test_main_fails_when_the_assessor_has_no_dicom_seg(tmp_path: Path) -> None:
    with mock.patch.object(export_mask, "connect") as connect:
        status = export_mask.main(["-i", str(tmp_path), "-o", str(tmp_path), "--experiment", "E", "--assessor", "A"])
    assert status == 1
    connect.assert_not_called()


class FakeXnat:
    """The Xnat interface over a dict of listings; records downloads and uploads."""

    def __init__(self, listings: dict[str, list[str]]) -> None:
        self.listings = listings
        self.downloaded: list[tuple[str, Path]] = []
        self.uploaded: list[tuple[str, Path]] = []

    def list(self, path: str, field: str) -> list[str]:
        return self.listings.get(path, [])

    def download(self, path: str, target: Path) -> None:
        self.downloaded.append((path, target))

    def upload(self, path: str, source: Path) -> None:
        self.uploaded.append((path, source))


SESSION = "/data/experiments/E"
SEG_FILES = f"{SESSION}/assessors/A/resources/SEG/files"
NIFTI_FILES = f"{SESSION}/scans/1/resources/NIFTI/files"


def test_upload_puts_the_mask_into_the_assessors_seg_resource(tmp_path: Path) -> None:
    xnat = FakeXnat({SEG_FILES: ["SEG_1.dcm"]})
    export_mask.upload(xnat, "E", "A", tmp_path / "SEG_1.nii.gz")
    assert xnat.uploaded == [(f"{SEG_FILES}/SEG_1.nii.gz", tmp_path / "SEG_1.nii.gz")]


def test_upload_never_overwrites_an_existing_mask(tmp_path: Path) -> None:
    xnat = FakeXnat({SEG_FILES: ["SEG_1.dcm", "SEG_1.nii.gz"]})
    export_mask.upload(xnat, "E", "A", tmp_path / "SEG_1.nii.gz")
    assert xnat.uploaded == []


def test_no_reference_when_the_session_has_several_scans(tmp_path: Path) -> None:
    xnat = FakeXnat({f"{SESSION}/scans": ["1", "2"]})
    assert export_mask.find_reference_nifti(xnat, "E", tmp_path) is None
    assert xnat.downloaded == []


def test_reference_is_the_converted_scan_not_the_labels_beside_it(tmp_path: Path) -> None:
    """FLIP's NIFTI resource holds dcm2niix's ``input_*`` image plus enrichment's ``label_*`` masks."""
    image = "input_CT_Spleen_20161103205508_1.nii.gz"
    xnat = FakeXnat(
        {
            f"{SESSION}/scans": ["1"],
            NIFTI_FILES: ["label_CT_Spleen_20161103205508_1.nii.gz", image, "input_CT_Spleen_20161103205508_1.json"],
        }
    )
    assert export_mask.find_reference_nifti(xnat, "E", tmp_path) == tmp_path / image
    assert xnat.downloaded == [(f"{NIFTI_FILES}/{image}", tmp_path / image)]


def _response(status: int, payload: object = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode() if payload is not None else b""
    return response


def test_the_client_is_built_from_what_the_container_service_injects(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real client, not a mock: constructing it must not import anything the image lacks."""
    monkeypatch.setenv("XNAT_HOST", "http://xnat-web:8080/")
    monkeypatch.setenv("XNAT_USER", "alias-token")
    monkeypatch.setenv("XNAT_PASS", "alias-secret")  # pragma: allowlist secret
    xnat = export_mask.connect()
    assert xnat.host == "http://xnat-web:8080"
    assert xnat.session.auth == ("alias-token", "alias-secret")  # pragma: allowlist secret


def test_a_listing_reads_the_named_field_of_each_row() -> None:
    xnat = export_mask.Xnat("http://xnat", "u", "p")
    payload = {"ResultSet": {"Result": [{"Name": "a.dcm"}, {"Name": "b.nii.gz"}]}}
    with mock.patch.object(xnat.session, "request", return_value=_response(200, payload)) as request:
        assert xnat.list("/data/x/files", "Name") == ["a.dcm", "b.nii.gz"]
    assert request.call_args.args == ("GET", "http://xnat/data/x/files")
    assert request.call_args.kwargs["params"] == {"format": "json"}


def test_a_missing_resource_lists_as_empty_but_other_errors_raise() -> None:
    xnat = export_mask.Xnat("http://xnat", "u", "p")
    with mock.patch.object(xnat.session, "request", return_value=_response(404)):
        assert xnat.list("/data/x/files", "Name") == []
    with (
        mock.patch.object(xnat.session, "request", return_value=_response(500)),
        pytest.raises(requests.HTTPError),
    ):
        xnat.list("/data/x/files", "Name")


def test_an_upload_puts_the_file_body(tmp_path: Path) -> None:
    source = tmp_path / "SEG_1.nii.gz"
    source.write_bytes(b"nifti")
    xnat = export_mask.Xnat("http://xnat", "u", "p")
    with mock.patch.object(xnat.session, "request", return_value=_response(200)) as request:
        xnat.upload("/data/x/files/SEG_1.nii.gz", source)
    assert request.call_args.args == ("PUT", "http://xnat/data/x/files/SEG_1.nii.gz")
    assert request.call_args.kwargs["params"] == {"inbody": "true"}


@pytest.mark.parametrize("orientation", ORIENTATIONS.values(), ids=ORIENTATIONS.keys())
def test_identity_labels_to_seg_to_nifti_back_on_the_source_grid(orientation, tmp_path: Path) -> None:
    """Labels -> DICOM-SEG -> NIfTI -> the CT grid they were drawn on must give the labels back exactly.

    Off-axial orientations are where an axis-order or direction mistake hides: the axial case
    alone passes with a transposed or sign-flipped direction matrix.
    """
    out = tmp_path / "mask.nii.gz"
    export_mask.dicom_seg_to_nifti(_write_seg(tmp_path / "in", orientation), out)
    reference_path = tmp_path / "scan.nii.gz"
    sitk.WriteImage(_source_grid(orientation), str(reference_path))

    export_mask.resample_to_reference(out, reference_path)

    assert np.array_equal(sitk.GetArrayFromImage(sitk.ReadImage(str(out))), _labels())


def test_identity_resampling_onto_its_own_grid_changes_nothing(dicom_seg: Path, tmp_path: Path) -> None:
    out = tmp_path / "mask.nii.gz"
    export_mask.dicom_seg_to_nifti(dicom_seg, out)
    before = sitk.ReadImage(str(out))
    own_grid = tmp_path / "own_grid.nii.gz"
    sitk.WriteImage(before, str(own_grid))

    export_mask.resample_to_reference(out, own_grid)

    after = sitk.ReadImage(str(out))
    assert np.array_equal(sitk.GetArrayFromImage(after), sitk.GetArrayFromImage(before))
    assert after.GetOrigin() == pytest.approx(before.GetOrigin())
    assert after.GetSpacing() == pytest.approx(before.GetSpacing())
    assert after.GetDirection() == pytest.approx(before.GetDirection())
