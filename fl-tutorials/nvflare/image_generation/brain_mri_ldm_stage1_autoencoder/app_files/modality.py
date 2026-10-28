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

"""Which MR sequence a file holds, and how to hand that to a conditioned network.

A brain-MRI study is four co-registered series — FLAIR, T1w, T1Gd, T2w — and each reaches the app
as its own single-channel NIfTI. The two paths a tutorial sees data on say which is which in
different places:

* the simulator layout names the file ``input_<label>_<case>.nii.gz``
  (``fl-tutorials/datasets/brain_mri/prepare_brain_mri_local_data.py``);
* a platform pull does not: XNAT's conversion names it ``input_<n>_<scan>.nii.gz``, under
  ``scans/<scan id>-<type>/``. The scan id is the DICOM ``SeriesNumber``, which the converter
  (``convert_brain_mri_to_dicom.py``) writes as 1–4 in MSD's channel order, ``SERIES_NUMBER``.

So the filename is read first and the scan folder second, with nothing to configure per site.
The match is against ``MODALITIES`` from ``config.json`` rather than a hard-coded list, because that
list is also the switch between the single-sequence and all-sequences forms of these tutorials: set
it to ``["T1w"]`` and every other series is left out of the cohort entirely.

Matching on whole ``_``-separated tokens, not substrings, is what keeps ``T1w`` from also claiming
``T1Gd`` — a substring test would put contrast-enhanced volumes in the T1w bucket and quietly train
a "T1w" model on two different sequences.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import torch

logger = logging.getLogger(__name__)

VOLUME_SUFFIX = ".nii.gz"
# The DICOM SeriesNumber each sequence was written with, the scan id XNAT files it under.
SERIES_NUMBER = {"FLAIR": 1, "T1w": 2, "T1Gd": 3, "T2w": 4}
_SCAN_DIR = re.compile(r"^(\d+)-")


def modality_of(path: Path | str, modalities: list[str]) -> str | None:
    """The configured modality this file holds, or None if it names none of them.

    Args:
        path (Path | str): The NIfTI file.
        modalities (list[str]): ``MODALITIES`` from ``config.json``.

    Returns:
        str | None: The matching label, or None when neither the filename nor the scan folder names
        a configured modality (a sequence the run deliberately excludes), or the filename names more
        than one (an ambiguous name — refused rather than guessed, so a mislabelled file never
        silently trains as the wrong sequence).
    """
    path = Path(path)
    name = path.name
    stem = name[: -len(VOLUME_SUFFIX)] if name.endswith(VOLUME_SUFFIX) else path.stem
    tokens = set(stem.split("_"))
    matched = [modality for modality in modalities if modality in tokens]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        logger.warning(f"{name} names more than one configured modality ({matched}); skipping it.")
        return None
    parts = path.parent.parts
    scan_ids = {int(m.group(1)) for up, part in zip(parts, parts[1:]) if up == "scans" and (m := _SCAN_DIR.match(part))}
    return next((modality for modality in modalities if SERIES_NUMBER.get(modality) in scan_ids), None)


def one_hot_condition(modality_index: torch.Tensor, num_modalities: int, device: torch.device) -> torch.Tensor:
    """One-hot the batch's modality indices into a cross-attention context tensor.

    ``DiffusionModelUNet`` in ``crossattn`` mode wants ``(batch, sequence, cross_attention_dim)``.
    The condition here is a single categorical fact per sample, so the sequence length is 1 and
    ``cross_attention_dim`` is the number of modalities — which is why ``config.json``'s
    ``net_config.diffusion_model.cross_attention_dim`` must equal ``len(MODALITIES)`` (pinned by
    fl-tutorials/tests/test_image_generation_config_parity.py).

    Args:
        modality_index (torch.Tensor): Integer index per sample, as collated from the datalist.
        num_modalities (int): ``len(MODALITIES)``.
        device (torch.device): Device the model lives on.

    Returns:
        torch.Tensor: ``(batch, 1, num_modalities)``, float.
    """
    indices = modality_index.to(device=device, dtype=torch.long).reshape(-1)
    return torch.nn.functional.one_hot(indices, num_classes=num_modalities).float().unsqueeze(1)
