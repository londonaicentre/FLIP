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
"""The converter on synthetic CT slices and a NIfTI mask written in another orientation; XNAT is mocked."""

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pydicom
import pytest
import requests
import SimpleITK as sitk
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import nifti_to_seg  # noqa: E402

ROWS, COLS, SLICES = 6, 5, 4
SPACING = (0.8, 0.8, 2.5)  # x, y, z in mm
ORIENTATIONS = {
    "axial": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "sagittal": ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
    "oblique-flipped": ((0.0, -0.6, 0.8), (-1.0, 0.0, 0.0)),
}
LABELS = {
    "5": {
        "name": "liver",
        "category": ["123037004", "SCT", "Anatomical Structure"],
        "type": ["10200004", "SCT", "Liver"],
        "color": [40, 140, 40],
    },
    "88": {
        "name": "iliopsoas_left",
        "type": ["68455001", "SCT", "Iliopsoas muscle"],
        "modifier": ["7771000", "SCT", "Left"],
    },
}


def _write_series(directory: Path, orientation) -> list[Dataset]:
    """Synthetic CT slices, written under shuffled file names so the converter must sort them itself."""
    row, col = (np.array(v) for v in orientation)
    normal = np.cross(row, col)
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    slices = []
    for z in range(SLICES):
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
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
        ds.save_as(directory / f"{(7 * z + 3) % SLICES:03d}-{generate_uid()[-6:]}.dcm", enforce_file_format=True)
        slices.append(ds)
    return slices


def _expected(slice_index: int) -> np.ndarray:
    """The label values the mask holds on one source slice: liver (5) in a block, iliopsoas (88) in a corner, and
    an unlisted label (99) the converter must leave out."""
    plane = np.zeros((ROWS, COLS), dtype=np.uint16)
    if slice_index in (1, 2):
        plane[1:4, 1:3] = 5
    if slice_index == 3:
        plane[0, 4] = 88
    plane[5, 0] = 99
    return plane


def _write_mask(dicom_dir: Path, mask_path: Path) -> None:
    """The mask as dcm2niix would leave it: the same voxels, but stored in another axis order and direction."""
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(reader.GetGDCMSeriesFileNames(str(dicom_dir)))
    reference = reader.Execute()
    mask = sitk.GetImageFromArray(np.stack([_expected(z) for z in range(SLICES)]))
    mask.CopyInformation(reference)
    sitk.WriteImage(sitk.DICOMOrient(mask, "RAS"), str(mask_path))


@pytest.fixture(params=list(ORIENTATIONS), ids=list(ORIENTATIONS))
def scan(request, tmp_path: Path) -> tuple[Path, Path, list[Dataset]]:
    dicom_dir = tmp_path / "dicom"
    dicom_dir.mkdir()
    slices = _write_series(dicom_dir, ORIENTATIONS[request.param])
    mask = tmp_path / "pred_000000_1.nii.gz"
    _write_mask(dicom_dir, mask)
    return dicom_dir, mask, slices


def _planes_by_source(seg: Dataset) -> dict[str, dict[int, np.ndarray]]:
    """{source SOP instance UID: {segment number: frame}} read back from the SEG's per-frame references."""
    planes: dict[str, dict[int, np.ndarray]] = {}
    pixels = seg.pixel_array.reshape(int(seg.NumberOfFrames), ROWS, COLS)
    for frame, item in zip(pixels, seg.PerFrameFunctionalGroupsSequence, strict=True):
        uid = item.DerivationImageSequence[0].SourceImageSequence[0].ReferencedSOPInstanceUID
        number = int(item.SegmentIdentificationSequence[0].ReferencedSegmentNumber)
        planes.setdefault(uid, {})[number] = frame
    return planes


def test_each_label_lands_on_its_source_slices(scan) -> None:
    dicom_dir, mask, slices = scan
    seg = nifti_to_seg.nifti_to_seg(mask, dicom_dir, LABELS, "NIFTI_SEG test")

    assert [s.SegmentLabel for s in seg.SegmentSequence] == ["liver", "iliopsoas_left"]
    planes = _planes_by_source(seg)
    for z, source in enumerate(slices):
        expected = _expected(z)
        for number, value in ((1, 5), (2, 88)):
            frame = planes.get(source.SOPInstanceUID, {}).get(number)
            if (expected == value).any():
                assert frame is not None
                assert np.array_equal(frame.astype(bool), expected == value)
            else:
                assert frame is None  # empty frames are left out


def test_frames_sit_at_their_source_slices_position(scan) -> None:
    dicom_dir, mask, slices = scan
    seg = nifti_to_seg.nifti_to_seg(mask, dicom_dir, LABELS, "NIFTI_SEG test")
    positions = {s.SOPInstanceUID: [float(v) for v in s.ImagePositionPatient] for s in slices}
    for item in seg.PerFrameFunctionalGroupsSequence:
        uid = item.DerivationImageSequence[0].SourceImageSequence[0].ReferencedSOPInstanceUID
        position = [float(v) for v in item.PlanePositionSequence[0].ImagePositionPatient]
        assert np.allclose(position, positions[uid], atol=1e-3)


def test_the_viewer_breaking_top_level_reference_list_is_gone(scan) -> None:
    dicom_dir, mask, _ = scan
    seg = nifti_to_seg.nifti_to_seg(mask, dicom_dir, LABELS, "NIFTI_SEG test")
    assert "SourceImageSequence" not in seg


def test_codes_modifier_and_colour_come_from_labels_json(scan) -> None:
    dicom_dir, mask, _ = scan
    liver, iliopsoas = nifti_to_seg.nifti_to_seg(mask, dicom_dir, LABELS, "NIFTI_SEG test").SegmentSequence
    assert liver.SegmentedPropertyTypeCodeSequence[0].CodeValue == "10200004"
    assert liver.RecommendedDisplayCIELabValue == nifti_to_seg.rgb_to_dicom_lab([40, 140, 40])
    modifier = iliopsoas.SegmentedPropertyTypeCodeSequence[0].SegmentedPropertyTypeModifierCodeSequence[0]
    assert modifier.CodeMeaning == "Left"
    assert iliopsoas.SegmentedPropertyCategoryCodeSequence[0].CodeValue == "123037004"  # the default category


def test_no_seg_when_no_labelled_voxel_is_listed(scan) -> None:
    dicom_dir, mask, _ = scan
    assert nifti_to_seg.nifti_to_seg(mask, dicom_dir, {"7": {"name": "spleen"}}, "NIFTI_SEG test") is None


def test_white_is_full_lightness_and_neutral() -> None:
    lightness, a, b = nifti_to_seg.rgb_to_dicom_lab([255, 255, 255])
    assert lightness == 65535
    assert abs(a - 32896) < 40
    assert abs(b - 32896) < 40


class FakeXnat:
    def __init__(self, dicom_dir: Path) -> None:
        self.dicom_dir = dicom_dir
        self.stored: list[tuple[str, str, str, bytes]] = []

    def project_of(self, experiment: str) -> str:
        return "PROJ"

    def download_dicom(self, experiment: str, scan: str, target: Path) -> None:
        for f in self.dicom_dir.iterdir():
            shutil.copy(f, target / f.name)

    def store_roi_collection(self, project: str, experiment: str, label: str, seg: bytes) -> None:
        self.stored.append((project, experiment, label, seg))


def _main(resource: Path, uri: str, monkeypatch: pytest.MonkeyPatch, fake: FakeXnat) -> int:
    monkeypatch.setattr(nifti_to_seg, "Xnat", lambda *args: fake)
    for key in ("XNAT_HOST", "XNAT_USER", "XNAT_PASS"):
        monkeypatch.setenv(key, "x")
    return nifti_to_seg.main(["-i", str(resource), "--resource-uri", uri, "--resource-label", "NIFTI_SEG"])


URI = "/archive/experiments/XNAT_E00001/scans/1/resources/NIFTI_SEG"


def test_main_stores_one_collection_named_after_the_session(scan, tmp_path, monkeypatch) -> None:
    dicom_dir, mask, _ = scan
    resource = tmp_path / "resource"
    resource.mkdir()
    shutil.copy(mask, resource / mask.name)
    (resource / "labels.json").write_text(json.dumps(LABELS))
    fake = FakeXnat(dicom_dir)

    assert _main(resource, URI, monkeypatch, fake) == 0
    ((project, experiment, label, seg),) = fake.stored
    assert (project, experiment, label) == ("PROJ", "XNAT_E00001", "XNAT_E00001_NIFTI_SEG_1")
    assert pydicom.dcmread(pydicom.filebase.DicomBytesIO(seg)).Modality == "SEG"


def test_main_waits_for_labels_json(scan, tmp_path, monkeypatch) -> None:
    _, mask, _ = scan
    resource = tmp_path / "resource"
    resource.mkdir()
    shutil.copy(mask, resource / mask.name)
    fake = FakeXnat(tmp_path)
    assert _main(resource, URI, monkeypatch, fake) == 0
    assert fake.stored == []


def test_main_waits_for_a_nifti(tmp_path, monkeypatch) -> None:
    (tmp_path / "labels.json").write_text(json.dumps(LABELS))
    fake = FakeXnat(tmp_path)
    assert _main(tmp_path, URI, monkeypatch, fake) == 0
    assert fake.stored == []


def test_main_leaves_resources_off_scans_alone(tmp_path, monkeypatch) -> None:
    fake = FakeXnat(tmp_path)
    assert _main(tmp_path, "/archive/experiments/XNAT_E00001/resources/NIFTI_SEG", monkeypatch, fake) == 0
    assert fake.stored == []


@pytest.mark.parametrize(
    "labels",
    [[], {}, {"0": {"name": "x"}}, {"x": {"name": "x"}}, {"5": {}}, {"5": "liver"}],
    ids=["list", "empty", "background", "non-numeric", "no-name", "bare-name"],
)
def test_main_fails_on_a_malformed_labels_json(scan, tmp_path, monkeypatch, labels) -> None:
    _, mask, _ = scan
    resource = tmp_path / "resource"
    resource.mkdir()
    shutil.copy(mask, resource / mask.name)
    (resource / "labels.json").write_text(json.dumps(labels))
    fake = FakeXnat(tmp_path)
    assert _main(resource, URI, monkeypatch, fake) == 1
    assert fake.stored == []


def test_main_refuses_a_mask_over_the_voxel_cap(scan, tmp_path, monkeypatch) -> None:
    _, mask, _ = scan
    resource = tmp_path / "resource"
    resource.mkdir()
    shutil.copy(mask, resource / mask.name)
    (resource / "labels.json").write_text(json.dumps(LABELS))
    monkeypatch.setattr(nifti_to_seg, "MAX_VOXELS", ROWS * COLS * SLICES - 1)
    fake = FakeXnat(tmp_path)
    assert _main(resource, URI, monkeypatch, fake) == 1
    assert fake.stored == []


def test_a_2d_image_is_not_a_label_map(tmp_path) -> None:
    flat = tmp_path / "flat.nii.gz"
    sitk.WriteImage(sitk.Image([COLS, ROWS], sitk.sitkUInt8), str(flat))
    with pytest.raises(nifti_to_seg.InputError):
        nifti_to_seg.check_mask_size(flat)


class _Answer:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code, self.text, self.ok = status, text, status < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(response=self)


@pytest.mark.parametrize(("exists", "raises"), [("true", False), ("false", True)])
def test_a_lost_race_is_fine_only_when_the_collection_exists(monkeypatch, exists, raises) -> None:
    xnat = nifti_to_seg.Xnat("http://xnat", "u", "p")
    monkeypatch.setattr(xnat.session, "request", lambda *args, **kwargs: _Answer(500))
    monkeypatch.setattr(xnat.session, "get", lambda *args, **kwargs: _Answer(200, exists))
    if raises:
        with pytest.raises(requests.HTTPError):
            xnat.store_roi_collection("P", "E", "E_NIFTI_SEG_1", b"seg")
    else:
        xnat.store_roi_collection("P", "E", "E_NIFTI_SEG_1", b"seg")
