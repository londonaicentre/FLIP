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

"""Federated bootstrapped FCI, expressed as weighted averaging of client arrays.

FLIP's two backends aggregate one way: a weighted mean of the arrays each site returns (FedAvg).
This module turns constraint-based causal discovery into that shape, so the platform's fixed
server code (the NVFLARE recipe, the Flower template ``server_app.py``) runs it unchanged. Both
the NVFLARE ``trainer.py`` and the Flower ``client_app.py`` are thin transports around
:func:`client_round`; everything that decides *what* a site sends lives here, once, and is
kept byte-identical between the two tutorials (``scripts/check_tutorial_sync.sh``).

Two federation modes, selected by ``config.json``'s ``FEDERATION``:

``pooled_statistics``
    Each site sends the sufficient statistics of Fisher's z test — the per-bootstrap mean
    vector and second-moment matrix of its (log-transformed) variables — weighted by its
    sample size. The weighted mean of those *is* the pooled cohort's statistics, so the
    correlation matrix every site receives back is exactly the one a centralised run would
    compute, and FCI on it (round 2, run identically at every site) is exactly centralised
    FCI with the Fisher-z test. This is the "federated CI test" idea of FedC²SL (Wang et al.,
    2023) specialised to the Gaussian case, where the test needs nothing but second moments.
    Two rounds; one exchange of ``p × p`` matrices per bootstrap; no raw rows leave a site.

``layer_voting``
    The FedPC scheme (Huang et al., 2023): one global round per conditioning-set size. Each
    site runs that layer's CI tests on its own data against the current global skeleton and
    sends back a 0/1 adjacency matrix plus 0/1 separating-set membership; the server's plain
    average is the share of sites that kept each edge, which the sites threshold at
    ``VOTE_RATIO`` next round (FedPC's "vote"), and any site's separating set survives (the
    union). The last round orients the voted skeleton with FCI's rules. Works with any local
    CI test (``CI_TEST: "kci"`` included), at the cost of exactness: a vote is not a pooled
    test, and FedPC's own paper discusses the inconsistent-separating-set problem it creates.

Bootstrapping is *stratified by site*: bootstrap ``b`` at site ``s`` resamples that site's own
rows with the stream ``(SEED, s, b)``, so a run is reproducible and the ``B`` resamples line up
across sites. Everything is computed for all ``B`` bootstraps at once (arrays carry a leading
bootstrap axis), so the number of rounds does not depend on ``B``.

Two platform details shape the wire format:

* The NVFLARE standard template applies a percentile-privacy filter to every training result:
  it zeroes the smallest 10 % of a site's update (by absolute value, across all arrays) and
  clips the rest to ±0.01 per local step. Both are no-ops here by construction: arrays are
  sent multiplied by :data:`ENCODING_SCALE` (so no entry reaches the clip), and a zero
  ``padding`` array a quarter the size of the rest guarantees the 10th percentile is exactly
  zero (so nothing non-zero is zeroed). The Flower local-DP mod is simply off in config.toml,
  because clipping and noising a correlation matrix is not a meaningful privacy mechanism for
  it — see the README's privacy section for what is and is not protected.
* No site knows how many sites there are, yet Fisher's z needs the pooled sample size. Each
  site writes its own count into one slot of ``site_counts`` (slot = its site index); after
  size-weighted averaging slot ``j`` holds ``n_j² / N``, and ``N = (Σ_j √slot_j)²`` recovers
  the total exactly. Under equal weights (voting mode) slot ``j`` holds ``n_j / K``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import math
import re
import zlib
from collections.abc import Iterable, Sequence

import numpy as np
from causallearn.graph.Edge import Edge
from causallearn.graph.Endpoint import Endpoint
from causallearn.graph.GeneralGraph import GeneralGraph
from causallearn.graph.GraphNode import GraphNode
from causallearn.search.ConstraintBased import FCI as fci_rules
from causallearn.utils.cit import CIT, CIT_Base
from causallearn.utils.FAS import fas
from causallearn.utils.PCUtils.BackgroundKnowledge import BackgroundKnowledge

# causal-learn iterates over *sets* of nodes in several orientation rules, and GraphNode hashes
# its name — which Python salts per process. Without a fixed hash the same inputs can orient
# differently at two sites, and the two PAGs would no longer average to themselves.
GraphNode.__hash__ = lambda node: zlib.crc32(node.name.encode())  # type: ignore[method-assign]

MODES = ("pooled_statistics", "layer_voting")
#: Every array is sent multiplied by this (and divided by it on receipt), so the NVFLARE
#: percentile-privacy clip at ±0.01 never engages. Float32 keeps ~7 significant digits, which
#: is far more than a correlation needs.
ENCODING_SCALE = 1e-5
#: The NVFLARE percentile-privacy filter's clip, per local step (stock ``gamma``). Every sent entry
#: must stay below it once scaled, or the filter silently truncates it — hence ``SCALE`` in
#: config.json and the check in :func:`assert_within_filter_bounds`.
PERCENTILE_CLIP = 0.01
#: Slots in ``site_counts``. A site's slot is its index (Trust_1 → 0); a site whose name
#: carries no number hashes into the table, with a small collision risk that only ever
#: *under*-estimates the pooled sample size (a conservative test).
MAX_SITES = 64
#: Fraction of the payload that is zero padding, so the percentile filter's cutoff is zero.
PADDING_FRACTION = 0.25
#: ``site_counts`` carries ``n × COUNT_SCALE`` so a site of any realistic size stays below the clip
#: even under voting mode's weight of one.
COUNT_SCALE = 1e-4
STATE_KEYS = ("site_counts", "stats_mean", "stats_moment", "skeleton", "sepsets", "pag", "padding")

# PAG endpoint marks in causal-learn's ``graph.graph`` convention: ``G[i, j]`` is the mark at i on
# the i–j edge: -1 tail, 1 arrowhead, 2 circle, 0 no edge.
MARK_TAIL, MARK_ARROW, MARK_CIRCLE = -1, 1, 2
# (mark at source, mark at target) -> edge read left to right; mirror images are flipped.
EDGE_SYMBOLS = {(-1, 1): "-->", (2, 1): "o->", (1, 1): "<->", (2, 2): "o-o", (-1, -1): "---", (2, -1): "o--"}
_MIRRORED = {(1, -1), (1, 2), (-1, 2)}
EDGE_MEANINGS = {
    "-->": "A causes B (A is an ancestor of B, B is not an ancestor of A)",
    "<->": "neither causes the other: a latent confounder of A and B",
    "o->": "B does not cause A; A causes B and/or they share a latent confounder",
    "o-o": "adjacent, orientation undetermined",
}


# --- configuration ------------------------------------------------------------------------


def n_bootstrap_slabs(config: dict) -> int:
    """How many bootstrap slabs the arrays carry: ``N_BOOTSTRAPS``, or one unresampled fit for 0."""
    return max(int(config.get("N_BOOTSTRAPS", 0)), 1)


def resampling_enabled(config: dict) -> bool:
    return int(config.get("N_BOOTSTRAPS", 0)) > 0


def federation_mode(config: dict) -> str:
    mode = config.get("FEDERATION", "pooled_statistics")
    if mode not in MODES:
        raise ValueError(f"FEDERATION must be one of {MODES}, got {mode!r}")
    return mode


def state_shapes(config: dict) -> dict[str, tuple[int, ...]]:
    """Shape of every array in the wire format, from the variable count and bootstrap count."""
    p = len(config["VARIABLES"])
    b = n_bootstrap_slabs(config)
    core = {
        "site_counts": (MAX_SITES,),
        "stats_mean": (b, p),
        "stats_moment": (b, p, p),
        "skeleton": (b, p, p),
        "sepsets": (b, p, p, p),
        "pag": (b, p, p),
    }
    n_core = sum(math.prod(shape) for shape in core.values())
    return {**core, "padding": (int(math.ceil(PADDING_FRACTION * n_core)),)}


def empty_state(config: dict) -> dict[str, np.ndarray]:
    return {key: np.zeros(shape, dtype=np.float64) for key, shape in state_shapes(config).items()}


def encode(state: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The arrays as they travel: scaled float32, in :data:`STATE_KEYS` order."""
    return {key: (np.asarray(state[key], dtype=np.float64) * ENCODING_SCALE).astype(np.float32) for key in STATE_KEYS}


def decode(arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Inverse of :func:`encode`, back to float64 in natural units."""
    return {key: np.asarray(arrays[key], dtype=np.float64) / ENCODING_SCALE for key in STATE_KEYS}


def as_pags(pag_array: np.ndarray) -> np.ndarray:
    """Endpoint marks as integers again after a float32 round trip (1.0000001 is an arrowhead)."""
    return np.rint(np.asarray(pag_array, dtype=np.float64)).astype(np.int8)


def assert_within_filter_bounds(target: dict[str, np.ndarray], weight: int) -> None:
    """Refuse a payload the platform's percentile-privacy clip would truncate.

    The filter divides a site's update by its weight and clips at ±:data:`PERCENTILE_CLIP`;
    an entry beyond that is corrupted silently and the run goes on to report a wrong graph.
    A variable in its raw units (a volume in mm³) overshoots by orders of magnitude, which is
    what ``LOG_TRANSFORM`` and ``SCALE`` in config.json exist for; this check turns a forgotten
    one into an error that names it.
    """
    limit = PERCENTILE_CLIP * max(int(weight), 1) / ENCODING_SCALE
    for key, array in target.items():
        largest = float(np.max(np.abs(array))) if array.size else 0.0
        if largest > limit:
            raise ValueError(
                f"{key} holds a value of {largest:.3g}, above the {limit:.3g} the platform's privacy filter "
                "would clip at: scale the variable down (config.json SCALE) or log-transform it (LOG_TRANSFORM)"
            )


# --- data ---------------------------------------------------------------------------------


def prepare_matrix(dataframe, config: dict) -> np.ndarray:
    """The analysis matrix: ``VARIABLES`` columns, logged or divided by a constant as configured.

    Log-transforming the right-skewed markers is a fixed, site-independent choice (the
    centralised analysis used a data-fitted Yeo-Johnson transform, which cannot be shared
    across sites without exchanging it). ``SCALE`` divides a variable by a constant so its
    second moments are of order one — Fisher's z is scale-free, so the graph is unchanged, but
    the platform's privacy filter clips large values (see :func:`assert_within_filter_bounds`).
    Nothing is standardised per site: pooling second moments that each site had standardised
    separately would not give the pooled cohort's correlation.
    """
    variables = list(config["VARIABLES"])
    missing = [v for v in variables if v not in dataframe.columns]
    if missing:
        raise KeyError(f"cohort lacks the configured VARIABLES {missing}; columns are {list(dataframe.columns)}")
    matrix = dataframe[variables].to_numpy(dtype=np.float64, copy=True)
    for column in config.get("LOG_TRANSFORM", []):
        index = variables.index(column)
        values = matrix[:, index]
        if np.any(values <= 0):
            raise ValueError(f"{column} is listed in LOG_TRANSFORM but has non-positive values")
        matrix[:, index] = np.log(values)
    for column, divisor in config.get("SCALE", {}).items():
        if column in variables:
            matrix[:, variables.index(column)] /= float(divisor)
    keep = np.isfinite(matrix).all(axis=1)
    return matrix[keep]


def partition_for_site(dataframe, site_name: str, num_partitions: int):
    """LOCAL_DEV only: this site's ``person_id``-modulo slice of a shared dev cohort.

    The simulators hand every site the same CSV; a deployed trust already holds its own
    cohort and must not be sliced again (the callers check ``FlipConstants.LOCAL_DEV``).
    Trailing digits of the site name pick the partition (``site-1`` → the first).
    """
    match = re.search(r"(\d+)\s*$", str(site_name or ""))
    if match is None or num_partitions <= 1:
        return dataframe
    if "person_id" not in dataframe.columns:
        raise KeyError("partition_for_site needs a 'person_id' column to slice deterministically")
    index = (int(match.group(1)) - 1) % num_partitions
    person_id = dataframe["person_id"].astype("int64")
    return dataframe[person_id % num_partitions == index].reset_index(drop=True)


def site_slot(site_name: str) -> int:
    """Which ``site_counts`` slot this site writes: its index when the name ends in one."""
    match = re.search(r"(\d+)\s*$", site_name or "")
    if match:
        return (int(match.group(1)) - 1) % MAX_SITES
    digest = hashlib.sha256((site_name or "").encode()).hexdigest()
    return int(digest, 16) % MAX_SITES


def bootstrap_rows(
    matrix: np.ndarray, b: int, seed: int, slot: int, resample: bool, max_tries: int = 100
) -> np.ndarray:
    """Row indices of bootstrap ``b`` at this site, redrawn while any column is constant.

    A constant column makes every CI test involving it undefined, so such a resample is drawn
    again from the same stream — the same rule the centralised analysis used.
    """
    n = matrix.shape[0]
    if not resample:
        return np.arange(n)
    rng = np.random.default_rng([int(seed), int(slot), int(b)])
    for _ in range(max_tries):
        rows = rng.integers(0, n, size=n)
        if matrix[rows].std(axis=0).min() > 0:
            return rows
    raise ValueError(f"no usable bootstrap resample in {max_tries} draws: too few rows (n={n}) for these variables")


def column_order(b: int, seed: int, p: int) -> np.ndarray:
    """The variable order bootstrap ``b`` is oriented in (FCI's output can depend on it).

    Shared across sites (no site term in the stream), so the sites agree on the PAG.
    """
    return np.random.default_rng([int(seed), 7919, int(b)]).permutation(p)


# --- pooled statistics ----------------------------------------------------------------------


def site_statistics(matrix: np.ndarray, config: dict, slot: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-bootstrap mean vector and second-moment matrix of this site's rows."""
    b_total = n_bootstrap_slabs(config)
    resample = resampling_enabled(config)
    seed = int(config.get("SEED", 0))
    means = np.zeros((b_total, matrix.shape[1]))
    moments = np.zeros((b_total, matrix.shape[1], matrix.shape[1]))
    for b in range(b_total):
        sample = matrix[bootstrap_rows(matrix, b, seed, slot, resample)]
        means[b] = sample.mean(axis=0)
        moments[b] = sample.T @ sample / sample.shape[0]
    return means, moments


def pooled_correlation(means: np.ndarray, moments: np.ndarray) -> np.ndarray:
    """Correlation matrices from size-weighted mean vectors and second moments."""
    cov = moments - np.einsum("bi,bj->bij", means, means)
    scale = np.sqrt(np.clip(np.einsum("bii->bi", cov), 1e-300, None))
    corr = cov / (scale[:, :, None] * scale[:, None, :])
    return np.clip(corr, -1.0, 1.0)


def recover_site_counts(site_counts: np.ndarray, weighted_by_size: bool) -> tuple[int, dict[int, int]]:
    """Pooled sample size and per-slot sizes from the averaged ``site_counts`` array."""
    slots = np.asarray(site_counts, dtype=np.float64) / COUNT_SCALE
    filled = np.flatnonzero(slots > 1e-6)
    if filled.size == 0:
        return 0, {}
    if weighted_by_size:
        total = float(np.sum(np.sqrt(slots[filled]))) ** 2
        sizes = {int(j): int(round(math.sqrt(slots[j] * total))) for j in filled}
    else:
        sizes = {int(j): int(round(slots[j] * filled.size)) for j in filled}
        total = float(sum(sizes.values()))
    return int(round(total)), sizes


class CorrelationFisherZ(CIT_Base):
    """Fisher's z test from a correlation matrix and a sample size, no data needed.

    causal-learn's ``FisherZ`` computes the correlation matrix from rows; this one takes the
    matrix directly (the pooled one), so the full FCI machinery — including the possible-D-sep
    stage and rule R4, which issue further tests — runs unchanged. Indices in
    ``never_condition`` are dropped from every conditioning set (the centralised analysis
    never conditioned on the cognitive score, a downstream collider).
    """

    def __init__(self, correlation: np.ndarray, sample_size: int, never_condition: Iterable[int] = ()):
        p = correlation.shape[0]
        # CIT_Base wants a data array; FAS and the rules only ever read its shape.
        super().__init__(np.zeros((max(int(sample_size), 2), p)))
        self.method = "fisherz"
        self.correlation_matrix = np.asarray(correlation, dtype=np.float64)
        self.sample_size = int(sample_size)
        self.never_condition = frozenset(int(i) for i in never_condition)

    def __call__(self, X, Y, condition_set=None):
        if condition_set is not None:
            condition_set = [int(c) for c in condition_set if int(c) not in self.never_condition]
        Xs, Ys, condition_set, cache_key = self.get_formatted_XYZ_and_cachekey(X, Y, condition_set)
        if cache_key in self.pvalue_cache:
            return self.pvalue_cache[cache_key]
        var = Xs + Ys + condition_set
        sub = self.correlation_matrix[np.ix_(var, var)]
        try:
            inv = np.linalg.inv(sub)
            r = -inv[0, 1] / math.sqrt(abs(inv[0, 0] * inv[1, 1]))
        except (np.linalg.LinAlgError, ZeroDivisionError, ValueError):
            r = float("nan")
        if not np.isfinite(r):
            p_value = 1.0  # an undefined partial correlation is treated as "no evidence of dependence"
        else:
            if abs(r) >= 1:
                r = (1.0 - np.finfo(float).eps) * np.sign(r)
            z = 0.5 * math.log((1 + r) / (1 - r))
            statistic = math.sqrt(max(self.sample_size - len(condition_set) - 3, 1)) * abs(z)
            p_value = float(math.erfc(statistic / math.sqrt(2.0)))  # 2 * (1 - Phi(|statistic|))
        self.pvalue_cache[cache_key] = p_value
        return p_value


# --- background knowledge and orientation -------------------------------------------------


def knowledge_block(config: dict) -> dict:
    return dict(config.get("KNOWLEDGE", {}))


def forbidden_pairs(variables: Sequence[str], config: dict) -> set[tuple[int, int]]:
    """Every (cause, effect) index pair the background knowledge rules out."""
    index = {name: i for i, name in enumerate(variables)}
    block = knowledge_block(config)
    forbidden: set[tuple[int, int]] = set()
    present = set(variables)
    for root in block.get("EXOGENOUS", []):  # nothing causes a root
        if root in present:
            forbidden.update((index[v], index[root]) for v in variables if v != root)
    for sink in block.get("SINKS", []):  # a sink causes nothing
        if sink in present:
            forbidden.update((index[sink], index[v]) for v in variables if v != sink)
    for cause, effect in block.get("FORBIDDEN", []):
        if cause in present and effect in present:
            forbidden.add((index[cause], index[effect]))
    return forbidden


def never_condition_indices(variables: Sequence[str], config: dict) -> frozenset[int]:
    names = set(knowledge_block(config).get("NEVER_CONDITION_ON", []))
    return frozenset(i for i, v in enumerate(variables) if v in names)


def make_nodes(names: Sequence[str]) -> list[GraphNode]:
    nodes = []
    for i, name in enumerate(names):
        node = GraphNode(str(name))
        node.add_attribute("id", i)
        nodes.append(node)
    return nodes


def background_knowledge(nodes: Sequence[GraphNode], forbidden: Iterable[tuple[int, int]]) -> BackgroundKnowledge:
    bk = BackgroundKnowledge()
    for cause, effect in sorted(forbidden):
        bk.add_forbidden_by_node(nodes[cause], nodes[effect])
    return bk


def orient_pag(
    graph: GeneralGraph,
    nodes: Sequence[GraphNode],
    sep_sets: dict,
    bk: BackgroundKnowledge | None,
    *,
    ci_test: CIT_Base | None = None,
    data: np.ndarray | None = None,
    alpha: float = 0.05,
    max_path_length: int = -1,
) -> None:
    """FCI's orientation phase on a skeleton, in place — causal-learn's ``fci()`` after FAS.

    With a ``ci_test`` this is the complete algorithm (the possible-D-sep removal and rule R4
    issue further tests); without one it is the test-free subset, which is what the voted
    skeleton of ``layer_voting`` mode can support.
    """
    rules = fci_rules
    with contextlib.redirect_stdout(io.StringIO()):  # causal-learn prints every knowledge orientation
        _orient_pag(graph, nodes, sep_sets, bk, rules, ci_test, data, alpha, max_path_length)


def _orient_pag(graph, nodes, sep_sets, bk, rules, ci_test, data, alpha, max_path_length) -> None:
    rules.reorientAllWith(graph, Endpoint.CIRCLE)
    rules.rule0(graph, list(nodes), sep_sets, bk, False)
    if ci_test is not None:
        rules.removeByPossibleDsep(graph, ci_test, alpha, sep_sets)
        rules.reorientAllWith(graph, Endpoint.CIRCLE)
        rules.rule0(graph, list(nodes), sep_sets, bk, False)

    change, first_time = True, True
    while change:
        change = False
        change = rules.rulesR1R2cycle(graph, bk, change, False)
        change = rules.ruleR3(graph, sep_sets, bk, change, False)
        if ci_test is not None and (
            change
            or (
                first_time
                and bk is not None
                and len(bk.forbidden_rules_specs) > 0
                and len(bk.required_rules_specs) > 0
                and len(bk.tier_map.keys()) > 0
            )
        ):
            change = rules.ruleR4B(graph, max_path_length, data, ci_test, alpha, sep_sets, change, bk, False)
            first_time = False
        change = rules.ruleR5(graph, change, False)
        change = rules.ruleR6(graph, change, False)
        change = rules.ruleR7(graph, change, False)
        change = rules.rule8(graph, list(nodes), change)
        change = rules.rule9(graph, list(nodes), change)
        change = rules.rule10(graph, change)
    graph.set_pag(True)


def _unpermute(pag: np.ndarray, order: np.ndarray) -> np.ndarray:
    out = np.empty_like(pag)
    out[np.ix_(order, order)] = pag
    return out


def fci_from_correlation(
    correlation: np.ndarray, sample_size: int, variables: Sequence[str], config: dict, b: int
) -> np.ndarray:
    """Complete FCI (Fisher-z) on one correlation matrix; the PAG in canonical variable order."""
    p = len(variables)
    order = column_order(b, int(config.get("SEED", 0)), p)
    names = [variables[k] for k in order]
    forbidden = {
        (int(np.where(order == c)[0][0]), int(np.where(order == e)[0][0]))
        for c, e in forbidden_pairs(variables, config)
    }
    never = {int(np.where(order == i)[0][0]) for i in never_condition_indices(variables, config)}
    nodes = make_nodes(names)
    bk = background_knowledge(nodes, forbidden)
    ci_test = CorrelationFisherZ(correlation[np.ix_(order, order)], sample_size, never)
    alpha = float(config.get("ALPHA", 0.05))
    data = np.zeros((max(int(sample_size), 2), p))
    graph, sep_sets, _ = fas(
        data, nodes, ci_test, alpha=alpha, knowledge=bk, depth=int(config.get("MAX_DEPTH", -1)), show_progress=False
    )
    orient_pag(graph, nodes, sep_sets, bk, ci_test=ci_test, data=data, alpha=alpha)
    return _unpermute(graph.graph.astype(np.int8), order)


def pag_from_skeleton(
    skeleton: np.ndarray, sepsets: np.ndarray, variables: Sequence[str], config: dict, b: int
) -> np.ndarray:
    """Orient a (voted) skeleton with its (unioned) separating sets; no further CI tests."""
    p = len(variables)
    order = column_order(b, int(config.get("SEED", 0)), p)
    position = {int(k): i for i, k in enumerate(order)}  # canonical index -> permuted index
    names = [variables[k] for k in order]
    nodes = make_nodes(names)
    forbidden = {(position[c], position[e]) for c, e in forbidden_pairs(variables, config)}
    bk = background_knowledge(nodes, forbidden)
    graph = GeneralGraph(nodes)
    sep_sets: dict = {}
    for i in range(p):
        for j in range(i + 1, p):
            if skeleton[i, j] > 0:
                graph.add_edge(Edge(nodes[position[i]], nodes[position[j]], Endpoint.CIRCLE, Endpoint.CIRCLE))
            else:
                members = {position[int(k)] for k in np.flatnonzero(sepsets[i, j] > 0)}
                sep_sets[(position[i], position[j])] = members
                sep_sets[(position[j], position[i])] = members
    orient_pag(graph, nodes, sep_sets, bk)
    return _unpermute(graph.graph.astype(np.int8), order)


# --- layer voting (FedPC) -----------------------------------------------------------------


def complete_skeleton(variables: Sequence[str], config: dict) -> np.ndarray:
    """The round-0 skeleton: complete, minus pairs the knowledge forbids in both directions."""
    p = len(variables)
    skeleton = np.ones((p, p), dtype=np.float64) - np.eye(p)
    forbidden = forbidden_pairs(variables, config)
    for i, j in forbidden:
        if (j, i) in forbidden:
            skeleton[i, j] = skeleton[j, i] = 0.0
    return skeleton


def layer_step(
    matrix: np.ndarray,
    skeleton: np.ndarray,
    sepsets: np.ndarray,
    order: int,
    config: dict,
    never_condition: frozenset[int],
) -> tuple[np.ndarray, np.ndarray]:
    """One FedPC layer at this site: tests with conditioning sets of exactly ``order`` variables.

    PC-stable: neighbourhoods come from the skeleton as received, so the result does not depend
    on the order pairs are visited. Returns the site's 0/1 skeleton and the accumulated 0/1
    separating-set membership (received union ∪ newly found).
    """
    p = skeleton.shape[0]
    adjacency = skeleton > 0
    out_skeleton = adjacency.astype(np.float64)
    out_sepsets = (sepsets > 0).astype(np.float64)
    ci_test = CIT(matrix, str(config.get("CI_TEST", "fisherz")))
    alpha = float(config.get("ALPHA", 0.05))
    for x in range(p):
        for y in range(x + 1, p):
            if not adjacency[x, y]:
                continue
            found = None
            for anchor, other in ((y, x), (x, y)):
                candidates = [k for k in np.flatnonzero(adjacency[anchor]) if k != other and k not in never_condition]
                if len(candidates) < order:
                    continue
                for subset in itertools.combinations(candidates, order):
                    if ci_test(x, y, list(subset)) > alpha:
                        found = subset
                        break
                if found is not None:
                    break
            if found is not None:
                out_skeleton[x, y] = out_skeleton[y, x] = 0.0
                for k in found:
                    out_sepsets[x, y, k] = out_sepsets[y, x, k] = 1.0
    return out_skeleton, out_sepsets


# --- summaries ----------------------------------------------------------------------------


def edge_frequencies(pags: np.ndarray, variables: Sequence[str]) -> list[dict]:
    """How often each edge type links each pair across the bootstrap PAGs, strongest first."""
    pags = np.asarray(pags)
    n_boot, p, _ = pags.shape
    rows = []
    for i in range(p):
        for j in range(i + 1, p):
            marks: dict[tuple[int, int], int] = {}
            for mark_i, mark_j in zip(pags[:, i, j].tolist(), pags[:, j, i].tolist(), strict=True):
                key = (int(mark_i), int(mark_j))
                marks[key] = marks.get(key, 0) + 1
            p_adjacent = 1 - marks.pop((0, 0), 0) / n_boot
            for (mark_i, mark_j), count in marks.items():
                source, target = variables[i], variables[j]
                if (mark_i, mark_j) in _MIRRORED:
                    mark_i, mark_j, source, target = mark_j, mark_i, target, source
                rows.append(
                    {
                        "source": source,
                        "edge": EDGE_SYMBOLS.get((mark_i, mark_j), f"{mark_i}/{mark_j}"),
                        "target": target,
                        "count": int(count),
                        "probability": count / n_boot,
                        "p_adjacent": float(p_adjacent),
                    }
                )
    return sorted(rows, key=lambda r: (-r["probability"], -r["p_adjacent"], r["source"], r["target"]))


def format_edges(rows: list[dict], min_prob: float = 0.0) -> str:
    shown = [r for r in rows if r["probability"] >= min_prob]
    if not shown:
        return f"  (no edges with frequency >= {min_prob})"
    w_src = max(len(r["source"]) for r in shown)
    w_tgt = max(len(r["target"]) for r in shown)
    return "\n".join(
        f"  {r['source']:<{w_src}}  {r['edge']}  {r['target']:<{w_tgt}}  {r['probability']:.2f}" for r in shown
    )


def summary_metrics(pags: np.ndarray, variables: Sequence[str], stable_prob: float) -> dict[str, float]:
    """The scalars a site reports: stable-edge count, mean edge count per PAG, adjacency count."""
    pags = as_pags(pags)
    if not np.any(pags):
        return {"stable_edges": 0.0, "mean_edges_per_pag": 0.0, "stable_adjacencies": 0.0}
    rows = edge_frequencies(pags, variables)
    per_pag = [(np.triu(pag != 0, 1)).sum() for pag in pags]
    adjacent = {(r["source"], r["target"]) for r in rows if r["p_adjacent"] >= stable_prob}
    return {
        "stable_edges": float(sum(r["probability"] >= stable_prob for r in rows)),
        "mean_edges_per_pag": float(np.mean(per_pag)),
        "stable_adjacencies": float(len({frozenset(pair) for pair in adjacent})),
    }


# --- the per-round client logic (shared by both backends) ---------------------------------


def client_round(
    received: dict[str, np.ndarray],
    round_index: int,
    total_rounds: int,
    matrix: np.ndarray,
    site_name: str,
    config: dict,
) -> tuple[dict[str, np.ndarray], int, dict[str, float]]:
    """What this site sends back in global round ``round_index`` (0-based), and with what weight.

    Returns ``(target, weight, metrics)``: ``target`` is the decoded state the site wants the
    global state to *become* (each transport turns it into a full array or a diff as its
    backend expects), ``weight`` the aggregation weight (its sample size in pooled mode, one
    vote in voting mode), and ``metrics`` the scalars to report for this round.
    """
    variables = list(config["VARIABLES"])
    mode = federation_mode(config)
    n = int(matrix.shape[0])
    if n < 5:
        raise ValueError(f"site {site_name!r} holds {n} complete rows; too few for a conditional-independence test")
    slot = site_slot(site_name)
    b_total = n_bootstrap_slabs(config)
    stable_prob = float(config.get("STABLE_EDGE_PROB", 0.5))
    target = {key: np.array(value, copy=True) for key, value in received.items()}
    target["pag"] = as_pags(received["pag"]).astype(np.float64)
    metrics: dict[str, float] = {"site_rows": float(n)}

    if mode == "pooled_statistics":
        if round_index == 0:
            target = empty_state(config)
            target["stats_mean"], target["stats_moment"] = site_statistics(matrix, config, slot)
            target["site_counts"][slot] = n * COUNT_SCALE
            assert_within_filter_bounds(target, n)
            return target, n, metrics
        pooled_n, sizes = recover_site_counts(received["site_counts"], weighted_by_size=True)
        metrics["pooled_rows"] = float(pooled_n)
        metrics["sites_seen"] = float(len(sizes))
        if not np.any(as_pags(received["pag"])):
            correlation = pooled_correlation(received["stats_mean"], received["stats_moment"])
            target["pag"] = np.stack(
                [fci_from_correlation(correlation[b], pooled_n, variables, config, b) for b in range(b_total)]
            ).astype(np.float64)
        metrics.update(summary_metrics(target["pag"], variables, stable_prob))
        assert_within_filter_bounds(target, n)
        return target, n, metrics

    # layer_voting: rounds 0..L-1 are layers of conditioning-set size 0..L-1, round L orients.
    n_layers = max(int(total_rounds) - 1, 1)
    ratio = float(config.get("VOTE_RATIO", 0.5))
    never = never_condition_indices(variables, config)
    if round_index == 0:
        skeleton = np.stack([complete_skeleton(variables, config)] * b_total)
        sepsets = np.zeros_like(received["sepsets"])
    else:
        skeleton = (received["skeleton"] >= ratio - 1e-9).astype(np.float64)
        sepsets = (
            received["sepsets"] > 0.5 / max(len(recover_site_counts(received["site_counts"], False)[1]), 1)
        ).astype(np.float64)
    target["site_counts"] = np.zeros_like(received["site_counts"])
    target["site_counts"][slot] = n * COUNT_SCALE
    if round_index < n_layers:
        seed = int(config.get("SEED", 0))
        out_skeleton = np.zeros_like(skeleton)
        out_sepsets = np.zeros_like(sepsets)
        for b in range(b_total):
            sample = matrix[bootstrap_rows(matrix, b, seed, slot, resampling_enabled(config))]
            out_skeleton[b], out_sepsets[b] = layer_step(sample, skeleton[b], sepsets[b], round_index, config, never)
        target["skeleton"], target["sepsets"] = out_skeleton, out_sepsets
        metrics["mean_edges_kept"] = float(np.mean([np.triu(s, 1).sum() for s in out_skeleton]))
        assert_within_filter_bounds(target, 1)
        return target, 1, metrics
    target["skeleton"], target["sepsets"] = skeleton, sepsets
    if not np.any(as_pags(received["pag"])):
        target["pag"] = np.stack(
            [pag_from_skeleton(skeleton[b], sepsets[b], variables, config, b) for b in range(b_total)]
        ).astype(np.float64)
    metrics.update(summary_metrics(target["pag"], variables, stable_prob))
    assert_within_filter_bounds(target, 1)
    return target, 1, metrics


def evaluate_state(received: dict[str, np.ndarray], config: dict) -> dict[str, float]:
    """The scalars a site reports when asked to evaluate the global state (the final PAGs)."""
    variables = list(config["VARIABLES"])
    stable_prob = float(config.get("STABLE_EDGE_PROB", 0.5))
    metrics = summary_metrics(received["pag"], variables, stable_prob)
    pooled_n, sizes = recover_site_counts(received["site_counts"], federation_mode(config) == "pooled_statistics")
    metrics["pooled_rows"] = float(pooled_n)
    metrics["sites_seen"] = float(len(sizes))
    return metrics
