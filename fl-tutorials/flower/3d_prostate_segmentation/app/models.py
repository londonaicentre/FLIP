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

"""The segmentation network, built from the nnU-Net plan that ships beside this file.

nnU-Net's *experiment planner* (``calculate_dataset_fingerprint_segmentation.py``, run once, offline,
over every participating site) decides the U-Net topology — how many resolution stages, which kernel
sizes and pooling strides at each, how many feature maps — from the cohort's voxel spacing and
shape. Its output, ``nnUNetPlans_segmentation.json``, is committed next to this module, and this is
the **one** place that turns it into a ``torch.nn.Module``: the ServerApp calls ``get_model()`` to
create the initial global weights and every ClientApp calls the same zero-argument factory before
loading the weights it receives, so the two can never disagree about the architecture.

The network class is ``NnUNetDynUNet``, a small subclass of MONAI's ``DynUNet``, rather than
nnU-Net's own ``PlainConvUNet``: the FL images carry MONAI but not ``nnunetv2``, and an app must not
install packages at run time. Stock ``DynUNet`` is not quite nnU-Net's network — it drops the conv
biases, gives each decoder stage the kernel of the stage below it, and returns its deep-supervision
heads upsampled to full resolution — so the subclass undoes all three, and with the same weights it
computes exactly what ``PlainConvUNet`` does (``tests/test_prostate_model.py`` pins it bit for bit
when ``nnunetv2`` is installed). The mapping from the plan's ``arch_kwargs`` is spelled out in
``build_dynunet_from_plan``; anything the plan asks for that DynUNet cannot express raises rather
than being silently approximated, because a topology mismatch between sites is exactly what makes
weight aggregation impossible (see the README's "nnU-Net plans").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
from monai.networks.blocks.dynunet_block import UnetUpBlock
from monai.networks.nets import DynUNet
from torch import nn

# The plan the planner wrote; committed so server and clients build the same net.
PLAN_PATH = Path(__file__).with_name("nnUNetPlans_segmentation.json")
# One t2w channel in; three overlapping label channels out — [whole gland, PZ, TZ], the order
# `dataset.PicaiDataset.combine_masks` stacks them in.
IN_CHANNELS = 1
OUT_CHANNELS = 3
CONFIGURATION = "3d_fullres"


def load_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    """Read the nnU-Net plan JSON (``nnUNetPlans_segmentation.json``).

    Args:
        path: The plan file. Defaults to the one committed beside this module.

    Returns:
        The parsed plan.

    Raises:
        FileNotFoundError: Naming the planner command to run, when the plan has not been generated.
    """
    if not Path(path).is_file():
        raise FileNotFoundError(
            f"nnU-Net plan not found at {path}. Generate it with `make plan` in the tutorial directory "
            "(runs calculate_dataset_fingerprint_segmentation.py over every site) and commit the result."
        )
    with open(path) as handle:
        return json.load(handle)


def _configuration(plan: dict[str, Any], name: str = CONFIGURATION) -> dict[str, Any]:
    try:
        return plan["configurations"][name]
    except KeyError as err:
        raise KeyError(f"plan has no configuration {name!r}: {sorted(plan.get('configurations', {}))}") from err


class NnUNetDynUNet(DynUNet):
    """``DynUNet`` made to compute exactly what nnU-Net's ``PlainConvUNet`` computes.

    Same constructor as ``DynUNet`` plus ``conv_bias``. Three departures from stock ``DynUNet``, each
    restoring nnU-Net's behaviour:

    * **Conv biases.** ``UnetBasicBlock`` builds its convolutions with ``bias=False``; nnU-Net's plans
      ask for ``conv_bias: true``. The missing biases are added (zero-initialised, as nnU-Net's
      ``InitWeights_He`` leaves them) and the transposed convolutions get theirs via ``trans_bias``.
    * **Decoder kernels.** nnU-Net's decoder stage at a resolution uses that resolution's encoder
      kernel; stock ``DynUNet`` uses the kernel of the stage one level deeper (``kernel_size[1:]``),
      which on a thick-slice plan turns an in-plane ``(1, 3, 3)`` stage into a ``(3, 3, 3)`` one.
    * **Deep-supervision outputs.** In training mode with deep supervision on, the net returns a list
      — full resolution first, every auxiliary head at its own resolution — the way nnU-Net does,
      instead of stock ``DynUNet``'s heads interpolated to full resolution and stacked. The loss then
      sees each head against a target resized to it (``train_seg``'s ``_build_ds_targets``), as in
      nnU-Net. Evaluation mode returns the single full-resolution output, unchanged.

    Every parameter maps one-to-one onto ``PlainConvUNet``'s, so the parameter count is nnU-Net's.
    """

    def __init__(self, *args: Any, conv_bias: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, trans_bias=conv_bias, **kwargs)
        if conv_bias:
            for module in self.modules():
                if isinstance(module, nn.Conv3d) and module.bias is None:
                    module.bias = nn.Parameter(torch.zeros(module.out_channels))

    def get_upsamples(self) -> nn.ModuleList:
        """The decoder, with each stage's kernel taken from its own resolution's encoder stage."""
        inp, out = self.filters[1:][::-1], self.filters[:-1][::-1]
        strides, kernel_size = self.strides[1:][::-1], self.kernel_size[:-1][::-1]
        return self.get_module_list(
            inp,
            out,
            kernel_size,
            strides,
            UnetUpBlock,  # type: ignore[arg-type]
            self.upsample_kernel_size[::-1],
            trans_bias=self.trans_bias,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor | list[torch.Tensor]:
        out = self.output_block(self.skip_layers(x))
        if self.training and self.deep_supervision:
            # The skip layers fill self.heads during the pass, highest resolution first.
            return [out, *self.heads]
        return out


def build_dynunet_from_plan(
    plan: dict[str, Any],
    *,
    in_channels: int = IN_CHANNELS,
    out_channels: int = OUT_CHANNELS,
    deep_supervision: bool = True,
) -> NnUNetDynUNet:
    """Instantiate an ``NnUNetDynUNet`` with the topology the plan's ``3d_fullres`` architecture describes.

    The plan's ``kernel_sizes`` / ``strides`` are per stage in nnU-Net's transposed ``(z, y, x)``
    order; the training loop feeds the network ``(B, C, z, y, x)`` tensors (``train_helpers.train_seg``
    rearranges ``b c h w d -> b c d h w``), so they are passed through unchanged.

    Mapping (plan → DynUNet):

    * ``kernel_sizes`` → ``kernel_size``; ``strides`` → ``strides``; ``strides[1:]`` →
      ``upsample_kernel_size`` (a transposed conv undoes each pooling step).
    * ``features_per_stage`` → ``filters``; ``conv_bias`` → ``conv_bias``.
    * ``norm_op`` (``InstanceNorm3d`` + its kwargs) → ``norm_name=("instance", {...})``; ``nonlin``
      (``LeakyReLU``) → ``act_name=("leakyrelu", {...})``.
    * ``n_stages - 2`` → ``deep_supr_num``: nnU-Net supervises every decoder resolution but the
      lowest, i.e. ``n_stages - 1`` outputs; the net returns ``1 + deep_supr_num``.

    Args:
        plan: A parsed plan (``load_plan``).
        in_channels: Input channels (one t2w volume).
        out_channels: Output channels (whole gland, PZ, TZ).
        deep_supervision: Whether the net returns the auxiliary decoder outputs in training mode.
            Toggle later with ``set_deep_supervision_enabled``.

    Returns:
        The network, on the CPU, randomly initialised.

    Raises:
        ValueError: When the plan asks for something DynUNet cannot build faithfully — a residual
            encoder, more or fewer than two convolutions per stage, a non-3-D convolution, a norm
            or activation other than instance norm / leaky ReLU, or fewer than three stages.
    """
    architecture = _configuration(plan)["architecture"]
    arch = architecture["arch_kwargs"]

    if "n_blocks_per_stage" in arch:
        raise ValueError(
            f"plan architecture {architecture.get('network_class_name')} is a residual-encoder U-Net; "
            "DynUNet's basic blocks cannot reproduce it — plan with the default PlainConvUNet."
        )
    n_stages = int(arch["n_stages"])
    if n_stages < 3:
        raise ValueError(f"plan has {n_stages} stage(s); deep supervision needs at least 3.")
    per_stage = list(arch["n_conv_per_stage"]) + list(arch["n_conv_per_stage_decoder"])
    if any(int(n) != 2 for n in per_stage):
        raise ValueError(f"plan uses {per_stage} convolutions per stage; DynUNet's UnetBasicBlock is fixed at two.")
    if not str(arch["conv_op"]).endswith("Conv3d"):
        raise ValueError(f"plan conv_op {arch['conv_op']!r} is not a 3-D convolution.")
    if not str(arch["norm_op"]).endswith("InstanceNorm3d"):
        raise ValueError(f"plan norm_op {arch['norm_op']!r} is not InstanceNorm3d.")
    if str(arch["nonlin"]) != "torch.nn.LeakyReLU":
        raise ValueError(f"plan nonlin {arch['nonlin']!r} is not torch.nn.LeakyReLU.")

    kernel_sizes = [list(k) for k in arch["kernel_sizes"]]
    strides = [list(s) for s in arch["strides"]]
    filters = [int(f) for f in arch["features_per_stage"]]
    if not len(kernel_sizes) == len(strides) == len(filters) == n_stages:
        raise ValueError(
            f"plan is inconsistent: n_stages={n_stages}, {len(kernel_sizes)} kernel sizes, "
            f"{len(strides)} strides, {len(filters)} feature widths."
        )

    norm_kwargs = dict(arch.get("norm_op_kwargs") or {})
    nonlin_kwargs = {"inplace": True, "negative_slope": 0.01, **dict(arch.get("nonlin_kwargs") or {})}

    return NnUNetDynUNet(
        spatial_dims=3,
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=kernel_sizes,
        strides=strides,
        upsample_kernel_size=strides[1:],
        filters=filters,
        norm_name=("instance", norm_kwargs),
        act_name=("leakyrelu", nonlin_kwargs),
        deep_supervision=deep_supervision,
        deep_supr_num=n_stages - 2,
        res_block=False,
        conv_bias=bool(arch.get("conv_bias", True)),
    )


def get_model() -> nn.Module:
    """The zero-argument factory the ServerApp and every ClientApp share (built from ``PLAN_PATH``)."""
    return build_dynunet_from_plan(load_plan())


def set_deep_supervision_enabled(enabled: bool, is_ddp: bool = False, network: nn.Module | None = None) -> nn.Module:
    """Turn the auxiliary decoder outputs on or off — the same call shape ``network.py`` had.

    DynUNet reads its ``deep_supervision`` flag on every forward and keeps the auxiliary heads in
    ``state_dict`` either way, so toggling never changes the weights on the wire: a strict
    ``load_state_dict`` on the server and the clients stays valid whatever each side sets here.

    Args:
        enabled: True to return the auxiliary outputs in training mode.
        is_ddp: Unwrap a ``DistributedDataParallel`` module first. Kept for interface parity.
        network: The network to configure.

    Returns:
        The (unwrapped) network.
    """
    if network is None:
        raise ValueError("network is required")
    module = network.module if is_ddp else network
    module.deep_supervision = enabled
    return module


def split_deep_supervision_outputs(
    logits: torch.Tensor | list[torch.Tensor] | tuple[torch.Tensor, ...],
) -> list[torch.Tensor]:
    """Normalise a network output to nnU-Net's list-of-outputs convention, full resolution first.

    ``train_helpers.train_seg`` was written against nnU-Net, whose network returns a *list* of
    outputs when deep supervision is on — as ``NnUNetDynUNet`` does, so a list passes through. A
    stock ``DynUNet`` stacks them along dim 1 instead (a ``(B, 1 + deep_supr_num, C, *spatial)``
    tensor, every head interpolated to full resolution), which is unbound; a single output is
    wrapped. Head 0 is the network's real output.

    Args:
        logits: Whatever the network returned.

    Returns:
        A list with the full-resolution output first, then the auxiliary heads (if any).
    """
    if isinstance(logits, (list, tuple)):
        return list(logits)
    if logits.ndim == 6:
        return list(logits.unbind(1))
    return [logits]


def deep_supervision_weights(num_outputs: int) -> list[float]:
    """nnU-Net's loss weights per output — ``1/2**i``, normalised to sum to one."""
    weights = [1.0 / (2**i) for i in range(num_outputs)]
    total = sum(weights)
    return [w / total for w in weights]
