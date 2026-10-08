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

"""Pin the prostate app's plan → DynUNet mapping and the deep-supervision adapter around train_seg.

The network the ServerApp initialises and the one every ClientApp loads weights into come from the
same plan JSON through ``app.models``; these tests hold the mapping to what nnU-Net's planner meant
(one auxiliary output per decoder stage but the lowest, instance norm, leaky ReLU), reject the plans
DynUNet cannot build faithfully, and run ``train_seg`` two steps on the CPU with a toy plan so the
ported loop's device-independence (no ``.cuda()``, autocast gated on CUDA) stays pinned.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import torch
from monai.data import DataLoader, list_data_collate
from tutorial_apps import TUTORIALS_ROOT

PROSTATE_DIR = TUTORIALS_ROOT / "flower" / "3d_prostate_segmentation"
SHIPPED_PLAN = PROSTATE_DIR / "app" / "nnUNetPlans_segmentation.json"

# A toy plan in exactly the shape the planner writes: three stages, tiny feature widths, the first
# stage anisotropic (a 2-D kernel and no pooling) the way a thick-slice prostate plan comes out.
MINI_ARCH = {
    "network_class_name": "dynamic_network_architectures.architectures.unet.PlainConvUNet",
    "arch_kwargs": {
        "n_stages": 3,
        "features_per_stage": [4, 8, 16],
        "conv_op": "torch.nn.modules.conv.Conv3d",
        "kernel_sizes": [[1, 3, 3], [3, 3, 3], [3, 3, 3]],
        "strides": [[1, 1, 1], [1, 2, 2], [2, 2, 2]],
        "n_conv_per_stage": [2, 2, 2],
        "n_conv_per_stage_decoder": [2, 2],
        "conv_bias": True,
        "norm_op": "torch.nn.modules.instancenorm.InstanceNorm3d",
        "norm_op_kwargs": {"eps": 1e-5, "affine": True},
        "dropout_op": None,
        "dropout_op_kwargs": None,
        "nonlin": "torch.nn.LeakyReLU",
        "nonlin_kwargs": {"inplace": True},
    },
    "_kw_requires_import": ["conv_op", "norm_op", "dropout_op", "nonlin"],
}
MINI_PLAN = {
    "original_median_spacing_after_transp": [3.0, 0.5, 0.5],
    "original_median_shape_after_transp": [21, 383, 383],
    "foreground_intensity_properties_per_channel": {"0": {"mean": 300.0, "std": 150.0}},
    "configurations": {
        "3d_fullres": {
            "patch_size": [4, 16, 16],
            "spacing": [3.0, 0.5, 0.5],
            "batch_size": 2,
            "architecture": MINI_ARCH,
        }
    },
}


def _app_modules() -> dict[str, ModuleType]:
    return {name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")}


@pytest.fixture(scope="module")
def prostate_app() -> dict[str, ModuleType]:
    """``app.models``, ``app.task``, ``app.train_helpers`` under the ``app`` name the tutorial ships as."""
    displaced = _app_modules()
    for name in displaced:
        del sys.modules[name]
    sys.path.insert(0, str(PROSTATE_DIR))
    try:
        yield {
            name: importlib.import_module(f"app.{name}")
            for name in ("models", "task", "train_helpers", "preprocess", "data_loading", "client_app")
        }
    finally:
        sys.path.remove(str(PROSTATE_DIR))
        for name in _app_modules():
            del sys.modules[name]
        sys.modules.update(displaced)


def test_every_app_module_imports(prostate_app: dict[str, ModuleType]) -> None:
    """The simulator loads app.client_app and everything under it; a bad import only shows up there."""
    assert prostate_app["client_app"].app is not None
    assert callable(prostate_app["preprocess"].build_patch_iter)


def test_mini_plan_builds_the_planned_topology(prostate_app: dict[str, ModuleType]) -> None:
    models = prostate_app["models"]
    net = models.build_dynunet_from_plan(MINI_PLAN)

    assert net.deep_supr_num == 1  # n_stages - 2: every decoder resolution but the lowest
    assert [list(k) for k in net.kernel_size] == MINI_ARCH["arch_kwargs"]["kernel_sizes"]
    assert [list(s) for s in net.strides] == MINI_ARCH["arch_kwargs"]["strides"]
    assert list(net.filters) == [4, 8, 16]

    x = torch.zeros(1, 1, 4, 16, 16)  # (B, C, z, y, x) — the order train_seg feeds
    net.train()
    outputs = net(x)
    # train mode + DS: nnU-Net's list, full resolution first, each head at its own resolution
    assert [tuple(o.shape) for o in outputs] == [(1, 3, 4, 16, 16), (1, 3, 4, 8, 8)]
    net.eval()
    assert net(x).shape == (1, 3, 4, 16, 16)


def test_decoder_kernels_and_biases_follow_nnunet(prostate_app: dict[str, ModuleType]) -> None:
    """Stock DynUNet shifts the decoder kernels a level and drops the conv biases; the subclass must not."""
    net = prostate_app["models"].build_dynunet_from_plan(MINI_PLAN)
    kernels = MINI_ARCH["arch_kwargs"]["kernel_sizes"]
    # upsamples run deepest first; the one producing resolution r convolves with encoder stage r's kernel
    decoder = [list(up.conv_block.conv1.conv.kernel_size) for up in net.upsamples]
    assert decoder == kernels[:-1][::-1]
    convs = [m for m in net.modules() if isinstance(m, (torch.nn.Conv3d, torch.nn.ConvTranspose3d))]
    assert all(m.bias is not None for m in convs), "the plan's conv_bias: true reaches every convolution"

    plan = json.loads(json.dumps(MINI_PLAN))
    plan["configurations"]["3d_fullres"]["architecture"]["arch_kwargs"]["conv_bias"] = False
    no_bias = prostate_app["models"].build_dynunet_from_plan(plan)
    assert all(
        m.bias is None for m in no_bias.modules() if isinstance(m, torch.nn.Conv3d) and m.kernel_size != (1, 1, 1)
    )


def test_network_is_nnunets_plain_conv_unet(prostate_app: dict[str, ModuleType]) -> None:
    """With PlainConvUNet's weights copied in, the app's network computes the same outputs, bit for bit.

    Needs nnU-Net's network package, which the FL images (and this suite's environment) do not carry:
    run it with the tutorial's planning group (``uv sync --group planning``).
    """
    unet = pytest.importorskip("dynamic_network_architectures.architectures.unet")
    models = prostate_app["models"]
    arch = MINI_ARCH["arch_kwargs"]
    n = arch["n_stages"]
    torch.manual_seed(0)
    ref = unet.PlainConvUNet(
        input_channels=1,
        n_stages=n,
        features_per_stage=arch["features_per_stage"],
        conv_op=torch.nn.Conv3d,
        kernel_sizes=arch["kernel_sizes"],
        strides=arch["strides"],
        n_conv_per_stage=arch["n_conv_per_stage"],
        num_classes=3,
        n_conv_per_stage_decoder=arch["n_conv_per_stage_decoder"],
        conv_bias=True,
        norm_op=torch.nn.InstanceNorm3d,
        norm_op_kwargs=arch["norm_op_kwargs"],
        nonlin=torch.nn.LeakyReLU,
        nonlin_kwargs={"inplace": True},
        deep_supervision=True,
    )
    net = models.build_dynunet_from_plan(MINI_PLAN)
    assert sum(p.numel() for p in net.parameters()) == sum(p.numel() for p in ref.parameters())

    def block(r, d):  # StackedConvBlocks <-> UnetBasicBlock
        return [
            (r.convs[0].conv, d.conv1.conv),
            (r.convs[0].norm, d.norm1),
            (r.convs[1].conv, d.conv2.conv),
            (r.convs[1].norm, d.norm2),
        ]

    pairs = []
    for r, d in zip([ref.encoder.stages[s][0] for s in range(n)], [net.input_block, *net.downsamples, net.bottleneck]):
        pairs += block(r, d)
    for s in range(n - 1):  # decoder stage s (deepest first) <-> upsamples[s]
        pairs += [(ref.decoder.transpconvs[s], net.upsamples[s].transp_conv.conv)]
        pairs += block(ref.decoder.stages[s], net.upsamples[s].conv_block)
    pairs.append((ref.decoder.seg_layers[-1], net.output_block.conv.conv))
    for i in range(n - 2):  # head i sits at resolution i + 1 = decoder stage n - 3 - i
        pairs.append((ref.decoder.seg_layers[n - 3 - i], net.deep_supervision_heads[i].conv.conv))
    with torch.no_grad():
        for r, d in pairs:
            d.weight.copy_(r.weight)
            d.bias.copy_(r.bias)
    assert len({id(p) for _, d in pairs for p in d.parameters(recurse=False)}) == len(list(net.parameters()))

    x = torch.randn(2, 1, 4, 16, 16)
    ref.train(), net.train()
    with torch.no_grad():
        for a, b in zip(ref(x), net(x), strict=True):
            assert torch.equal(a, b)


def test_deep_supervision_toggle_keeps_the_state_dict(prostate_app: dict[str, ModuleType]) -> None:
    models = prostate_app["models"]
    net = models.build_dynunet_from_plan(MINI_PLAN)
    keys_on = set(net.state_dict())
    models.set_deep_supervision_enabled(False, network=net)
    net.train()
    assert net(torch.zeros(1, 1, 4, 16, 16)).ndim == 5, "DS off: the plain output even in train mode"
    assert set(net.state_dict()) == keys_on, "the auxiliary heads stay in the wire format either way"


def test_split_deep_supervision_outputs(prostate_app: dict[str, ModuleType]) -> None:
    models = prostate_app["models"]
    stacked = torch.arange(2 * 2 * 3 * 4).reshape(2, 2, 3, 1, 2, 2).float()
    outputs = models.split_deep_supervision_outputs(stacked)
    assert len(outputs) == 2
    assert torch.equal(outputs[0], stacked[:, 0])
    single = torch.zeros(2, 3, 1, 2, 2)
    assert models.split_deep_supervision_outputs(single) == [single]
    assert models.split_deep_supervision_outputs([single, single]) == [single, single]


def test_deep_supervision_weights_are_halving_and_normalised(prostate_app: dict[str, ModuleType]) -> None:
    weights = prostate_app["models"].deep_supervision_weights(3)
    assert weights == pytest.approx([4 / 7, 2 / 7, 1 / 7])
    assert prostate_app["models"].deep_supervision_weights(1) == [1.0]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda a: a.__setitem__("n_blocks_per_stage", [1, 3, 4]), "residual-encoder"),
        (lambda a: a.__setitem__("n_conv_per_stage", [1, 2, 2]), "convolutions per stage"),
        (lambda a: a.__setitem__("conv_op", "torch.nn.modules.conv.Conv2d"), "not a 3-D convolution"),
        (lambda a: a.__setitem__("norm_op", "torch.nn.modules.batchnorm.BatchNorm3d"), "not InstanceNorm3d"),
        (lambda a: a.__setitem__("nonlin", "torch.nn.ReLU"), "not torch.nn.LeakyReLU"),
    ],
)
def test_unbuildable_plans_raise_rather_than_approximate(
    prostate_app: dict[str, ModuleType], mutation, message: str
) -> None:
    plan = json.loads(json.dumps(MINI_PLAN))
    mutation(plan["configurations"]["3d_fullres"]["architecture"]["arch_kwargs"])
    with pytest.raises(ValueError, match=message):
        prostate_app["models"].build_dynunet_from_plan(plan)


def test_missing_plan_names_the_command(prostate_app: dict[str, ModuleType], tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make plan"):
        prostate_app["models"].load_plan(tmp_path / "nope.json")


@pytest.mark.parametrize("depth", [3, 4, 9])
def test_every_patch_has_the_planned_size(prostate_app: dict[str, ModuleType], depth: int) -> None:
    """A volume shallower than the patch is zero-padded up to it, never tiled into a smaller patch.

    PatchIterd alone shrinks the patch to the volume, and a depth the network's pooling cannot divide
    breaks the skip connections — what a 24-slice plan did to every study under 24 slices.
    """
    iterate = prostate_app["preprocess"].build_patch_iter((16, 16, 4))
    volume = {"image": torch.ones(1, 16, 16, depth), "mask": torch.ones(3, 16, 16, depth)}
    patches = [patch for patch, _ in iterate(volume)]
    assert len(patches) == -(-depth // 4)
    assert {tuple(p["image"].shape) for p in patches} == {(1, 16, 16, 4)}
    assert {tuple(p["mask"].shape) for p in patches} == {(3, 16, 16, 4)}
    if depth < 4:
        assert int((patches[0]["image"] == 0).all(dim=(0, 1, 2)).sum()) == 4 - depth, "zero-padded, not wrapped"


def test_plan_geometry_reverses_the_plan_axes(prostate_app: dict[str, ModuleType]) -> None:
    geometry = prostate_app["task"].plan_geometry(MINI_PLAN)
    assert geometry.target_spacing == (0.5, 0.5, 3.0)  # (x, y, z) for the MONAI loader
    assert geometry.patch_size == (16, 16, 4)  # (x, y, z) for PatchIterd
    assert geometry.patch_size_zyx == (4, 16, 16)  # the sliding-window ROI, network order
    assert geometry.crop_size == (384, 384)  # 383 padded up to even, both in-plane axes
    assert (geometry.image_mean, geometry.image_std) == (300.0, 150.0)


def test_criterion_penalises_predicting_gland_everywhere(prostate_app: dict[str, ModuleType]) -> None:
    """The background counts: marking every voxel as gland must cost more than marking none.

    MONAI's DiceCELoss(sigmoid=True) scored these overlapping channels with a softmax CE that ignores
    every all-zero (background) voxel, so "gland everywhere" was the cheaper answer — and the network
    learned it. The criterion's BCE is per channel and sees the background.
    """
    task = prostate_app["task"]
    criterion = task.build_criterion({"deep_supervision": False}, num_outputs=1)
    target = torch.zeros(1, 3, 4, 16, 16)
    target[:, 0, :, 6:10, 6:10] = 1  # a small gland: whole gland and TZ, the rest background
    target[:, 2, :, 6:10, 6:10] = 1
    everywhere = criterion(torch.full_like(target, 5.0), target)
    nowhere = criterion(torch.full_like(target, -5.0), target)
    right = criterion(target * 10 - 5, target)
    assert right < nowhere < everywhere

    logits = torch.randn(2, 3, 4, 16, 16)
    expected = task.DiceLoss(include_background=True, sigmoid=True)(logits, target.expand(2, -1, -1, -1, -1))
    expected = expected + torch.nn.functional.binary_cross_entropy_with_logits(logits, target.expand(2, -1, -1, -1, -1))
    assert criterion(logits, target.expand(2, -1, -1, -1, -1)).item() == pytest.approx(expected.item())


def test_deep_supervision_loss_renormalises_over_the_outputs_present(prostate_app: dict[str, ModuleType]) -> None:
    task = prostate_app["task"]
    mse = torch.nn.MSELoss()
    loss = task.DeepSupervisionLoss(mse, [4 / 7, 2 / 7, 1 / 7])
    a, b = torch.ones(1, 3, 2, 2, 2), torch.zeros(1, 3, 2, 2, 2)
    # two outputs, weights renormalised over the two: (4/6)*1 + (2/6)*0
    assert loss([a, b], [b, b]).item() == pytest.approx(4 / 6)
    # one output (eval mode): weight 1.0, so the loss equals the bare loss
    assert loss([a], [b]).item() == pytest.approx(mse(a, b).item())
    with pytest.raises(ValueError, match="prediction"):
        loss([a], [b, b])


def test_train_seg_runs_two_steps_on_cpu_with_the_toy_plan(prostate_app: dict[str, ModuleType]) -> None:
    """The ported loop end to end: patch batches, DS loss, gradient clipping, per-channel Dice, no GPU."""
    models, task, helpers = prostate_app["models"], prostate_app["task"], prostate_app["train_helpers"]
    torch.manual_seed(0)
    net = models.build_dynunet_from_plan(MINI_PLAN)
    conf = {"bf16": True, "deep_supervision": True, "max_norm": 12, "norm_type": 2, "scheduler": "Polynomial"}

    def patches(seed: int) -> list[dict]:
        g = torch.Generator().manual_seed(seed)
        # (C, x, y, z) tensors, as the loader produces them before train_seg's rearrange
        return [
            {
                "image": torch.rand(1, 16, 16, 4, generator=g),
                "mask": (torch.rand(3, 16, 16, 4, generator=g) > 0.5).half(),
                "accession_id": f"a{seed}",
                "coord": (0, 0, 0),
            }
            for _ in range(2)
        ]

    loader = DataLoader([patches(1), patches(2)], batch_size=1, collate_fn=list_data_collate)
    criterion = task.build_criterion(conf, num_outputs=1 + net.deep_supr_num)
    optimizer = task.build_optimizer(net, 0.01, {"momentum": 0.99, "weight_decay": 3e-5})
    scheduler = task.build_scheduler(optimizer, total_epochs=4, start_epoch=1)

    train_loss, val_loss, metrics = helpers.train_seg(
        conf, net, optimizer, scheduler, loader, loader, criterion, torch.device("cpu")
    )

    assert all(torch.isfinite(torch.tensor(v)) for v in (train_loss, val_loss))
    assert set(metrics) == {f"{split}/{k}_dice_avg" for split in ("train", "val") for k in ("mean", "wp", "pz", "tz")}
    assert scheduler.last_epoch == 1, "fast-forwarded to the round's start, not stepped per batch"


def test_evaluate_func_scores_whole_volumes(prostate_app: dict[str, ModuleType]) -> None:
    models, task = prostate_app["models"], prostate_app["task"]
    net = models.build_dynunet_from_plan(MINI_PLAN)
    volumes = [
        {"image": torch.rand(1, 32, 24, 8), "mask": (torch.rand(3, 32, 24, 8) > 0.5).float(), "accession_id": "v"}
    ]
    loader = DataLoader(volumes, batch_size=1)
    criterion = task.build_criterion({"deep_supervision": False}, num_outputs=1)

    loss, dice = task.evaluate_func(net, loader, criterion, torch.device("cpu"), roi_size_zyx=(4, 16, 16))

    assert torch.isfinite(torch.tensor(loss))
    assert set(dice) == {"dice_mean", "dice_wg", "dice_pz", "dice_tz"}
    assert dice["dice_mean"] == pytest.approx((dice["dice_wg"] + dice["dice_pz"] + dice["dice_tz"]) / 3)
    assert net.deep_supervision is True, "the flag is restored after the pass"


@pytest.mark.skipif(
    not SHIPPED_PLAN.is_file(), reason="app/nnUNetPlans_segmentation.json not generated yet (make plan)"
)
def test_shipped_plan_builds_and_is_stable(prostate_app: dict[str, ModuleType]) -> None:
    """The committed plan must build, and two get_model() calls must agree on the wire format."""
    models = prostate_app["models"]
    first, second = models.get_model(), models.get_model()
    assert list(first.state_dict()) == list(second.state_dict())
    assert {k: v.shape for k, v in first.state_dict().items()} == {k: v.shape for k, v in second.state_dict().items()}
    geometry = prostate_app["task"].plan_geometry(models.load_plan())
    assert all(v > 0 for v in geometry.patch_size)
    assert geometry.image_std > 0
