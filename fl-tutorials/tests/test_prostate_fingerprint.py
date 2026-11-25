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
"""The federated nnU-Net fingerprint (``3d_prostate_nnunet_fingerprint``).

The job exists so the planner gets, from histograms pooled by FedAvg, the numbers it would have read
from every site's cases in one place. These tests pin that equivalence on invented cohorts, the
per-case crop, what a site refuses to release, and the Flower transport around it. The planner
itself needs nnunetv2 and is not run here; ``test_picai_fold0_*`` pins its inputs on the real
PI-CAI geometry instead.
"""

from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

import nibabel as nib
import numpy as np
import pytest
from flip.flower import identity
from flwr.app import ConfigRecord, RecordDict
from tutorial_apps import TUTORIALS_ROOT, app_package, flower_client_app

TUTORIAL = TUTORIALS_ROOT / "flower" / "image_segmentation" / "3d_prostate_nnunet_fingerprint"


@pytest.fixture(scope="module")
def fp():
    with app_package(TUTORIAL):
        yield importlib.import_module("app.fingerprint")


@pytest.fixture(scope="module")
def config():
    with app_package(TUTORIAL):
        return importlib.import_module("app.models").load_config()


def _case(fp, spacing, shape, relative_size=1.0, intensities=(100.0,)):
    return fp.CaseFingerprint(tuple(spacing), tuple(shape), relative_size, [np.asarray(intensities)])


def _fedavg(sites: list[dict[str, np.ndarray]], weights: list[int]) -> dict[str, np.ndarray]:
    """The template strategy's aggregation: a size-weighted mean of float32 arrays."""
    total = sum(weights)
    return {
        key: sum(site[key] * np.float32(weight / total) for site, weight in zip(sites, weights)) for key in sites[0]
    }


def _pool(fp, config, cases_by_site: dict[str, list]) -> dict:
    sites = [fp.site_statistics(cases, config, name) for name, cases in cases_by_site.items()]
    return fp.pooled_fingerprint(_fedavg(sites, [len(cases) for cases in cases_by_site.values()]), config)


def _planner_inputs(spacings, shapes, extents=None) -> dict[str, np.ndarray]:
    """Every number ExperimentPlanner reads from the per-case lists (see its plan_experiment)."""
    spacings, shapes = np.asarray(spacings, dtype=float), np.asarray(shapes, dtype=float)
    target = np.percentile(spacings, 50, axis=0)
    extents = spacings * shapes if extents is None else np.asarray(extents)
    return {
        "median_spacing": target,
        "tenth_percentile_spacing": np.percentile(spacings, 10, axis=0),
        "median_shape": np.percentile(shapes, 50, axis=0),
        "median_resampled_shape": np.median(np.round(extents / target), axis=0),
    }


def test_analyze_case_crops_to_the_nonzero_box_and_keeps_the_labelled_intensities(fp):
    image = np.zeros((1, 6, 10, 10), dtype=np.float32)
    image[0, 1:5, 2:9, 3:8] = 50
    image[0, 2, 4, 4] = 0  # a hole inside the body is filled, not cropped around
    image[0, 2:4, 4:6, 4:6] += 7
    foreground = np.zeros((6, 10, 10), dtype=bool)
    foreground[2:4, 4:6, 4:6] = True

    case = fp.analyze_case(image, foreground, (3.0, 0.5, 0.5))

    assert case.shape_after_crop == (4, 7, 5)
    assert case.relative_size_after_cropping == pytest.approx(4 * 7 * 5 / 600)
    assert sorted(case.foreground_intensities[0].tolist()) == [7.0] + [57.0] * 7
    assert case.spacing == (3.0, 0.5, 0.5)


def test_pooled_histograms_give_the_planner_what_the_pooled_cases_would(fp, config):
    """Three unequal sites, one with finer and thicker scans: every planner input survives the pooling."""
    rng = np.random.default_rng(0)

    def site(n, in_plane, slice_spacings):
        return [
            _case(
                fp,
                (rng.choice(slice_spacings), in_plane, in_plane),
                (rng.integers(15, 40), rng.integers(300, 1025), rng.integers(300, 1025)),
                relative_size=rng.uniform(0.6, 1.0),
                intensities=rng.integers(0, 900, size=500).astype(float),
            )
            for _ in range(n)
        ]

    cases_by_site = {
        "Trust_1": site(76, 0.5, [3.0, 3.6]),
        "Trust_2": site(69, 0.3438, [3.0, 3.3, 4.0]),
        "Trust_3": site(155, 0.5, [3.0]),
    }
    cases = [case for site_cases in cases_by_site.values() for case in site_cases]

    pooled = _pool(fp, config, cases_by_site)

    assert len(pooled["spacings"]) == len(cases) == 300
    expected = _planner_inputs([c.spacing for c in cases], [c.shape_after_crop for c in cases])
    actual = _planner_inputs(pooled["spacings"], pooled["shapes_after_crop"], pooled["physical_extents"])
    np.testing.assert_allclose(actual["median_spacing"], expected["median_spacing"], atol=config["SPACING_BIN_MM"])
    np.testing.assert_allclose(
        actual["tenth_percentile_spacing"], expected["tenth_percentile_spacing"], atol=config["SPACING_BIN_MM"]
    )
    np.testing.assert_array_equal(actual["median_shape"], expected["median_shape"])
    # Resampling to a 0.5 mm target puts Trust_2's 0.3438 mm cases off the grid: within one voxel.
    np.testing.assert_allclose(actual["median_resampled_shape"], expected["median_resampled_shape"], atol=1)
    assert pooled["median_relative_size_after_cropping"] == pytest.approx(
        np.median([c.relative_size_after_cropping for c in cases]), abs=1e-3
    )
    # nnU-Net pools the same number of foreground voxels per case; so does a mean of per-case histograms.
    voxels = np.concatenate([c.foreground_intensities[0] for c in cases])
    intensity = pooled["foreground_intensity_properties_per_channel"]["0"]
    assert intensity["mean"] == pytest.approx(voxels.mean(), abs=0.01)
    assert intensity["std"] == pytest.approx(voxels.std(), abs=0.01)
    assert intensity["median"] == pytest.approx(np.median(voxels), abs=1)
    assert (intensity["min"], intensity["max"]) == (voxels.min(), voxels.max())


def test_picai_fold0_geometry_gives_the_committed_plans_inputs(fp, config):
    """PI-CAI fold 0 at ZGT / PCNN / RUMC, as counts of cases per (spacing, cropped shape).

    The table is the public dataset's geometry (README, "Agreement with the pooled plan"). Pooled
    through the histograms it must give the planner what the committed plan was built from: target
    spacing [3.0, 0.5, 0.5], median shape [21, 383, 383] and 300 cases.
    """
    geometry = np.loadtxt(TUTORIAL / "docs" / "picai_fold0_geometry.csv", delimiter=",", skiprows=1, dtype=str)
    cases_by_site: dict[str, list] = {}
    for site, *numbers in geometry:
        d_mm, h_mm, w_mm, d, h, w, count = (float(n) for n in numbers)
        cases_by_site.setdefault(site, []).extend([_case(fp, (d_mm, h_mm, w_mm), (d, h, w))] * int(count))
    cases = [case for site_cases in cases_by_site.values() for case in site_cases]
    assert {site: len(c) for site, c in cases_by_site.items()} == {"ZGT": 76, "PCNN": 69, "RUMC": 155}

    renamed = {f"Trust_{i}": site_cases for i, site_cases in enumerate(cases_by_site.values(), start=1)}
    pooled = _pool(fp, config, renamed)

    expected = _planner_inputs([c.spacing for c in cases], [c.shape_after_crop for c in cases])
    actual = _planner_inputs(pooled["spacings"], pooled["shapes_after_crop"], pooled["physical_extents"])
    for key, value in expected.items():
        np.testing.assert_array_equal(actual[key], value, err_msg=key)
    np.testing.assert_array_equal(actual["median_spacing"], [3.0, 0.5, 0.5])
    np.testing.assert_array_equal(actual["median_shape"], [21, 383, 383])


def test_a_site_below_the_floor_refuses_without_saying_how_many(fp, config):
    with pytest.raises(ValueError, match="too few usable cases") as refusal:
        fp.assert_site_may_release(fp.HARD_MIN_SITE_CASES - 1, config, "Trust_1")
    assert not re.search(r"\d", str(refusal.value))
    # config.json can raise the floor, never lower it.
    fp.assert_site_may_release(fp.HARD_MIN_SITE_CASES, {**config, "MIN_SITE_CASES": 1})
    with pytest.raises(ValueError, match="too few usable cases"):
        fp.assert_site_may_release(fp.HARD_MIN_SITE_CASES, {**config, "MIN_SITE_CASES": 50})


def test_a_case_beyond_the_histogram_range_is_refused_not_clipped(fp, config):
    cases = [_case(fp, (3.0, 0.5, 0.5), (20, 384, 384))] * 11
    with pytest.raises(ValueError, match="outside the histogram ranges"):
        fp.site_statistics([*cases, _case(fp, (config["MAX_SPACING_MM"] + 1, 0.5, 0.5), (20, 384, 384))], config, "s")
    with pytest.raises(ValueError, match="outside the histogram ranges"):
        fp.site_statistics([*cases, _case(fp, (3.0, 0.5, 0.5), (20, config["MAX_SHAPE"] + 1, 384))], config, "s")


def test_noised_histograms_are_rejected_at_export(fp, config):
    """What clip-and-noise, or two sites sharing a site_cases slot, would leave: shares that are not counts."""
    state = fp.site_statistics([_case(fp, (3.0, 0.5, 0.5), (20, 384, 384))] * 12, config, "Trust_1")
    state["spacing"] = state["spacing"] + np.float32(0.01)
    with pytest.raises(ValueError, match="do not add up"):
        fp.pooled_fingerprint(state, config)


# --- the Flower transport ----------------------------------------------------------------------


@pytest.fixture
def client_app(monkeypatch, tmp_path):
    """The ClientApp over 12 phantom studies: 16 x 12 x 5 voxels at (0.5, 0.6, 3.0) mm, gland labelled."""
    datalist = []
    for index in range(12):
        image = np.full((16, 12, 5), 100 + index, dtype=np.float32)
        gland = np.zeros((16, 12, 5), dtype=np.uint8)
        gland[4:8, 4:8, 1:3] = 1
        affine = np.diag([0.5, 0.6, 3.0, 1.0])
        paths = {key: tmp_path / f"{key}_{index}.nii.gz" for key in ("image", "whole_gland", "pz_tz")}
        nib.save(nib.Nifti1Image(image, affine), paths["image"])
        nib.save(nib.Nifti1Image(gland, affine), paths["whole_gland"])
        nib.save(nib.Nifti1Image(gland, affine), paths["pz_tz"])
        datalist.append({**paths, "accession_id": f"acc{index}"})

    class Trust:
        pulls = 0

        def fetch_dataframe(self):
            self.dataframe = SimpleNamespace()

        def get_case_list(self, modality, val_split, test_split):
            assert (modality, val_split, test_split) == ("t2w", 0.0, 0.0)
            Trust.pulls += 1
            return datalist[: Trust.cases], []

    Trust.cases = 12
    with flower_client_app(TUTORIAL) as module:
        monkeypatch.setattr(module, "Message", lambda content, reply_to: SimpleNamespace(content=content))
        monkeypatch.setattr(module, "FLIP_BASE", Trust)
        monkeypatch.setattr(module, "partition_cohort", lambda dataframe, context: dataframe)
        monkeypatch.setattr(identity, "FlipConstants", SimpleNamespace(LOCAL_DEV=True))
        monkeypatch.delenv("SUPERNODE_NAME", raising=False)
        module.Trust = Trust
        yield module


def _context(**run_config) -> SimpleNamespace:
    return SimpleNamespace(
        run_config={"dp-enabled": False, **run_config},
        node_config={"partition-id": "0", "num-partitions": "1"},
        state=RecordDict(),
    )


def _message(client_app) -> SimpleNamespace:
    from flwr.app import ArrayRecord

    return SimpleNamespace(
        content={
            "arrays": ArrayRecord(client_app.get_model().state_dict()),
            "config": ConfigRecord({"server-round": 1}),
        }
    )


def test_a_run_config_without_dp_enabled_is_refused_as_the_mod_reads_it(client_app):
    without_key = _context()
    del without_key.run_config["dp-enabled"]
    for context in (without_key, _context(**{"dp-enabled": True})):
        with pytest.raises(ValueError, match="dp-enabled is true"):
            client_app.train(_message(client_app), context)
    assert client_app.Trust.pulls == 0


def test_the_client_sends_its_histograms_in_nnunet_axis_order_weighted_by_cases(client_app, fp, config):
    context = _context()
    reply = client_app.train(_message(client_app), context)

    assert reply.content["metrics"]["num-examples"] == 12  # the size weight FedAvg pools by
    state = {key: tensor.numpy() for key, tensor in reply.content["arrays"].to_torch_state_dict().items()}
    pooled = fp.pooled_fingerprint(state, config)
    np.testing.assert_allclose(np.median(pooled["spacings"], axis=0), [3.0, 0.5, 0.6])  # (d, h, w) from RAS (h, w, d)
    np.testing.assert_array_equal(np.median(pooled["shapes_after_crop"], axis=0), [5, 16, 12])
    intensity = pooled["foreground_intensity_properties_per_channel"]["0"]
    assert (intensity["min"], intensity["max"], intensity["mean"]) == (100, 111, 105.5)

    evaluated = client_app.evaluate(_message(client_app), context)
    assert evaluated.content["metrics"]["num-examples"] == 12
    assert "arrays" not in evaluated.content
    assert client_app.Trust.pulls == 1  # one pull of the cohort per run


def test_the_client_refuses_below_the_case_floor(client_app):
    client_app.Trust.cases = 9
    with pytest.raises(ValueError, match="too few usable cases"):
        client_app.train(_message(client_app), _context())
