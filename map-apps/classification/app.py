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

"""MONAI Deploy application wrapping the FLIP chest radiograph classifier.

This is the classification counterpart to the spleen segmentation application. The pipeline shape
differs from segmentation in two ways that matter to app authors:

* inference runs through a bespoke operator rather than ``MonaiBundleInferenceOperator``, because
  a classifier emits labels rather than an image;
* the output is a DICOM Structured Report rather than a DICOM Segmentation, since there is no
  pixel-level result to encode.
"""

import logging
from pathlib import Path

from classifier_operator import FlipXrayClassifierOperator
from monai.deploy.conditions import CountCondition
from monai.deploy.core import AppContext, Application
from monai.deploy.operators.dicom_data_loader_operator import DICOMDataLoaderOperator
from monai.deploy.operators.dicom_series_selector_operator import DICOMSeriesSelectorOperator
from monai.deploy.operators.dicom_series_to_volume_operator import DICOMSeriesToVolumeOperator
from monai.deploy.operators.dicom_text_sr_writer_operator import (
    DICOMTextSRWriterOperator,
    EquipmentInfo,
    ModelInfo,
)


class FlipXrayClassificationApp(Application):
    """Loads a radiograph study, classifies it, and writes the result as a DICOM SR instance."""

    def __init__(self, *args, **kwargs):
        self._logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        super().__init__(*args, **kwargs)

    def compose(self):
        app_context: AppContext = Application.init_app_context(self.argv)
        app_input_path = Path(app_context.input_path)
        app_output_path = Path(app_context.output_path)

        study_loader_op = DICOMDataLoaderOperator(
            self, CountCondition(self, 1), input_folder=app_input_path, name="study_loader_op"
        )
        series_selector_op = DICOMSeriesSelectorOperator(self, rules=Sample_Rules_Text, name="series_selector_op")
        series_to_vol_op = DICOMSeriesToVolumeOperator(self, name="series_to_vol_op")

        classifier_op = FlipXrayClassifierOperator(
            self, app_context=app_context, output_folder=app_output_path, name="classifier_op"
        )

        model_info = ModelInfo(
            creator="London AI Centre for Value Based Healthcare",
            name="FLIP chest radiograph classifier",
            version="0.0.1",
        )
        equipment_info = EquipmentInfo(manufacturer="FLIP", manufacturer_model="FLIP MAP DICOM SR Writer")
        custom_tags = {"SeriesDescription": "AI generated report, not for clinical use."}

        sr_writer_op = DICOMTextSRWriterOperator(
            self,
            copy_tags=True,
            model_info=model_info,
            equipment_info=equipment_info,
            custom_tags=custom_tags,
            output_folder=app_output_path,
            name="sr_writer_op",
        )

        self.add_flow(study_loader_op, series_selector_op, {("dicom_study_list", "dicom_study_list")})
        self.add_flow(
            series_selector_op, series_to_vol_op, {("study_selected_series_list", "study_selected_series_list")}
        )
        self.add_flow(series_to_vol_op, classifier_op, {("image", "image")})
        self.add_flow(classifier_op, sr_writer_op, {("result_text", "text")})
        # The SR writer copies Study and Patient attributes from the source series, so it needs the
        # selection as well as the text.
        self.add_flow(
            series_selector_op, sr_writer_op, {("study_selected_series_list", "study_selected_series_list")}
        )


# Select radiograph series. The FLIP tutorial data is Computed Radiography (CR); widen the
# modality pattern if your site sends Digital Radiography (DX) instead.
Sample_Rules_Text = """
{
    "selections": [
        {
            "name": "Radiograph series",
            "conditions": {
                "Modality": "(?i)CR|DX"
            }
        }
    ]
}
"""

if __name__ == "__main__":
    FlipXrayClassificationApp().run()
