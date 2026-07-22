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

"""Operator that writes the deepcOS engine report alongside the DICOM SEG.

This is the thin MONAI Deploy adapter over ``deepc_report``: it receives the predicted mask and the
source volume, delegates the volume computation and report assembly to the tested pure functions,
and writes the result into the output folder so deepcOS collects it next to the SEG. Keeping the
logic in ``deepc_report`` is deliberate — the operator cannot be unit-tested without the SDK, so it
holds no logic worth testing.
"""

import logging
from pathlib import Path

import deepc_report
import numpy as np
from monai.deploy.core import Fragment, Image, Operator, OperatorSpec

# MONAI Deploy image metadata keys for the DICOM identifiers, with the DICOM-attribute spellings as
# fallbacks.
_STUDY_UID_KEYS = ("study_instance_uid", "StudyInstanceUID")
_SERIES_UID_KEYS = ("series_instance_uid", "SeriesInstanceUID")


class DeepcSegReportOperator(Operator):
    """Computes the segmented volume and writes the deepcOS engine report.

    Named inputs:
        seg_image: The predicted segmentation mask.
        source_image: The DICOM-derived volume the mask was produced from, read for voxel spacing
            and the study/series identifiers.
    """

    def __init__(self, fragment: Fragment, *args, output_folder: Path, **kwargs):
        self.input_name_seg = "seg_image"
        self.input_name_source = "source_image"
        self.output_folder = output_folder
        self._logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        super().__init__(fragment, *args, **kwargs)

    def setup(self, spec: OperatorSpec):
        spec.input(self.input_name_seg)
        spec.input(self.input_name_source)

    def compute(self, op_input, op_output, context):
        seg_image = op_input.receive(self.input_name_seg)
        source_image = op_input.receive(self.input_name_source)
        if not isinstance(seg_image, Image):
            raise ValueError(f"Expected an Image on '{self.input_name_seg}', got {type(seg_image)!r}")

        metadata = source_image.metadata() if isinstance(source_image, Image) else {}
        study_uid = self._first(metadata, _STUDY_UID_KEYS)
        series_uid = self._first(metadata, _SERIES_UID_KEYS)

        # Drop any singleton channel axis the bundle's post-processing may leave on the label map,
        # so its dimensionality matches the three voxel spacings.
        mask = np.squeeze(seg_image.asnumpy())

        try:
            spacing = deepc_report.voxel_spacing(metadata)
            volume_ml = deepc_report.segmented_volume_ml(mask, spacing)
        except ValueError as error:
            # A missing spacing or an unexpected mask shape means no trustworthy volume. Surface it
            # as a degraded report rather than emitting a fabricated number — the same
            # silent-empty-output failure mode the report is meant to catch.
            self._logger.error(f"Cannot compute segmented volume: {error}")
            report = deepc_report.build_engine_report(
                quantities=[],
                messages=[deepc_report.coded_message("SEG_VOLUME_UNAVAILABLE", str(error))],
                status="DEGRADED",
                study_uid=study_uid,
                series_uid=series_uid,
            )
            written = deepc_report.write_engine_report(self.output_folder, report)
            self._logger.info(f"Wrote degraded engine report to {written}")
            return

        self._logger.info(f"Segmented volume: {volume_ml:.2f} mL")
        report = deepc_report.build_engine_report(
            quantities=[deepc_report.volume_quantity(volume_ml)],
            study_uid=study_uid,
            series_uid=series_uid,
        )
        written = deepc_report.write_engine_report(self.output_folder, report)
        self._logger.info(f"Wrote engine report to {written}")

    @staticmethod
    def _first(metadata: dict, keys: tuple[str, ...]):
        """Return the first present metadata value among ``keys``, else ``None``."""
        return next((metadata[key] for key in keys if key in metadata), None)
