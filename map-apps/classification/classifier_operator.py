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

"""Inference operator for the FLIP chest radiograph classification model.

A classification MAP cannot use ``MonaiBundleInferenceOperator``: that operator maps an image to
an image, whereas a classifier produces labels. This operator therefore runs the scripted network
itself and emits a text result, which the DICOM Structured Report writer turns into a DICOM object.

The preprocessing below mirrors the training application's ``transforms.py::get_xray_transforms``
(validation branch): resize to 224x224, then scale intensity to [0, 1] on a single channel. Keeping
these in step with training is the app author's responsibility; a mismatch here degrades the result
silently.

The orientation step needs care, because the two paths read DICOM through different loaders. MONAI's
``LoadImaged`` returns the pixel array transposed — indexed ``(column, row)`` rather than the
``(row, column)`` of ``PixelData`` — and the training chain's ``Rotate90d(k=-1)`` composes with that
transpose into a net left-right mirror. So the network was trained on mirrored radiographs. This
operator instead receives its array from ``DICOMSeriesToVolumeOperator``, which preserves DICOM's
``(row, column)`` order, and must therefore mirror explicitly to present the network the orientation
it was trained on. ``Flip(spatial_axis=1)`` below does that; transcribing ``Rotate90`` instead would
be wrong, as it corrects for a transpose this path never applies.

This was verified rather than reasoned about: the same CR study pushed through the training chain and
through this operator agrees bit-for-bit (``max|diff| == 0``) with the flip, on both a square and a
non-square radiograph, and disagrees under every other rotation/flip combination.
"""

import json
import logging
import os
from pathlib import Path

import torch
from monai.deploy.core import AppContext, ConditionType, Fragment, Image, Operator, OperatorSpec
from monai.transforms import Activations, Compose, EnsureType, Flip, Resize, ScaleIntensity

# Label semantics come from the training app's config.json LESIONS block. The trainer optimises a
# binary cross-entropy loss, so the two outputs are independent binary labels read through a
# sigmoid, not mutually exclusive classes under a softmax.
LABELS = ("Effusion", "Edema")
POSITIVE_THRESHOLD = 0.5


class FlipXrayClassifierOperator(Operator):
    """Runs the FLIP xray classifier over a DICOM-derived image and emits a text result.

    Named input:
        image: Image object converted from the selected DICOM series.

    Named output:
        result_text: Human-readable classification result for the DICOM SR writer.
    """

    DEFAULT_OUTPUT_FOLDER = Path.cwd() / "classification_results"
    MODEL_LOCAL_PATH = Path(os.environ.get("HOLOSCAN_MODEL_PATH", Path.cwd() / "model" / "model.ts"))

    def __init__(
        self,
        fragment: Fragment,
        *args,
        app_context: AppContext,
        model_name: str | None = "",
        model_path: Path = MODEL_LOCAL_PATH,
        output_folder: Path = DEFAULT_OUTPUT_FOLDER,
        **kwargs,
    ):
        self.input_name_image = "image"
        self.output_name_result = "result_text"
        self.output_folder = output_folder
        self.model_path = model_path
        self.app_context = app_context
        self._model_name = model_name.strip() if isinstance(model_name, str) else ""
        self._logger = logging.getLogger(f"{__name__}.{type(self).__name__}")
        self.model = self._get_model(app_context, model_path, self._model_name)
        super().__init__(fragment, *args, **kwargs)

    def _get_model(self, app_context: AppContext, model_path: Path, model_name: str):
        """Take the network from the application context, else load the TorchScript file directly."""
        if app_context.models:
            self._logger.info("Using model network from the application context.")
            return app_context.models.get(model_name)
        self._logger.info(f"Model not in context; loading TorchScript from {model_path}")
        return torch.jit.load(
            str(model_path), map_location=torch.device("cuda" if torch.cuda.is_available() else "cpu")
        )

    def setup(self, spec: OperatorSpec):
        spec.input(self.input_name_image)
        spec.output(self.output_name_result).condition(ConditionType.NONE)

    def compute(self, op_input, op_output, context):
        input_image = op_input.receive(self.input_name_image)
        if not isinstance(input_image, Image):
            raise ValueError(f"Expected an Image on '{self.input_name_image}', got {type(input_image)!r}")

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # DICOMSeriesToVolumeOperator yields a volume even for a single-frame radiograph, so the
        # array arrives as (1, H, W). Collapse to a single 2D frame before the 2D transforms.
        array = input_image.asnumpy()
        self._logger.info(f"Received image array with shape {array.shape}")
        while array.ndim > 2:
            array = array[0]

        # Same order as the training chain: Resized -> (orientation) -> ScaleIntensityd. The flip
        # stands in for training's Rotate90d(k=-1); see the module docstring for why they differ.
        pre = Compose([EnsureType(), Resize(spatial_size=(224, 224)), Flip(spatial_axis=1), ScaleIntensity()])
        tensor = pre(array[None])  # add channel -> (1, 224, 224)
        batch = torch.as_tensor(tensor)[None].float().to(device)  # add batch -> (1, 1, 224, 224)

        with torch.no_grad():
            logits = self.model(batch)
        probabilities = Compose([EnsureType(), Activations(sigmoid=True)])(logits)[0].cpu().numpy()

        findings = {
            label: float(probabilities[index]) for index, label in enumerate(LABELS) if index < len(probabilities)
        }
        positives = [label for label, score in findings.items() if score >= POSITIVE_THRESHOLD]

        detail = ", ".join(f"{label} {score:.3f}" for label, score in findings.items())
        summary = ", ".join(positives) if positives else "no findings above threshold"
        # Keep the report text ASCII-only. The SR writer does not set SpecificCharacterSet, so a
        # DICOM value defaults to the ISO-IR 6 repertoire and any non-ASCII character (an em dash,
        # a degree sign) is replaced with '?' in the stored instance.
        result_text = (
            f"FLIP chest radiograph classification: {summary}. "
            f"Probabilities: {detail}. "
            f"Threshold {POSITIVE_THRESHOLD}. Research model, not for clinical use."
        )
        self._logger.info(result_text)

        op_output.emit(result_text, self.output_name_result)

        Path.mkdir(self.output_folder, parents=True, exist_ok=True)
        with open(self.output_folder / "classification.json", "w") as handle:
            json.dump({"findings": findings, "positive": positives}, handle, indent=2)
