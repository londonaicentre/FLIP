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

"""The biomarker causal-discovery tutorial's shared federation module (``federated_fci.py``).

The module is byte-identical on both backends (``scripts/check_tutorial_sync.sh``), so one suite
over the NVFLARE copy covers both. The tests emulate FedAvg exactly the way the platform does
it — a weighted mean of what each site returns — and check the properties the tutorial's
README claims:

* pooled statistics reproduce the centralised correlation matrix and Fisher-z p-values exactly;
* the site-count slot trick recovers the pooled sample size and every site's size;
* the wire format survives the NVFLARE percentile-privacy filter unchanged;
* both federation modes recover the generating graph of a small structural causal model, and
  the pooled mode's PAG equals causal-learn's own ``fci()`` on the pooled rows;
* the public contract: config shapes, encode/decode, edge-frequency bookkeeping, and the guard
  that refuses a payload the privacy filter would clip.

No GPU, no dataset download: the cohort is sampled in-process from a 7-variable SCM.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tutorial_apps import TUTORIALS_ROOT, load_module

APP_FILES = TUTORIALS_ROOT / "nvflare" / "tabular_causal_discovery" / "biomarker_causal_discovery" / "app_files"
VARIABLES = ["a_root", "b_root", "x_fluid", "y_fluid", "u_mri", "v_mri", "z_sink"]
TRUE_EDGES = {
    ("a_root", "x_fluid"),
    ("b_root", "y_fluid"),
    ("x_fluid", "y_fluid"),
    ("y_fluid", "v_mri"),
    ("a_root", "v_mri"),
    ("v_mri", "z_sink"),
    ("x_fluid", "z_sink"),
    ("b_root", "z_sink"),
    ("b_root", "u_mri"),  # gives u a second parent, so u <-> v is identifiable (b *-> u <-* v)
}
LATENT_PAIR = ("u_mri", "v_mri")


@pytest.fixture(scope="module")
def ff():
    return load_module("federated_fci", APP_FILES / "federated_fci.py")


@pytest.fixture(scope="module")
def config() -> dict:
    return {
        "FEDERATION": "pooled_statistics",
        "N_BOOTSTRAPS": 0,
        "SEED": 3,
        "ALPHA": 0.05,
        "CI_TEST": "fisherz",
        "VOTE_RATIO": 0.5,
        "STABLE_EDGE_PROB": 0.5,
        "VARIABLES": VARIABLES,
        "LOG_TRANSFORM": ["x_fluid"],
        "SCALE": {"u_mri": 1000},
        "KNOWLEDGE": {
            "EXOGENOUS": ["a_root", "b_root"],
            "SINKS": ["z_sink"],
            "NEVER_CONDITION_ON": ["z_sink"],
            "FORBIDDEN": [],
        },
    }


def sample_scm(n: int, seed: int, age_shift: float = 0.0) -> pd.DataFrame:
    """A linear-Gaussian SCM with one latent (``h``) behind ``u_mri`` and ``v_mri``."""
    rng = np.random.default_rng(seed)
    a = rng.normal(age_shift, 1, n)
    b = rng.binomial(1, 0.5, n).astype(float)
    h = rng.normal(0, 1, n)
    x = np.exp(0.8 * a + rng.normal(0, 0.8, n))  # positive: it is LOG_TRANSFORMed
    y = 0.5 * (b - 0.5) + 0.6 * np.log(x) + rng.normal(0, 0.7, n)
    u = 1000 * (0.8 * h + 0.6 * (b - 0.5) + rng.normal(0, 0.6, n)) + 5000  # large units: it is SCALEd
    v = 0.7 * h - 0.6 * y - 0.5 * a + rng.normal(0, 0.6, n)
    z = -0.6 * v + 0.4 * np.log(x) + 1.0 * (b - 0.5) + rng.normal(0, 0.6, n)
    return pd.DataFrame(
        {
            "person_id": np.arange(1, n + 1),
            "a_root": a,
            "b_root": b,
            "x_fluid": x,
            "y_fluid": y,
            "u_mri": u,
            "v_mri": v,
            "z_sink": z,
        }
    )


def fedavg(
    ff, config: dict, sites: dict[str, np.ndarray], total_rounds: int, stop_after: int | None = None
) -> dict[str, np.ndarray]:
    """The platform's aggregation, emulated: a weight-weighted mean of every site's encoded target.

    ``stop_after`` plays the hub stopping a ``total_rounds`` job early, after that many rounds.
    """
    encoded = ff.encode(ff.empty_state(config))
    for round_index in range(total_rounds if stop_after is None else stop_after):
        targets, weights = [], []
        for site_name, matrix in sites.items():
            target, weight, _ = ff.client_round(
                ff.decode(encoded), round_index, total_rounds, matrix, site_name, config, local_dev=True
            )
            targets.append(ff.encode(target))
            weights.append(float(weight))
        total = sum(weights)
        encoded = {
            key: (sum(w * t[key].astype(np.float64) for w, t in zip(weights, targets, strict=True)) / total).astype(
                np.float32
            )
            for key in ff.STATE_KEYS
        }
    return ff.decode(encoded)


def percentile_filter(ff, encoded: dict[str, np.ndarray], weight: int) -> dict[str, np.ndarray]:
    """NVFLARE's PercentilePrivacy maths (stock percentile 10, gamma 0.01) on one site's update."""
    delta = {k: v.astype(np.float64) / weight for k, v in encoded.items()}
    cutoff = np.percentile(np.concatenate([np.abs(d.ravel()) for d in delta.values()]), 10)
    out = {}
    for key, d in delta.items():
        d = d.copy()
        d[(d > -cutoff) & (d < cutoff)] = 0.0
        out[key] = (np.clip(d, -ff.PERCENTILE_CLIP, ff.PERCENTILE_CLIP) * weight).astype(np.float32)
    return out


def stable_edges(ff, pags: np.ndarray) -> dict[tuple[str, str], str]:
    return {
        (r["source"], r["target"]): r["edge"] for r in ff.edge_frequencies(pags, VARIABLES) if r["probability"] >= 0.5
    }


# --- exactness of the pooled statistics ---------------------------------------------------


def test_pooled_statistics_equal_the_centralised_correlation(ff, config):
    frame = sample_scm(900, 11)
    full = ff.prepare_matrix(frame, config)
    parts = {f"site-{i + 1}": ff.prepare_matrix(frame[frame.person_id % 3 == i], config) for i in range(3)}
    weighted = [(len(m), *ff.site_statistics(m)) for m in parts.values()]
    total = sum(n for n, _, _ in weighted)
    mean = sum(n * mu for n, mu, _ in weighted) / total
    moment = sum(n * mo for n, _, mo in weighted) / total
    pooled = ff.pooled_correlation(mean, moment)
    assert np.allclose(pooled, np.corrcoef(full.T), atol=1e-10)


def test_bootstrap_correlations_are_shared_centred_and_standardised(ff, config):
    """The parametric bootstrap: same stream at every site, spread around the pooled correlation."""
    full = ff.prepare_matrix(sample_scm(600, 13), config)
    mean, moment = ff.site_statistics(full)
    boot = {**config, "N_BOOTSTRAPS": 400}
    draws = ff.bootstrap_correlations(mean, moment, len(full), boot)
    again = ff.bootstrap_correlations(mean, moment, len(full), boot)
    p = len(VARIABLES)
    assert draws.shape == (400, p, p)
    assert np.array_equal(draws, again)  # no site term in the stream: every site sees the same resamples
    assert np.allclose(np.einsum("bii->bi", draws), 1.0)  # correlation matrices
    pooled = ff.pooled_correlation(mean, moment)
    assert np.abs(draws.mean(axis=0) - pooled).max() < 0.03  # centred on the pooled correlation
    off = ~np.eye(p, dtype=bool)
    assert 0.005 < draws.std(axis=0)[off].mean() < 0.08  # a real spread, of the order 1/sqrt(n)
    assert ff.bootstrap_correlations(mean, moment, len(full), config).shape == (1, p, p)  # N_BOOTSTRAPS 0


def test_a_site_below_the_row_floor_refuses_without_saying_how_many(ff, config):
    small = ff.prepare_matrix(sample_scm(40, 2), config)
    state = ff.empty_state(config)
    with pytest.raises(ValueError, match="too few complete rows") as refused:
        ff.client_round(state, 0, 2, small, "site-1", {**config, "MIN_SITE_ROWS": 50})
    assert "40" not in str(refused.value)  # the count stays in the site's own log
    target, weight, _ = ff.client_round(state, 0, 2, small, "site-1", {**config, "MIN_SITE_ROWS": 40})
    assert weight == 40
    assert target["stats_moment"].shape == (len(VARIABLES), len(VARIABLES))


def test_the_row_floor_is_the_hard_floor_raised_by_config_or_the_trust(ff, config, monkeypatch):
    assert ff.site_row_floor({**config, "MIN_SITE_ROWS": 1}, env={}) == ff.HARD_MIN_SITE_ROWS  # config cannot lower it
    assert ff.site_row_floor({**config, "MIN_SITE_ROWS": 80}, env={}) == 80
    assert ff.site_row_floor(config, env={"COHORT_QUERY_THRESHOLD": "100"}) == 100  # the trust raises it
    assert ff.site_row_floor({**config, "MIN_SITE_ROWS": 80}, env={"COHORT_QUERY_THRESHOLD": "60"}) == 80
    assert ff.site_row_floor(config, env={"COHORT_QUERY_THRESHOLD": "junk"}) == ff.HARD_MIN_SITE_ROWS
    assert ff.site_row_floor(config, env={"COHORT_QUERY_THRESHOLD": " "}) == ff.HARD_MIN_SITE_ROWS


def test_a_small_cell_in_a_discrete_column_blocks_the_release(ff, config):
    matrix = ff.prepare_matrix(sample_scm(200, 3), config)
    b = VARIABLES.index("b_root")
    assert ff.small_cells(matrix, config) == []
    matrix[:, b] = 0.0
    matrix[:2, b] = 1.0  # two rows in one level: their moments would publish that pair
    assert ff.small_cells(matrix, config) == [b]
    with pytest.raises(ValueError, match="discrete variable") as refused:
        ff.client_round(ff.empty_state(config), 0, 2, matrix, "site-1", config)
    assert "2 " not in str(refused.value)
    assert "b_root" not in str(refused.value)  # neither the count nor the column reaches the hub
    assert ff.small_cells(matrix, {**config, "MIN_CELL_COUNT": 1}) == [b]  # config cannot lower the floor
    matrix[:6, b] = 1.0  # six rows: clears the default floor of five ...
    assert ff.small_cells(matrix, config) == []
    assert ff.small_cells(matrix, {**config, "MIN_CELL_COUNT": 8}) == [b]  # ... but not a raised one


def test_layer_voting_runs_on_the_simulator_only(ff, config, two_sites):
    voting = {**config, "FEDERATION": "layer_voting"}
    matrix = next(iter(two_sites.values()))
    assert ff.layer_voting_allowed(True)
    assert not ff.layer_voting_allowed(False)
    with pytest.raises(ValueError, match="experimental"):
        ff.client_round(ff.empty_state(voting), 0, 6, matrix, "site-1", voting, local_dev=False)
    with pytest.raises(ValueError, match="experimental"):
        ff.client_round(ff.empty_state(voting), 0, 6, matrix, "site-1", voting)  # the default is the deployed answer
    ff.client_round(ff.empty_state(voting), 0, 6, matrix, "site-1", voting, local_dev=True)


def test_pooled_mode_refuses_a_one_round_job(ff, config, two_sites):
    matrix = next(iter(two_sites.values()))
    with pytest.raises(ValueError, match="GLOBAL_ROUNDS"):
        ff.client_round(ff.empty_state(config), 0, 1, matrix, "site-1", config)
    target, weight, _ = ff.client_round(ff.empty_state(config), 0, 2, matrix, "site-1", config)
    assert weight == matrix.shape[0]


def test_filter_bound_follows_a_stricter_site_policy(ff, monkeypatch):
    class Policy:
        gamma = 0.001

    monkeypatch.setattr(ff, "resolve_policy", lambda env: (Policy(), "env"))
    assert ff.effective_percentile_clip() == pytest.approx(0.001)
    monkeypatch.setattr(ff, "resolve_policy", lambda env: (None, "none"))
    assert ff.effective_percentile_clip() == ff.PERCENTILE_CLIP
    looser = type("P", (), {"gamma": 0.5})()
    monkeypatch.setattr(ff, "resolve_policy", lambda env: (looser, "env"))
    assert ff.effective_percentile_clip() == ff.PERCENTILE_CLIP  # never looser than the stock clip
    monkeypatch.setattr(ff, "resolve_policy", lambda env: (_ for _ in ()).throw(RuntimeError("bad policy")))
    assert ff.effective_percentile_clip() == ff.PERCENTILE_CLIP


def test_correlation_fisherz_matches_causallearn_on_the_same_rows(ff, config):
    from causallearn.utils.cit import CIT

    full = ff.prepare_matrix(sample_scm(500, 5), config)
    reference = CIT(full, "fisherz")
    mine = ff.CorrelationFisherZ(np.corrcoef(full.T), len(full))
    for x, y, s in ((0, 1, []), (2, 3, [0]), (4, 5, [1, 2, 3]), (0, 6, [2, 5])):
        assert mine(x, y, s) == pytest.approx(reference(x, y, s), abs=1e-12)


def test_never_condition_drops_the_sink_from_conditioning_sets(ff, config):
    full = ff.prepare_matrix(sample_scm(400, 8), config)
    corr = np.corrcoef(full.T)
    plain = ff.CorrelationFisherZ(corr, len(full))
    restricted = ff.CorrelationFisherZ(corr, len(full), never_condition={6})
    assert restricted(2, 4, [6, 1]) == pytest.approx(plain(2, 4, [1]))


# --- the site-count slots ----------------------------------------------------------------


@pytest.mark.parametrize("sizes", [(600, 600), (50, 1300, 275), (12, 12, 12, 12)])
def test_site_counts_recover_total_and_sizes_under_size_weighting(ff, sizes):
    slots = np.zeros(ff.MAX_SITES)
    total = float(sum(sizes))
    for j, n in enumerate(sizes):  # size-weighted mean of one-hot n*COUNT_SCALE vectors
        slots[j] = n * (n * ff.COUNT_SCALE) / total
    pooled, recovered = ff.recover_site_counts(slots, weighted_by_size=True)
    assert pooled == sum(sizes)
    assert recovered == {j: n for j, n in enumerate(sizes)}


def test_site_counts_recover_sizes_under_equal_weighting(ff):
    sizes = (40, 700, 9)
    slots = np.zeros(ff.MAX_SITES)
    for j, n in enumerate(sizes):
        slots[j] = n * ff.COUNT_SCALE / len(sizes)
    pooled, recovered = ff.recover_site_counts(slots, weighted_by_size=False)
    assert (pooled, recovered) == (sum(sizes), {0: 40, 1: 700, 2: 9})


@pytest.mark.parametrize(("name", "slot"), [("Trust_1", 0), ("site-2", 1), ("supernode-3", 2), ("Trust_65", 0)])
def test_site_slot_comes_from_the_trailing_site_number(ff, name, slot):
    assert ff.site_slot(name) == slot


def test_site_slot_without_a_number_is_stable_and_in_range(ff):
    assert ff.site_slot("GSTT") == ff.site_slot("GSTT")
    assert 0 <= ff.site_slot("GSTT") < ff.MAX_SITES


# --- the wire format and the platform's privacy filter -----------------------------------


def test_state_shapes_follow_the_config(ff, config):
    shapes = ff.state_shapes({**config, "N_BOOTSTRAPS": 7})
    p = len(VARIABLES)
    assert shapes["stats_mean"] == (p,)  # one statistic per site, whatever the bootstrap count
    assert shapes["stats_moment"] == (p, p)
    assert shapes["sepsets"] == (7, p, p, p)
    assert shapes["site_counts"] == (ff.MAX_SITES,)
    assert shapes["padding"][0] >= ff.PADDING_FRACTION * (p + p * p + 7 * (2 * p * p + p**3) + ff.MAX_SITES)
    assert ff.state_shapes(config)["pag"] == (1, p, p)  # N_BOOTSTRAPS 0 -> one unresampled slab


def test_encode_decode_round_trip_keeps_seven_digits(ff, config):
    state = ff.empty_state(config)
    state["stats_moment"][:] = 123.456789
    state["pag"][0, 0, 1] = 2
    back = ff.decode(ff.encode(state))
    assert back["stats_moment"][0, 0] == pytest.approx(123.456789, rel=1e-6)
    assert ff.as_pags(back["pag"])[0, 0, 1] == 2
    assert list(ff.encode(state)) == list(ff.STATE_KEYS)


def test_percentile_privacy_filter_is_a_no_op_on_a_round_one_payload(ff, config):
    matrix = ff.prepare_matrix(sample_scm(300, 2), config)
    target, weight, _ = ff.client_round(ff.decode(ff.encode(ff.empty_state(config))), 0, 2, matrix, "site-1", config)
    encoded = ff.encode(target)
    filtered = percentile_filter(ff, encoded, weight)
    for key in ff.STATE_KEYS:
        assert np.array_equal(filtered[key], encoded[key]), key


def test_unscaled_large_units_are_refused_rather_than_clipped(ff, config):
    unscaled = {**config, "SCALE": {}}  # u_mri in its raw thousands
    matrix = ff.prepare_matrix(sample_scm(300, 2), unscaled)
    with pytest.raises(ValueError, match="u_mri|stats_moment.*SCALE"):
        ff.client_round(ff.decode(ff.encode(ff.empty_state(unscaled))), 0, 2, matrix, "site-1", unscaled)


# --- recovering the generating graph -----------------------------------------------------


@pytest.fixture(scope="module")
def two_sites(ff, config) -> dict[str, np.ndarray]:
    frame = pd.concat([sample_scm(700, 21, age_shift=0.4), sample_scm(500, 22, age_shift=-0.4)], ignore_index=True)
    frame["person_id"] = np.arange(1, len(frame) + 1)
    return {f"site-{i + 1}": ff.prepare_matrix(frame[frame.person_id % 2 == i], config) for i in range(2)}


def test_pooled_mode_recovers_the_generating_graph(ff, config, two_sites):
    final = fedavg(ff, config, two_sites, total_rounds=2)
    edges = stable_edges(ff, ff.as_pags(final["pag"]))
    directed = {pair for pair, mark in edges.items() if mark == "-->"}
    assert TRUE_EDGES <= directed, TRUE_EDGES - directed
    assert edges.get(LATENT_PAIR) == "<->" or edges.get(LATENT_PAIR[::-1]) == "<->"
    extra = set(edges) - TRUE_EDGES - {LATENT_PAIR, LATENT_PAIR[::-1]}
    assert len(extra) <= 1, extra


def test_pooled_mode_equals_centralised_fci_on_the_pooled_rows(ff, config, two_sites):
    """The federated PAG is *the* centralised PAG: same rows pooled, same test, same rules."""
    final = fedavg(ff, config, two_sites, total_rounds=2)
    pooled_rows = np.vstack(list(two_sites.values()))
    centralised = ff.fci_from_correlation(np.corrcoef(pooled_rows.T), len(pooled_rows), VARIABLES, config, 0)
    assert np.array_equal(ff.as_pags(final["pag"])[0], centralised)


def test_voting_mode_recovers_the_skeleton_and_most_orientations(ff, config, two_sites):
    voting = {**config, "FEDERATION": "layer_voting"}
    final = fedavg(ff, voting, two_sites, total_rounds=5)  # conditioning sets of size 0..3, then orient
    pags = ff.as_pags(final["pag"])
    adjacent = {frozenset(pair) for pair in stable_edges(ff, pags)}
    expected = {frozenset(e) for e in TRUE_EDGES} | {frozenset(LATENT_PAIR)}
    assert adjacent == expected
    skeleton = final["skeleton"][0] >= voting["VOTE_RATIO"]
    assert np.array_equal(skeleton, skeleton.T)
    directed = {pair for pair, mark in stable_edges(ff, pags).items() if mark == "-->"}
    assert len(directed & TRUE_EDGES) >= len(TRUE_EDGES) - 2


def test_bootstraps_give_frequencies_strictly_between_zero_and_one_somewhere(ff, config, two_sites):
    boot = {**config, "N_BOOTSTRAPS": 6}
    final = fedavg(ff, boot, two_sites, total_rounds=2)
    rows = ff.edge_frequencies(ff.as_pags(final["pag"]), VARIABLES)
    assert any(0 < r["probability"] < 1 for r in rows)
    assert all(r["count"] <= 6 for r in rows)


def test_the_final_model_carries_graphs_and_counts_but_no_statistics(ff, config, two_sites):
    """What a researcher downloads: PAGs and row counts. The pooled moments never travel on."""
    final = fedavg(ff, {**config, "N_BOOTSTRAPS": 6}, two_sites, total_rounds=2)
    assert np.any(ff.as_pags(final["pag"]))
    assert ff.recover_site_counts(final["site_counts"], True)[0] == sum(len(m) for m in two_sites.values())
    assert not np.any(final["stats_mean"])
    assert not np.any(final["stats_moment"])
    # ... whereas a two-round job the hub stops after round one holds, by design, the pooled statistics.
    after_round_one = fedavg(ff, {**config, "N_BOOTSTRAPS": 6}, two_sites, total_rounds=2, stop_after=1)
    assert np.any(after_round_one["stats_moment"])


def test_every_site_computes_the_same_pag_so_averaging_is_exact(ff, config, two_sites):
    """Round 2 of pooled mode: both sites must produce identical PAGs, or their mean is garbage."""
    encoded = ff.encode(ff.empty_state(config))
    targets, weights = [], []
    for name, matrix in two_sites.items():
        t, w, _ = ff.client_round(ff.decode(encoded), 0, 2, matrix, name, config)
        targets.append(ff.encode(t))
        weights.append(w)
    pooled = {
        k: sum(w * t[k].astype(np.float64) for w, t in zip(weights, targets, strict=True)) / sum(weights)
        for k in ff.STATE_KEYS
    }
    pags = [
        ff.client_round(ff.decode(pooled), 1, 2, matrix, name, config)[0]["pag"] for name, matrix in two_sites.items()
    ]
    assert np.array_equal(pags[0], pags[1])


def test_a_variable_constant_across_the_pooled_cohort_is_refused_not_isolated(ff, config, two_sites):
    """The parametric bootstrap would draw pure noise for it and the graph would show it unconnected."""
    b = VARIABLES.index("b_root")
    sites = {name: matrix.copy() for name, matrix in two_sites.items()}
    for matrix in sites.values():
        matrix[:, b] = 1.0  # the same single level at every site clears the small-cell rule
    encoded = ff.encode(ff.empty_state(config))
    targets, weights = [], []
    for name, matrix in sites.items():
        t, w, _ = ff.client_round(ff.decode(encoded), 0, 2, matrix, name, config)
        targets.append(ff.encode(t))
        weights.append(w)
    pooled = {
        k: sum(w * t[k].astype(np.float64) for w, t in zip(weights, targets, strict=True)) / sum(weights)
        for k in ff.STATE_KEYS
    }
    with pytest.raises(ValueError, match="constant across the pooled cohort.*b_root"):
        ff.client_round(ff.decode(pooled), 1, 2, next(iter(sites.values())), "site-1", config)


# --- summaries and bookkeeping ------------------------------------------------------------


def test_edge_frequencies_mirror_and_count(ff):
    pags = np.zeros((4, 3, 3), dtype=np.int8)
    pags[:3, 0, 1], pags[:3, 1, 0] = ff.MARK_TAIL, ff.MARK_ARROW  # 0 --> 1 in three of four
    pags[:, 2, 1], pags[:, 1, 2] = ff.MARK_ARROW, ff.MARK_TAIL  # 1 --> 2 stored as (2,1) tail/arrow: mirrored
    rows = ff.edge_frequencies(pags, ["p", "q", "r"])
    assert rows[0] == {"source": "q", "edge": "-->", "target": "r", "count": 4, "probability": 1.0, "p_adjacent": 1.0}
    assert rows[1] == {"source": "p", "edge": "-->", "target": "q", "count": 3, "probability": 0.75, "p_adjacent": 0.75}
    assert ff.summary_metrics(pags, ["p", "q", "r"], 0.5) == {
        "stable_edges": 2.0,
        "mean_edges_per_pag": 1.75,
        "stable_adjacencies": 2.0,
    }


def test_layer_step_removes_marginally_independent_pairs_and_records_the_set(ff, config):
    matrix = ff.prepare_matrix(sample_scm(800, 4), config)
    p = len(VARIABLES)
    skeleton = ff.complete_skeleton(VARIABLES, config)
    assert skeleton[0, 1] == 0  # two roots: forbidden both ways, never adjacent
    out_skeleton, out_sepsets = ff.layer_step(matrix, skeleton, np.zeros((p, p, p)), 0, config, frozenset({6}))
    assert out_skeleton[0, 3] == 1  # a_root -> x_fluid -> y_fluid: marginally dependent, kept at order 0
    assert out_skeleton[1, 2] == 0  # b_root and x_fluid: marginally independent, removed
    assert out_sepsets[1, 2].sum() == 0  # ... with the empty separating set
    assert out_skeleton[0, 2] == 1  # a_root -> x_fluid stays


def test_knowledge_forbidden_pairs_carry_no_separating_set(ff, config):
    """A pair the knowledge rules out in both directions was never tested, so it must not be read as
    "marginally independent" and orient a collider at every common neighbour (pooled mode's fas()
    records no sepset for such a pair either, so the two modes must agree)."""
    forbidden_config = {
        **config,
        "KNOWLEDGE": {**config["KNOWLEDGE"], "FORBIDDEN": [["x_fluid", "y_fluid"], ["y_fluid", "x_fluid"]]},
    }
    p = len(VARIABLES)
    x, y, v = VARIABLES.index("x_fluid"), VARIABLES.index("y_fluid"), VARIABLES.index("v_mri")
    skeleton = np.zeros((p, p))
    skeleton[x, v] = skeleton[v, x] = skeleton[y, v] = skeleton[v, y] = 1.0  # x - v - y, x and y never tested
    pag = ff.pag_from_skeleton(skeleton, np.zeros((p, p, p)), VARIABLES, forbidden_config, 0)
    assert pag[v, x] == ff.MARK_CIRCLE  # no collider at v ...
    assert pag[v, y] == ff.MARK_CIRCLE
    assert pag[x, v] == ff.MARK_CIRCLE  # ... and nothing else oriented either
    assert pag[y, v] == ff.MARK_CIRCLE
    # The same skeleton with an empty *tested* separating set for (x, y) is a genuine unshielded collider.
    tested = {**config, "KNOWLEDGE": {**config["KNOWLEDGE"], "FORBIDDEN": []}}
    pag = ff.pag_from_skeleton(skeleton, np.zeros((p, p, p)), VARIABLES, tested, 0)
    assert pag[v, x] == ff.MARK_ARROW
    assert pag[v, y] == ff.MARK_ARROW


def test_shipped_config_names_only_known_variables(ff):
    config = json.loads((APP_FILES / "config.json").read_text())
    variables = set(config["VARIABLES"])
    knowledge = config["KNOWLEDGE"]
    for key in ("EXOGENOUS", "SINKS", "NEVER_CONDITION_ON"):
        assert set(knowledge[key]) <= variables, key
    assert set(config["LOG_TRANSFORM"]) <= variables
    assert set(config["SCALE"]) <= variables
    assert ff.federation_mode(config) in ff.MODES
    assert config["GLOBAL_ROUNDS"] == 2 if config["FEDERATION"] == "pooled_statistics" else config["GLOBAL_ROUNDS"] >= 3


def test_get_model_buffers_match_the_wire_format():
    models = load_module("causal_models", APP_FILES / "models.py")
    ff = load_module("federated_fci", APP_FILES / "federated_fci.py")
    config = json.loads((APP_FILES / "config.json").read_text())
    state = models.get_model().state_dict()
    assert list(state) == list(ff.STATE_KEYS)
    for key, shape in ff.state_shapes(config).items():
        assert tuple(state[key].shape) == shape, key
        assert not state[key].any()


def test_the_flower_copy_is_the_same_module():
    flower = Path(TUTORIALS_ROOT / "flower" / "biomarker_causal_discovery" / "app" / "federated_fci.py")
    assert flower.read_bytes() == (APP_FILES / "federated_fci.py").read_bytes()
