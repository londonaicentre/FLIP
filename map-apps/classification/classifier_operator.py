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
an image, whereas a classifier produces labels. This operator therefore loads the network itself
and emits a text result, which the DICOM Structured Report writer turns into a DICOM object.

Both bundle forms ``flip.export`` writes are accepted: a single TorchScript file, and a directory
bundle whose weights are a plain state dict applied to the architecture its config names. The
directory form exists because PyTorch has TorchScript on a removal path (FLIP#1019); loading it
here touches ``torch.jit`` at no point, so this operator survives ``torch.jit`` going away as long
as the model was exported that way.

The preprocessing below mirrors the training application's ``transforms.py::get_xray_transforms``
(validation branch): resize to 224x224, then scale intensity to [0, 1] on a single channel. Keeping
these in step with training is the app author's responsibility; a mismatch here degrades the result
silently.

There is deliberately **no orientation transform here**, on either side. The training chain loads
with ``LoadImaged(reader="PydicomReader", swap_ij=False)``, which returns the pixel array exactly as
``PixelData`` stores it — ``(row, column)`` — rather than MONAI's default transpose; this operator
receives its array from ``DICOMSeriesToVolumeOperator``, which preserves the same order. Both paths
agree on the image as stored, with nothing to undo (``fl-tutorials/tests/`` pins the training side).

Do not "restore" an orientation transform here by transcribing one from a training chain — a chain's
orientation step, when one exists, serves its loader, not the model. Verified by pushing the same CR
study through both paths: with no transform on either side, the two agree bit-for-bit
(``max|diff| == 0``) on both a square and a non-square radiograph, and disagree under every rotation
or flip.
"""

import json
import logging
import os
import sys
from pathlib import Path

import torch
from monai.bundle import ConfigParser
from monai.deploy.core import AppContext, ConditionType, Fragment, Image, Operator, OperatorSpec
from monai.transforms import Activations, Compose, EnsureType, Resize, ScaleIntensity

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
    # Either a TorchScript file or a directory bundle; HOLOSCAN_MODEL_PATH points at whichever the
    # MAP was packaged with.
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
        """Take the network from the application context, else load the bundle at ``model_path``.

        Args:
            app_context (AppContext): The MAP's application context, which may already hold the model.
            model_path (Path): A TorchScript file, or a directory bundle written by ``flip.export``.
            model_name (str): Model name, needed only in the multi-model case.

        Returns:
            The network, ready for inference.
        """
        if app_context.models:
            self._logger.info("Using model network from the application context.")
            return app_context.models.get(model_name)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if model_path.is_dir():
            self._logger.info(f"Model not in context; loading directory bundle from {model_path}")
            return self._load_directory_bundle(model_path, device)
        self._logger.info(f"Model not in context; loading TorchScript from {model_path}")
        return torch.jit.load(str(model_path), map_location=device)

    def _load_directory_bundle(self, bundle_path: Path, device: torch.device):
        """Load a directory bundle without TorchScript, the way MONAI Deploy's own loader does.

        The architecture comes from the bundle's ``network``/``network_def`` config entry, which for
        a ``flip.export`` bundle names ``get_model`` in the application copied under ``scripts/``.
        That import needs the bundle root on ``sys.path`` first — the application's modules import
        one another flatly, exactly as they do under the FL executors.

        ``HOLOSCAN_MODEL_PATH`` points at whatever directory the packager copied ``--models`` into,
        which may be the bundle itself or a directory holding it, so one level of nesting is
        searched before giving up.

        Args:
            bundle_path (Path): The bundle directory, or a directory containing one.
            device (torch.device): Device to place the network on.

        Returns:
            The network in eval mode, with the bundle's weights applied.

        Raises:
            IOError: If no bundle with ``models/model.pt`` is found.
            RuntimeError: If no config entry names an architecture.
        """
        if not (bundle_path / "models" / "model.pt").is_file():
            nested = [child for child in sorted(bundle_path.iterdir()) if (child / "models" / "model.pt").is_file()]
            if not nested:
                raise IOError(f"No models/model.pt in {bundle_path} or any directory directly under it")
            bundle_path = nested[0]
            self._logger.info(f"Found the bundle one level down, at {bundle_path}")
        weights = bundle_path / "models" / "model.pt"

        if str(bundle_path) not in sys.path:
            sys.path.insert(0, str(bundle_path))

        parser = ConfigParser()
        parser.read_meta(f=json.loads((bundle_path / "configs" / "metadata.json").read_text()))
        parser.read_config([bundle_path / "configs" / "inference.json"])
        parser.parse()

        network = None
        for key in ("network", "network_def"):
            if parser.get(key) is not None:
                network = parser.get_parsed_content(key)
                break
        if network is None:
            raise RuntimeError(f"No 'network' or 'network_def' in {bundle_path / 'configs' / 'inference.json'}")

        # weights_only=True: a bundle's weights are a plain state dict, so nothing is lost by
        # refusing to unpickle arbitrary objects out of a file that arrived with the MAP.
        network.load_state_dict(torch.load(str(weights), map_location=device, weights_only=True), strict=True)
        return network.to(device).eval()

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

        # Same order as the training chain: Resized -> ScaleIntensityd. No orientation transform —
        # this path is already on the image as DICOM stores it; see the module docstring.
        pre = Compose([EnsureType(), Resize(spatial_size=(224, 224)), ScaleIntensity()])
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
