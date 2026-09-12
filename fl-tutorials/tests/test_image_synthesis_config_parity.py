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

These are static checks over the shipped ``config.json`` / ``job.py`` files, plus one that
instantiates the latent network to compare the regex against real parameter names. Nothing trains.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import torch

_SYNTHESIS_ROOT = Path(__file__).resolve().parents[1] / "nvflare" / "image_synthesis"

_AUTOENCODER = _SYNTHESIS_ROOT / "autoencoder"
_DIFFUSION = _SYNTHESIS_ROOT / "diffusion_model"
_LATENT_DIFFUSION = _SYNTHESIS_ROOT / "latent_diffusion_model"

_ALL_TUTORIALS = (_AUTOENCODER, _DIFFUSION, _LATENT_DIFFUSION)

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
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # Never leave a half-initialised module reachable from the cache.
        del sys.modules[module_name]
        raise
    return module


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

    These tutorials were ported from a 3-D CT original to 2-D chest X-rays. A leftover
    ``spatial_dims: 3`` beside a 2-D ``spatial_shape`` builds a network that cannot consume the data
    the transform chain produces, so pin them to each other.

    Deliberately asserts *consistency* rather than ``== 2``: the autoencoder keeps working 3-D helpers
    for anyone porting these tutorials back to volumes (see "Porting this tutorial to 3-D" in its
    README), and a test that hard-codes 2 would fail such a port for no good reason. An inconsistent
    pair — which is the actual defect this catches — still fails either way.
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
    debug_samples = _load_app_module(tutorial, "debug_samples")
    assert f"app_files/{debug_samples.DEBUG_DIR_NAME}/" in ignored


def test_debug_samples_helper_is_identical_across_tutorials() -> None:
    """All three copies of ``debug_samples.py`` are byte-identical.

    Duplicated for the same reason ``transforms.py`` is: ``app_files/`` is the per-model upload unit,
    so a shared module would not survive the bundle. Pin them to each other so a fix to one (or a
    change to what the images are normalised against) cannot silently apply to only one tutorial.
    """
    contents = {t.name: (t / "app_files" / "debug_samples.py").read_bytes() for t in _ALL_TUTORIALS}
    assert len(set(contents.values())) == 1, f"debug_samples.py differs between {sorted(contents)}"


def test_debug_samples_writes_nothing_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag off (or absent) ``save_grid`` writes no file and creates no directory.

    The off path is the one that runs at a trust, so it is the one worth pinning: a regression that
    made saving unconditional would deposit reconstructions on every client of every real run.
    """
    debug_samples = _load_app_module(_AUTOENCODER, "debug_samples")
    monkeypatch.setattr(debug_samples, "debug_dir", lambda: tmp_path)
    batch = torch.zeros(2, 1, 8, 8)

    assert debug_samples.save_grid({"input": batch}, "recon", {"SAVE_DEBUG_SAMPLES": False}) is None
    assert debug_samples.save_grid({"input": batch}, "recon", {}) is None
    assert not list(tmp_path.iterdir())


def test_debug_samples_writes_one_png_per_call_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled, ``save_grid`` writes a single PNG tiling the batches it was given."""
    debug_samples = _load_app_module(_AUTOENCODER, "debug_samples")
    monkeypatch.setattr(debug_samples, "debug_dir", lambda: tmp_path)
    config = {"SAVE_DEBUG_SAMPLES": True, "DEBUG_SAMPLES_MAX": 2}

    written = debug_samples.save_grid(
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
    debug_samples = _load_app_module(_AUTOENCODER, "debug_samples")
    monkeypatch.setattr(debug_samples, "debug_dir", lambda: tmp_path)

    volume = torch.zeros(1, 1, 8, 8, 5)
    volume[..., 2] = 1.0  # only the mid-slice is bright
    written = debug_samples.save_grid({"input": volume}, "recon", {"SAVE_DEBUG_SAMPLES": True})

    assert written is not None
    assert written.exists()
    assert debug_samples._to_2d(volume).shape == (1, 1, 8, 8)
    assert debug_samples._to_2d(volume).max() == 1.0


def test_debug_samples_never_raises_into_the_training_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure to write is logged and swallowed, never propagated.

    Debug output is an instrument, not part of the job: an unwritable workspace must not fail a
    federated round that has already done its training.
    """
    debug_samples = _load_app_module(_AUTOENCODER, "debug_samples")
    monkeypatch.setattr(debug_samples, "debug_dir", lambda: (_ for _ in ()).throw(OSError("read-only")))

    assert debug_samples.save_grid({"input": torch.rand(1, 1, 8, 8)}, "recon", {"SAVE_DEBUG_SAMPLES": True}) is None
