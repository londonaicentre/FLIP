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

"""Guard the three image-synthesis tutorials' config wiring, which otherwise fails silently.

The ``autoencoder`` / ``diffusion_model`` / ``latent_diffusion_model`` tutorials are three
independent single-stage jobs, and the latent one consumes the autoencoder one's output as a frozen
encoder declared via ``SERVER_CHECKPOINT``. That handoff spans two directories and rests entirely on
convention, so every way it breaks breaks **quietly**:

* ``InitialCheckpointPTModelPersistor`` loads the checkpoint with ``strict=False``. A wrong submodule
  name or a drifted ``net_config.stage_1`` therefore matches *nothing*, raises *nothing*, and leaves
  the diffusion model training against a randomly-initialised encoder while it reports entirely
  plausible losses. This is the failure mode the tests here exist for.
* ``SERVER_CHECKPOINT`` naming a file that ``job.py`` does not stage means the persistor looks for a
  file nobody produced — logged, not raised.
* ``AGGREGATE_ONLY_REGEX`` is a *privacy* control as well as a bandwidth one. ``PercentilePrivacy``
  takes its percentile over every variable concatenated together, so if the frozen autoencoder's
  exact-zero diffs stay in the update they drag the cutoff to ``0.0`` and the DP sparsification
  silently degrades to the ``gamma`` clip alone. An over-broad regex re-opens that hole.
* ``configure_config`` in the FL API *raises* when a config has exactly one local-rounds key not
  named exactly ``LOCAL_ROUNDS`` — but only at submit time, i.e. after upload, on the platform.
  Catching it here keeps that a local failure.

Since FLIP#1221 the three no longer share a cohort: ``autoencoder`` and ``latent_diffusion_model``
train on 3-D brain MRI (four MR sequences per study), while ``diffusion_model`` stays on 2-D chest
X-rays as the cheap read-this-first tutorial. That adds a second silent-failure surface, because the
brain pair's conditioning is spread across two config keys that must agree:

* ``MODALITIES`` decides which sequences enter the cohort; ``cross_attention_dim`` decides how many
  classes the UNet's cross-attention is built for. A model told "4 classes" while the loader feeds 5
  raises; told "4" while the loader feeds 1 trains happily on three dead one-hot columns. And
  ``with_conditioning: false`` beside four modalities trains an unconditional model on four mixed
  sequences, whose failure mode is a blurred average that looks like slow convergence.

These are static checks over the shipped ``config.json`` / ``job.py`` files, plus one that
instantiates the latent network to compare the regex against real parameter names. Nothing trains.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
import torch

_SYNTHESIS_ROOT = Path(__file__).resolve().parents[1] / "nvflare" / "image_synthesis"

_AUTOENCODER = _SYNTHESIS_ROOT / "autoencoder"
_DIFFUSION = _SYNTHESIS_ROOT / "diffusion_model"
_LATENT_DIFFUSION = _SYNTHESIS_ROOT / "latent_diffusion_model"

_ALL_TUTORIALS = (_AUTOENCODER, _DIFFUSION, _LATENT_DIFFUSION)
# The two retargeted onto the 3-D brain-MRI cohort, which share the modality contract.
_BRAIN_MRI_TUTORIALS = (_AUTOENCODER, _LATENT_DIFFUSION)

# The submodule prefix the checkpoint handoff rides on. Both networks must name the autoencoder
# submodule `autoencoder`, so its parameters are keyed `autoencoder.*` in both state dicts.
_AUTOENCODER_PREFIX = "autoencoder."
# The submodule the latent job actually trains, and so the only thing that may travel per round.
_DIFFUSION_PREFIX = "diffusion_model."


def _config(tutorial: Path) -> dict:
    """Read a tutorial's shipped ``app_files/config.json``."""
    return json.loads((tutorial / "app_files" / "config.json").read_text())


def _load_app_module(tutorial: Path, module: str) -> ModuleType:
    """Import one of a tutorial's ``app_files/*.py`` modules under a unique name.

    Every tutorial ships modules with identical names (``models``, ``transforms``), so they would
    collide in ``sys.modules``; the name is namespaced by tutorial directory, as
    ``tutorial_apps.TutorialApp`` does for the ``data_utils`` modules. Importing ``models`` executes
    its module-level ``_net = ...`` singleton, which is the point — it builds the real network from
    the real config.
    """
    path = tutorial / "app_files" / f"{module}.py"
    module_name = f"fl_tutorials_under_test.image_synthesis.{tutorial.name}.{module}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = loaded

    # `trainer` imports its flat siblings by bare name (`from plot_utils import ...`), exactly as
    # the deployed job does — an app dir is copied whole and imported flat. So the app dir goes on
    # sys.path for the duration, and every bare name the import registered comes back off afterwards:
    # leaving `models` or `transforms` cached would make the *next* tutorial's import silently resolve
    # to this one's copy, and the cross-tutorial comparisons here would then compare a file with
    # itself. (test_autoencoder_offline_backbone.py does the same dance for the same reason.)
    app_dir = str(tutorial / "app_files")
    before = set(sys.modules)
    sys.path.insert(0, app_dir)
    try:
        spec.loader.exec_module(loaded)
    except BaseException:
        # Never leave a half-initialised module reachable from the cache.
        del sys.modules[module_name]
        raise
    finally:
        sys.path.remove(app_dir)
        for name in set(sys.modules) - before - {module_name}:
            if "." not in name:
                sys.modules.pop(name, None)
    return loaded


@pytest.fixture(scope="module")
def latent_state_dict_keys() -> list[str]:
    """Parameter names of the latent diffusion network, built from its shipped config."""
    return list(_load_app_module(_LATENT_DIFFUSION, "models").get_model().state_dict())


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_declares_standard_job_type(tutorial: Path) -> None:
    """All three are single-stage FedAvg jobs, so all three declare ``job_type: standard``.

    The ``diffusion_model`` *directory* deliberately shares its name with the platform's older
    two-stage ``diffusion_model`` *job type*, which none of these tutorials uses — this pins that
    distinction so a future edit cannot quietly adopt the wrong one.
    """
    assert _config(tutorial)["job_type"] == "standard"


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_env_app_job_type_matches_config(tutorial: Path) -> None:
    """``.env.app``'s ``JOB_TYPE`` agrees with ``config.json``'s ``job_type``."""
    env_text = (tutorial / ".env.app").read_text()
    declared = re.search(r"^JOB_TYPE=(\S+)$", env_text, re.MULTILINE)
    assert declared is not None, f"{tutorial.name}/.env.app declares no JOB_TYPE"
    assert declared.group(1) == _config(tutorial)["job_type"]


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_rounds_keys_are_unsuffixed(tutorial: Path) -> None:
    """Exactly one local-rounds key, named exactly ``LOCAL_ROUNDS``, paired with ``GLOBAL_ROUNDS``.

    ``fl_api.utils.prepare_config.configure_config`` raises when a config carries a single
    local-rounds key under any other name — so the retired two-stage ``LOCAL_ROUNDS_AE`` /
    ``LOCAL_ROUNDS_DM`` pairs must not reappear here. That check runs at submit time on the platform,
    which is far too late to discover it.
    """
    config = _config(tutorial)
    local_keys = [key for key in config if key.startswith("LOCAL_ROUNDS")]
    assert local_keys == ["LOCAL_ROUNDS"], (
        f"{tutorial.name} must declare exactly one local-rounds key named LOCAL_ROUNDS, got {local_keys}"
    )
    assert "GLOBAL_ROUNDS" in config
    global_keys = [key for key in config if key.startswith("GLOBAL_ROUNDS")]
    assert global_keys == ["GLOBAL_ROUNDS"], f"{tutorial.name} has stray global-rounds keys: {global_keys}"


def test_latent_stage_1_matches_autoencoder_tutorial() -> None:
    """The latent job's ``net_config.stage_1`` is the autoencoder tutorial's, exactly.

    ``stage_1`` *is* the architecture the uploaded checkpoint was trained for. Any drift makes the
    checkpoint unloadable — and because the persistor loads ``strict=False``, unloadable means
    "silently ignored", not "raises". This is the single most valuable assertion in this file.
    """
    autoencoder_stage_1 = _config(_AUTOENCODER)["net_config"]["stage_1"]
    latent_stage_1 = _config(_LATENT_DIFFUSION)["net_config"]["stage_1"]
    assert latent_stage_1 == autoencoder_stage_1, (
        "latent_diffusion_model's net_config.stage_1 has drifted from autoencoder's. The uploaded "
        "checkpoint will load nothing (strict=False swallows it) and the diffusion model will train "
        "against a randomly-initialised encoder."
    )


def test_latent_diffusion_channels_match_autoencoder_latent_channels() -> None:
    """The diffusion UNet's in/out channels are the autoencoder's ``latent_channels``.

    This is what makes it a *latent* diffusion model. The pixel-space tutorial is the contrast: its
    channels are the image's. Getting this wrong is the easiest error when adapting one into the
    other.
    """
    latent_config = _config(_LATENT_DIFFUSION)["net_config"]
    latent_channels = latent_config["stage_1"]["latent_channels"]
    assert latent_config["diffusion_model"]["in_channels"] == latent_channels
    assert latent_config["diffusion_model"]["out_channels"] == latent_channels


def test_pixel_diffusion_channels_match_image_channels() -> None:
    """The pixel-space UNet's in/out channels are the *image's*, not a latent's.

    Pinned against the transform chain's single-channel NIfTI input (``EnsureChannelFirstd`` with
    ``channel_dim="no_channel"``), so this tutorial cannot silently inherit the latent tutorial's 3.
    """
    diffusion_config = _config(_DIFFUSION)["net_config"]["diffusion_model"]
    assert diffusion_config["in_channels"] == 1
    assert diffusion_config["out_channels"] == 1


def test_pixel_diffusion_spatial_shape_is_divisible_by_downsampling() -> None:
    """``spatial_shape`` survives every UNet downsampling without a ragged dimension.

    The pixel-space job feeds images straight to the UNet, so — unlike the latent job, which pads its
    latent grid via ``derive_new_latent_shape`` — there is nothing to absorb an indivisible shape.
    """
    config = _config(_DIFFUSION)
    channels = config["net_config"]["diffusion_model"]["channels"]
    factor = 2 ** (len(channels) - 1)
    ragged = [dim for dim in config["spatial_shape"] if dim % factor]
    assert not ragged, (
        f"spatial_shape {config['spatial_shape']} must be divisible by {factor} "
        f"(2 ** (len(channels) - 1) for channels={channels}); offending dims: {ragged}"
    )


@pytest.mark.parametrize("tutorial", _BRAIN_MRI_TUTORIALS, ids=lambda p: p.name)
def test_modality_list_is_non_empty_and_unique(tutorial: Path) -> None:
    """``MODALITIES`` is a real list of distinct labels.

    It is the cohort filter *and* the one-hot vocabulary, so a duplicate would give two sequences
    the same index — silently training them as one class — and an empty list would select no files
    at all, which the loader turns into a raised error rather than an empty epoch.
    """
    modalities = _config(tutorial)["MODALITIES"]
    assert modalities, "MODALITIES is empty: the loader would match no files"
    assert len(set(modalities)) == len(modalities), f"duplicate entries in MODALITIES: {modalities}"


def test_modality_lists_match_across_the_brain_mri_tutorials() -> None:
    """The autoencoder and the latent job agree on the sequences, in the same order.

    Order is part of the contract, not just membership: the index into ``MODALITIES`` *is* the
    one-hot position. Two lists holding the same four labels in different orders would train the
    autoencoder on the same pixels but teach the diffusion model to call a T2w volume a FLAIR.
    """
    lists = {t.name: _config(t)["MODALITIES"] for t in _BRAIN_MRI_TUTORIALS}
    assert len(set(map(tuple, lists.values()))) == 1, f"MODALITIES differs between tutorials: {lists}"


def test_latent_conditioning_agrees_with_the_modality_list() -> None:
    """``with_conditioning`` and ``cross_attention_dim`` are consistent with ``MODALITIES``.

    The two supported shapes, and nothing between them:

    * conditioned   — ``with_conditioning: true``  and ``cross_attention_dim == len(MODALITIES)``
    * unconditional — ``with_conditioning: false`` and ``cross_attention_dim == 0``

    ``models.py`` maps a ``cross_attention_dim`` of 0 to ``None``, so a non-zero value beside
    ``with_conditioning: false`` builds cross-attention layers nothing ever feeds, and a mismatched
    non-zero value beside ``true`` raises only once a real batch reaches the UNet — on a trust, mid
    round. Both are caught here instead.
    """
    config = _config(_LATENT_DIFFUSION)
    diffusion = config["net_config"]["diffusion_model"]
    if diffusion["with_conditioning"]:
        assert diffusion["cross_attention_dim"] == len(config["MODALITIES"]), (
            f"cross_attention_dim {diffusion['cross_attention_dim']} != len(MODALITIES) "
            f"{len(config['MODALITIES'])}: the one-hot condition would not fit the attention layers"
        )
    else:
        assert diffusion["cross_attention_dim"] == 0, (
            "with_conditioning is false, so cross_attention_dim must be 0 (models.py maps 0 -> None)"
        )


def test_modality_helper_is_identical_across_tutorials() -> None:
    """Both copies of ``modality.py`` are byte-identical.

    Duplicated for the same reason ``transforms.py`` is — ``app_files/`` is the per-model upload
    unit — and the two copies must agree, because the index the autoencoder's loader assigns is the
    index the latent job one-hot encodes.
    """
    contents = {t.name: (t / "app_files" / "modality.py").read_bytes() for t in _BRAIN_MRI_TUTORIALS}
    assert len(set(contents.values())) == 1, f"modality.py differs between {sorted(contents)}"


def test_modality_parse_matches_the_cohort_filenames() -> None:
    """The filename parse handles the real names, and refuses the near-miss.

    ``T1w`` is a prefix of nothing but a substring of ``T1Gd``-adjacent spellings, and the case ids
    carry underscores of their own (``BRATS_001``), so a naive ``split("_")[1]`` or an ``in``
    substring test both go wrong on real data. Pinned against the names
    ``prepare_brain_mri_local_data.py`` writes and dcm2niix reproduces.
    """
    modality = _load_app_module(_AUTOENCODER, "modality")
    modalities = _config(_AUTOENCODER)["MODALITIES"]

    for label in modalities:
        assert modality.modality_of(f"input_{label}_BRATS_001.nii.gz", modalities) == label

    # A sequence this run excludes is not an error, just not selected.
    assert modality.modality_of("input_T1w_BRATS_001.nii.gz", ["FLAIR"]) is None
    # The label, not the case id, decides — even when the case id looks like one.
    assert modality.modality_of("input_FLAIR_BRATS_T2w.nii.gz", modalities) is None
    # And the non-input files in the same directory are never claimed.
    assert modality.modality_of("label_BRATS_001.nii.gz", modalities) is None


def test_one_hot_condition_has_the_shape_cross_attention_expects() -> None:
    """The condition is ``(batch, 1, len(MODALITIES))`` — the shape ``mode="crossattn"`` wants.

    A one-hot of the wrong rank does not raise where it is built; it raises (or worse, broadcasts)
    inside the UNet's attention, which is a long way from the mistake.
    """
    modality = _load_app_module(_AUTOENCODER, "modality")
    condition = modality.one_hot_condition(torch.tensor([0, 2, 1]), 4, torch.device("cpu"))
    assert condition.shape == (3, 1, 4)
    assert condition.dtype == torch.float32
    assert torch.equal(condition.argmax(dim=-1).reshape(-1), torch.tensor([0, 2, 1]))


def test_latent_grid_survives_both_downsampling_ladders() -> None:
    """``spatial_shape`` divides cleanly through the autoencoder, and the latent through the UNet.

    Two ladders in series, and only the second has a safety net: ``derive_new_latent_shape`` pads a
    ragged latent before the UNet, but nothing absorbs a ragged *image* shape at the autoencoder.
    An indivisible ``spatial_shape`` surfaces as a size mismatch when the decoder tries to meet its
    skip connections — deep inside a training step, not at start-up.
    """
    config = _config(_LATENT_DIFFUSION)
    net = config["net_config"]

    ae_factor = 2 ** (len(net["stage_1"]["channels"]) - 1)
    ragged = [dim for dim in config["spatial_shape"] if dim % ae_factor]
    assert not ragged, (
        f"spatial_shape {config['spatial_shape']} must be divisible by the autoencoder's "
        f"{ae_factor}x downsampling; offending dims: {ragged}"
    )

    # What the UNet actually sees. Padding would rescue it, but needing the padding at all means the
    # two ladders were not chosen together — say so here rather than discovering it in a log.
    latent = [dim // ae_factor for dim in config["spatial_shape"]]
    dm_factor = 2 ** (len(net["diffusion_model"]["channels"]) - 1)
    ragged = [dim for dim in latent if dim % dm_factor]
    assert not ragged, (
        f"the {latent} latent must be divisible by the diffusion UNet's {dm_factor}x downsampling; "
        f"offending dims: {ragged} (derive_new_latent_shape would pad, but the ladders should match)"
    )


def test_latent_server_checkpoint_matches_job_py_constant() -> None:
    """``SERVER_CHECKPOINT`` names the same file ``job.py`` stages server-side.

    They are declared in two places on purpose — ``job.py`` needs the literal to exclude the file
    from the client bundle, before any config is read — so they have to be checked against each
    other. A drift means the persistor hunts for a file nobody staged.
    """
    declared = _config(_LATENT_DIFFUSION)["SERVER_CHECKPOINT"]
    job_py = (_LATENT_DIFFUSION / "job.py").read_text()
    staged = re.search(r'^_CHECKPOINT_NAME\s*=\s*"([^"]+)"', job_py, re.MULTILINE)
    assert staged is not None, "latent_diffusion_model/job.py declares no _CHECKPOINT_NAME"
    assert staged.group(1) == declared, (
        f"job.py stages {staged.group(1)!r} but config.json declares SERVER_CHECKPOINT="
        f"{declared!r}; the persistor would look for a file that was never staged."
    )


def test_latent_checkpoint_is_gitignored() -> None:
    """The prepared checkpoint is a local build artefact, never committed."""
    declared = _config(_LATENT_DIFFUSION)["SERVER_CHECKPOINT"]
    ignored = (_LATENT_DIFFUSION / ".gitignore").read_text().split()
    assert f"app_files/{declared}" in ignored


def test_latent_aggregate_only_regex_selects_exactly_the_diffusion_model(
    latent_state_dict_keys: list[str],
) -> None:
    """The regex keeps every trained parameter and no frozen one.

    Checked against the real network's parameter names rather than a hand-written sample, because
    both failure directions are silent: a regex matching nothing makes ``KeepOnlyVars`` drop the
    entire update (warned, not raised), and one matching the autoencoder puts its exact-zero diffs
    (~10% of this network's parameters) back into the update, dragging ``PercentilePrivacy``'s global
    percentile cutoff to zero and disabling the DP sparsification.
    """
    declared = _config(_LATENT_DIFFUSION)["AGGREGATE_ONLY_REGEX"]
    pattern = re.compile(declared)  # raises here if it does not compile

    kept = [key for key in latent_state_dict_keys if pattern.search(key)]
    dropped = [key for key in latent_state_dict_keys if not pattern.search(key)]

    assert kept, f"AGGREGATE_ONLY_REGEX {declared!r} matches no parameter of the network"
    assert dropped, f"AGGREGATE_ONLY_REGEX {declared!r} matches every parameter; nothing stays frozen"

    wrongly_kept = [key for key in kept if key.startswith(_AUTOENCODER_PREFIX)]
    assert not wrongly_kept, (
        f"AGGREGATE_ONLY_REGEX {declared!r} matches {len(wrongly_kept)} frozen autoencoder "
        f"parameter(s) (e.g. {wrongly_kept[:3]}); their zero diffs would flatten the DP cutoff."
    )
    wrongly_dropped = [key for key in dropped if key.startswith(_DIFFUSION_PREFIX)]
    assert not wrongly_dropped, (
        f"AGGREGATE_ONLY_REGEX {declared!r} misses {len(wrongly_dropped)} trained diffusion-model "
        f"parameter(s) (e.g. {wrongly_dropped[:3]}); they would never be aggregated."
    )


def test_latent_network_exposes_the_autoencoder_submodule_name(
    latent_state_dict_keys: list[str],
) -> None:
    """The latent network keys its autoencoder ``autoencoder.*``, matching the uploaded checkpoint.

    The other half of the naming contract asserted by
    :func:`test_autoencoder_network_exposes_the_autoencoder_submodule_name`.
    """
    assert any(key.startswith(_AUTOENCODER_PREFIX) for key in latent_state_dict_keys)


def test_autoencoder_network_exposes_the_autoencoder_submodule_name() -> None:
    """The autoencoder tutorial keys its autoencoder ``autoencoder.*`` too.

    This is the contract ``process_tools/extract_autoencoder.py`` filters on and the persistor then
    matches against. Rename the submodule in either tutorial and the handoff silently stops working.
    """
    keys = list(_load_app_module(_AUTOENCODER, "models").get_model().state_dict())
    assert any(key.startswith(_AUTOENCODER_PREFIX) for key in keys)
    # The discriminator is what the extractor strips; if it ever stops existing, that step is moot.
    assert any(key.startswith("discriminator.") for key in keys)


def test_latent_network_has_no_discriminator(latent_state_dict_keys: list[str]) -> None:
    """The latent job carries no discriminator — it trains no autoencoder, so nothing adversarial.

    Its absence is why the uploaded checkpoint is autoencoder-*only*.
    """
    assert not [key for key in latent_state_dict_keys if key.startswith("discriminator.")]


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_spatial_shape_matches_the_transform_resize(tutorial: Path) -> None:
    """``config.json``'s ``spatial_shape`` equals the size ``transforms.py`` actually resizes to.

    The two are read by different halves of the app — the transform chain shapes the images, while
    ``spatial_shape`` shapes the noise tensor a diffusion step samples and the latent grid the latent
    job derives. A drift between them is not a config nit: the noise and the image would disagree on
    shape, which surfaces as a broadcasting error deep inside a training step, or (worse, in the
    latent job) as a silently mis-sized latent.
    """
    transforms = _load_app_module(tutorial, "transforms")
    assert _config(tutorial)["spatial_shape"] == list(transforms.SPATIAL_SHAPE)


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_spatial_dims_agrees_with_spatial_shape(tutorial: Path) -> None:
    """``net_config.spatial_dims`` matches the dimensionality of ``spatial_shape``.

    A leftover ``spatial_dims`` from the other cohort builds a network that cannot consume the data
    the transform chain produces, so pin them to each other. This is a live hazard rather than a
    theoretical one: these tutorials have now been moved between 2-D and 3-D twice, and the two
    brain-MRI ones sit beside a 2-D X-ray one in the same directory.

    Deliberately asserts *consistency* rather than a literal, so it holds for both cohorts. An
    inconsistent pair — the actual defect — fails either way.
    """
    config = _config(tutorial)
    assert config["net_config"]["spatial_dims"] == len(config["spatial_shape"])


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_subnetwork_spatial_dims_do_not_contradict_the_top_level(tutorial: Path) -> None:
    """Any ``spatial_dims`` repeated inside a ``net_config`` sub-block equals the top-level one.

    ``models.py`` builds every network from ``net_config["spatial_dims"]``, so the copies carried
    inside ``stage_1`` (and in a 3-D port, ``diffusion_model``) steer nothing — which is exactly why
    they are dangerous. They read like the authoritative value, so a 2-D/3-D switch that edits one and
    not the other produces a config that looks coherent, builds a network of the top-level
    dimensionality, and leaves anything keyed off the sub-block disagreeing with the model it was
    meant to describe.

    ``discriminator`` is the deliberate exception and is skipped: a 2-D patch discriminator scoring
    axial slices of a 3-D reconstruction is the supported thick-slice configuration (see
    ``slice_volume_for_2d`` and "Porting this tutorial to 3-D" in the autoencoder README), so there
    that mismatch is the feature rather than the bug.
    """
    net_config = _config(tutorial)["net_config"]
    top_level = net_config["spatial_dims"]

    contradictions = {
        name: block["spatial_dims"]
        for name, block in net_config.items()
        if name != "discriminator" and isinstance(block, dict) and "spatial_dims" in block
        if block["spatial_dims"] != top_level
    }
    assert not contradictions, (
        f"net_config.spatial_dims is {top_level} but {contradictions} disagree; "
        "the networks are built from the top-level value, so these copies would silently mislead."
    )


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_debug_samples_ship_disabled(tutorial: Path) -> None:
    """``SAVE_DEBUG_SAMPLES`` is declared and **false** in every shipped config.

    The flag writes patient-derived images to the client's own workspace. That is the right place for
    them, but it is still an accumulation of pixel data with no retention policy, so a job must opt in
    deliberately — shipping a tutorial with it already on would turn every real run into one.
    """
    config = _config(tutorial)
    assert config["SAVE_DEBUG_SAMPLES"] is False
    assert isinstance(config["DEBUG_SAMPLES_MAX"], int)
    assert config["DEBUG_SAMPLES_MAX"] > 0


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_debug_sample_output_is_gitignored(tutorial: Path) -> None:
    """The debug output directory is gitignored, so a local run cannot stage clinical images."""
    ignored = (tutorial / ".gitignore").read_text().split()
    plot_utils = _load_app_module(tutorial, "plot_utils")
    assert f"app_files/{plot_utils.DEBUG_DIR_NAME}/" in ignored


def test_plot_utils_helper_is_identical_across_tutorials() -> None:
    """All three copies of ``plot_utils.py`` are byte-identical.

    Duplicated for the same reason ``transforms.py`` is: ``app_files/`` is the per-model upload unit,
    so a shared module would not survive the bundle. Pin them to each other so a fix to one (or a
    change to what the images are normalised against) cannot silently apply to only one tutorial.
    """
    contents = {t.name: (t / "app_files" / "plot_utils.py").read_bytes() for t in _ALL_TUTORIALS}
    assert len(set(contents.values())) == 1, f"plot_utils.py differs between {sorted(contents)}"


def test_debug_samples_writes_nothing_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag off (or absent) ``save_grid`` writes no file and creates no directory.

    The off path is the one that runs at a trust, so it is the one worth pinning: a regression that
    made saving unconditional would deposit reconstructions on every client of every real run.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: tmp_path)
    batch = torch.zeros(2, 1, 8, 8)

    assert plot_utils.save_grid({"input": batch}, "recon", {"SAVE_DEBUG_SAMPLES": False}) is None
    assert plot_utils.save_grid({"input": batch}, "recon", {}) is None
    assert not list(tmp_path.iterdir())


def test_debug_samples_writes_one_png_per_call_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled, ``save_grid`` writes a single PNG tiling the batches it was given."""
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: tmp_path)
    config = {"SAVE_DEBUG_SAMPLES": True, "DEBUG_SAMPLES_MAX": 2}

    written = plot_utils.save_grid(
        {"input": torch.rand(4, 1, 8, 8), "reconstruction": torch.rand(4, 1, 8, 8)},
        "recon",
        config,
        site_name="site-1",
        step=3,
    )

    assert written is not None
    assert written.exists()
    assert written.suffix == ".png"
    # Row per entry, column per image, capped by DEBUG_SAMPLES_MAX.
    assert [p.name for p in tmp_path.iterdir()] == ["recon_input_reconstruction_site-1_step0003.png"]


def test_debug_samples_reduces_volumes_to_a_mid_slice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5-D batch is tiled as its axial mid-slice rather than failing.

    This is the 3-D port path (see "Porting this tutorial to 3-D" in the autoencoder README):
    ``make_grid`` cannot tile a volume, so a port that switched ``spatial_dims`` to 3 would otherwise
    lose its debug images to a swallowed exception at exactly the moment they are most useful.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: tmp_path)

    volume = torch.zeros(1, 1, 8, 8, 5)
    volume[..., 2] = 1.0  # only the mid-slice is bright
    written = plot_utils.save_grid({"input": volume}, "recon", {"SAVE_DEBUG_SAMPLES": True})

    assert written is not None
    assert written.exists()
    assert plot_utils._to_2d(volume).shape == (1, 1, 8, 8)
    assert plot_utils._to_2d(volume).max() == 1.0


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_debug_plot_interval_ships_configured(tutorial: Path) -> None:
    """Every tutorial declares ``DEBUG_PLOT_EVERY``, and it is a positive integer."""
    interval = _config(tutorial)["DEBUG_PLOT_EVERY"]
    assert isinstance(interval, int)
    assert not isinstance(interval, bool)
    assert interval > 0


def test_triplanar_is_gated_on_the_debug_flag_and_the_interval() -> None:
    """``due_for_plot`` fires on step 0 and every Nth after, and never while debugging is off.

    Step 0 matters: it is the only look at an untrained model, and a run that dies early would
    otherwise leave no figure at all.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    on = {"SAVE_DEBUG_SAMPLES": True, "DEBUG_PLOT_EVERY": 100}

    assert [plot_utils.due_for_plot(i, on) for i in (0, 1, 99, 100, 101, 200)] == [
        True, False, False, True, False, True,
    ]
    off = {"SAVE_DEBUG_SAMPLES": False, "DEBUG_PLOT_EVERY": 1}
    assert not any(plot_utils.due_for_plot(i, off) for i in range(3))
    # A non-positive interval turns the figures off without disturbing the grids.
    never = {"SAVE_DEBUG_SAMPLES": True, "DEBUG_PLOT_EVERY": 0}
    assert not any(plot_utils.due_for_plot(i, never) for i in range(3))
    # A junk value falls back rather than raising into a training step.
    assert plot_utils.plot_every({"DEBUG_PLOT_EVERY": "often"}) == plot_utils.DEFAULT_PLOT_EVERY


def test_triplanar_gate_and_file_name_count_the_same_step() -> None:
    """The interval is measured against the *same* step the figure is named after.

    ``due_for_plot`` is a plain modulo, so it is only "every N steps" if it is handed a step that
    counts across epochs. Handing it the within-epoch batch index instead degrades silently into
    "once per epoch" whenever an epoch is shorter than the interval — 80 batches against an interval
    of 100 fires only at index 0 — while the file name still carries the global step, so the output
    looks like an every-80 cadence that matches no configured value. Nothing raises and no log says
    so; the only symptom is the wrong number of figures.
    """
    tree = ast.parse((_AUTOENCODER / "app_files" / "trainer.py").read_text())

    gates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "due_for_plot"
    ]
    assert len(gates) == 1, "expected exactly one due_for_plot gate in the training loop"

    gate = gates[0]
    gated_on = gate.test.args[0]
    assert isinstance(gated_on, ast.Name), "gate on a named step, not an inline expression"

    steps = [
        keyword.value
        for node in ast.walk(gate)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "save_triplanar"
        for keyword in node.keywords
        if keyword.arg == "step"
    ]
    assert len(steps) == 1, "expected one save_triplanar(step=...) under the gate"
    assert isinstance(steps[0], ast.Name)
    assert steps[0].id == gated_on.id, (
        f"gated on {gated_on.id!r} but named the file after {steps[0].id!r}; "
        "both must be the global step"
    )


def test_triplanar_writes_three_views_of_each_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One PNG per call, and the three rows are genuinely different cuts.

    Built from a volume that is bright in only one octant, so a figure that took the same slice three
    times — the mistake that makes this plot useless while looking fine — cannot pass.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: tmp_path)

    volume = torch.zeros(2, 1, 12, 14, 16)
    volume[..., :6, :7, :8] = 1.0
    written = plot_utils.save_triplanar(
        {"input": volume, "reconstruction": volume * 0.5}, "triplanar", {"SAVE_DEBUG_SAMPLES": True}, step=7
    )
    assert written is not None
    assert written.exists()
    assert list(tmp_path.glob("*.png")) == [written]
    assert "step0007" in written.name

    single = volume[0, 0]
    cuts = [plot_utils._central_slice(single, axis, flip) for _, axis, flip in plot_utils._PLANES]
    assert [c.shape for c in cuts] == [(16, 14), (16, 12), (14, 12)], "each view fixes a different axis"


def test_only_the_sagittal_view_is_mirrored() -> None:
    """Sagittal is flipped left-right; coronal and axial are not.

    After ``Orientationd(axcodes="RAS")`` the array is ``(X, Y, Z)`` with +X right, +Y anterior,
    +Z superior. The shared ``rot90`` fixes the vertical axis for all three views but not the
    horizontal one: coronal and axial both put **X** across the page, so they come out with the
    patient's left on the viewer's left, while sagittal puts **Y** across the page and comes out with
    the head facing backwards. Only sagittal is corrected, and this pins that asymmetry — a flip
    applied to all three, or to none, both look plausible in code and wrong on screen.

    Each view gets its own probe volume with a single bright voxel of known anatomy, placed inside
    that view's central slice, so the assertions are about where anatomy lands on the page rather
    than about which numpy calls were made.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    by_view = {name: (axis, flip) for name, axis, flip in plot_utils._PLANES}
    assert [by_view[v][1] for v in ("sagittal", "coronal", "axial")] == [True, False, False]

    size, mid, far = 9, 4, 8

    def corner_of(view: str, marker: tuple[int, int, int]) -> tuple[int, int]:
        volume = torch.zeros(size, size, size)
        volume[marker] = 1.0
        rendered = plot_utils._central_slice(volume, *by_view[view])
        row, column = (int(v) for v in np.argwhere(rendered == 1.0)[0])
        return row < size // 2, column < size // 2  # (in the top half, in the left half)

    # Sagittal: anterior + superior. Superior belongs at the top, anterior at the LEFT.
    assert corner_of("sagittal", (mid, far, far)) == (True, True)

    # Coronal: patient-right + superior. Superior at the top, patient-right on the viewer's RIGHT.
    assert corner_of("coronal", (far, mid, far)) == (True, False)

    # Axial: patient-right + anterior. Anterior at the top, patient-right on the viewer's RIGHT.
    assert corner_of("axial", (far, far, mid)) == (True, False)


def test_triplanar_declines_non_volumetric_input_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2-D X-ray tutorial shares this module; a tri-planar cut of a radiograph is meaningless."""
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: tmp_path)

    assert plot_utils.save_triplanar({"input": torch.zeros(1, 1, 8, 8)}, "t", {"SAVE_DEBUG_SAMPLES": True}) is None
    disabled = {"SAVE_DEBUG_SAMPLES": False}
    assert plot_utils.save_triplanar({"input": torch.zeros(1, 1, 4, 4, 4)}, "t", disabled) is None
    assert list(tmp_path.glob("*.png")) == []


def test_foreground_ssim_masks_the_background_and_reads_real_ssim() -> None:
    """SSIM is scored over foreground only, and is SSIM rather than contrast sensitivity.

    Both halves of this have bitten: ``compute_ssim_and_cs`` returns ``(ssim, cs)`` and the tutorial
    long reported the second; and on a skull-stripped cohort ~85% of every volume is exactly 0, where
    any reconstruction agrees, so an unmasked mean mostly measures how much air the scan contains.

    The fixture is a volume with a bright foreground block in a zero background. A degraded copy must
    score *lower* under the masked metric than under the whole-volume mean — if it does not, the mask
    is not actually excluding anything.
    """
    trainer = _load_app_module(_AUTOENCODER, "trainer")
    from monai.metrics import compute_ssim_and_cs

    torch.manual_seed(0)
    images = torch.zeros(1, 1, 32, 32, 32)
    images[..., 8:24, 8:24, 8:24] = torch.rand(1, 1, 16, 16, 16)
    degraded = torch.nn.functional.avg_pool3d(images, 5, stride=1, padding=2)

    assert trainer.foreground_ssim(images, images) == pytest.approx(1.0, abs=1e-4)

    whole = compute_ssim_and_cs(
        degraded, images, spatial_dims=3, data_range=1, kernel_size=[11] * 3, kernel_sigma=[1.5] * 3
    )[0].mean().item()
    masked = trainer.foreground_ssim(degraded, images)
    assert masked < whole, f"masking changed nothing (masked={masked}, whole-volume={whole})"

    # An all-background batch has nothing to score; NaN, not a spurious 1.0.
    import math

    assert math.isnan(trainer.foreground_ssim(torch.zeros_like(images), torch.zeros_like(images)))


def test_foreground_ssim_mask_lines_up_with_the_ssim_map() -> None:
    """The mask crop matches the valid-convolution shrink, so mask and map are the same grid.

    ``compute_ssim_and_cs`` convolves without padding, so its map is ``kernel_size - 1`` smaller per
    axis. Cropping the mask by half that on each side is what makes each map voxel correspond to the
    window centred on it; an off-by-one here would silently score the wrong voxels.
    """
    trainer = _load_app_module(_AUTOENCODER, "trainer")
    from monai.metrics import compute_ssim_and_cs

    images = torch.rand(1, 1, 24, 26, 28)
    ssim_map = compute_ssim_and_cs(
        images, images, spatial_dims=3, data_range=1,
        kernel_size=[trainer.SSIM_KERNEL_SIZE] * 3, kernel_sigma=[trainer.SSIM_KERNEL_SIGMA] * 3,
    )[0]

    trim = (trainer.SSIM_KERNEL_SIZE - 1) // 2
    centres = (slice(None), slice(None)) + tuple(slice(trim, size - trim) for size in images.shape[2:])
    assert images[centres].shape == ssim_map.shape


def test_debug_dir_defaults_inside_the_job_and_is_relocatable_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default output stays in the app's own directory; ``DEBUG_SAMPLES_DIR`` moves it.

    The default is the production guarantee — a client writes patient-derived images only inside the
    job workspace the trust agreed to run, never to an operator-chosen path. So the override must be
    an *environment* variable and not a ``config.json`` key: ``config.json`` is uploaded with the app
    and read on the trust, where this variable is never set.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")

    monkeypatch.delenv(plot_utils.DEBUG_DIR_ENV, raising=False)
    assert plot_utils.debug_dir() == _AUTOENCODER / "app_files" / plot_utils.DEBUG_DIR_NAME

    monkeypatch.setenv(plot_utils.DEBUG_DIR_ENV, str(tmp_path / "elsewhere"))
    assert plot_utils.debug_dir() == tmp_path / "elsewhere"
    assert (tmp_path / "elsewhere").is_dir(), "the override directory is created, not just returned"

    # An empty or whitespace-only value is not a path — fall back rather than writing to the cwd.
    monkeypatch.setenv(plot_utils.DEBUG_DIR_ENV, "   ")
    assert plot_utils.debug_dir() == _AUTOENCODER / "app_files" / plot_utils.DEBUG_DIR_NAME


@pytest.mark.parametrize("tutorial", _ALL_TUTORIALS, ids=lambda p: p.name)
def test_sim_target_points_debug_output_at_the_gitignored_data_root(tutorial: Path) -> None:
    """Each ``sim`` target exports ``DEBUG_SAMPLES_DIR`` under ``fl-tutorials/data/``.

    Two things this pins. The variable has to be exported *in the recipe* (like ``DEV_IMAGES_DIR``),
    because make does not pass an ordinary Makefile variable into a recipe's environment — a
    definition without the export line would silently do nothing. And the path has to sit under
    ``fl-tutorials/data/``, the one tree already gitignored, or a local run starts leaving
    patient-derived PNGs in `git status`.
    """
    makefile = (tutorial / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^DEBUG_SAMPLES_DIR \?= \$\(FL_TUTORIALS\)/data/debug_samples/", makefile, re.M), (
        f"{tutorial.name}/Makefile does not default DEBUG_SAMPLES_DIR under fl-tutorials/data/"
    )
    assert 'DEBUG_SAMPLES_DIR="$(abspath $(DEBUG_SAMPLES_DIR))"' in makefile, (
        f"{tutorial.name}/Makefile defines DEBUG_SAMPLES_DIR but never exports it into the sim recipe"
    )


def test_debug_samples_never_raises_into_the_training_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure to write is logged and swallowed, never propagated.

    Debug output is an instrument, not part of the job: an unwritable workspace must not fail a
    federated round that has already done its training.
    """
    plot_utils = _load_app_module(_AUTOENCODER, "plot_utils")
    monkeypatch.setattr(plot_utils, "debug_dir", lambda: (_ for _ in ()).throw(OSError("read-only")))

    assert plot_utils.save_grid({"input": torch.rand(1, 1, 8, 8)}, "recon", {"SAVE_DEBUG_SAMPLES": True}) is None
