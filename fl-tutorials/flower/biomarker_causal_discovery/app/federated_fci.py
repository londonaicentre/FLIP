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
    Each site sends the sufficient statistics of Fisher's z test — ONE mean vector and ONE
    second-moment matrix of its (log-transformed) variables over all its rows — weighted by its
    sample size. The weighted mean of those *is* the pooled cohort's statistics, so the
    correlation matrix every site receives back is exactly the one a centralised run would
    compute, and FCI on it (round 2, run identically at every site) is exactly centralised
    FCI with the Fisher-z test. This is the pooled-test idea of FedC²SL (Wang et al., 2023)
    specialised to the Gaussian case, where the test needs nothing but second moments — without
    that paper's secure aggregation or random-projection encoding, which FLIP does not provide.
    Two rounds; one exchange of ``p + p × p`` numbers per site; no raw rows leave a site.
    In round 2 the statistics are zeroed in what a site sends back, so the final model carries
    only the graphs and the row counts.

``layer_voting``
    The FedPC scheme (Huang et al., 2023): one global round per conditioning-set size. Each
    site runs that layer's CI tests on its own data against the current global skeleton and
    sends back a 0/1 adjacency matrix plus 0/1 separating-set membership; the server's plain
    average is the share of sites that kept each edge, which the sites threshold at
    ``VOTE_RATIO`` next round (FedPC's "vote"), and any site's separating set survives (the
    union). The last round orients the voted skeleton with FCI's rules. Works with any local
    CI test (``CI_TEST: "kci"`` included), at the cost of exactness: a vote is not a pooled
    test, and FedPC's own paper discusses the inconsistent-separating-set problem it creates.

Bootstrapping differs by mode, and the difference is a privacy decision. In pooled mode the
``B`` resamples are drawn *after* pooling, from the pooled correlation itself (a parametric
bootstrap: Wishart draws of the sample covariance under the Gaussian model Fisher's z assumes,
re-standardised, with a stream shared by every site), so a site never sends more than one
statistic of its rows. Sending one mean and one moment matrix per bootstrap resample instead would
hand out ``B`` different weighted sums of the same rows with publicly reproducible weights — a
linear system a reviewer showed can be solved back to the rows for cohorts of a few hundred. In
voting mode (0/1 decisions, no statistics) bootstrap ``b`` at site ``s`` still resamples that
site's own rows with the stream ``(SEED, s, b)``. Everything is computed for all ``B`` bootstraps
at once (the graph arrays carry a leading bootstrap axis), so the number of rounds does not
depend on ``B``.

Where the pieces come from (full references in the README):

* Pooling the Gaussian sufficient statistics of horizontally partitioned data and running the
  analysis on the pooled ones is the DataSHIELD / secure-regression pattern (Karr, Lin, Sanil &
  Reiter, JCGS 2005; Gaye et al., IJE 2014); FedC²SL (Wang, Ma & Wang, ECML PKDD 2023) is the
  same move for the conditional-independence test inside PC/FCI, with secure aggregation.
* Fisher's z on partial correlations from the correlation matrix, with ``n − |S| − 3`` degrees
  of freedom, is the PC-algorithm test of Kalisch & Bühlmann (JMLR 2007); FCI and its orientation
  rules are Spirtes, Glymour & Scheines (2000) and Zhang (AI 2008), as implemented in causal-learn.
* Bootstrap edge frequencies as confidence in a learned structure: Friedman, Goldszmidt & Wyner
  (UAI 1999). The parametric bootstrap from a fitted Gaussian: Efron & Tibshirani (1993, §6.5);
  the sample covariance of ``n`` Gaussian rows is Wishart with ``n − 1`` degrees of freedom.
* The layer-wise skeleton search is PC-stable (Colombo & Maathuis, JMLR 2014) voted across sites
  as in FedPC (Huang et al., IEEE TBD 2023).
* Why per-resample statistics were withdrawn: many released linear functions of the same rows
  reconstruct them (Dinur & Nissim, PODS 2003). Formal protection for the one covariance that is
  released would be the Gaussian mechanism on it (Analyze Gauss: Dwork, Talwar, Thakurta & Zhang,
  STOC 2014) or the Wishart mechanism (Jiang, Xie & Zhang, AAAI 2016) — follow-up work.

Two platform details shape the wire format:

* The NVFLARE standard template applies a percentile-privacy filter to every training result:
  it zeroes the smallest 10 % of a site's update (by absolute value, across all arrays) and
  clips the rest to ±0.01 per local step. Both are no-ops here by construction: arrays are
  sent multiplied by :data:`ENCODING_SCALE` (so no entry reaches the clip), and a zero
  ``padding`` array a quarter the size of the rest guarantees the 10th percentile is exactly
  zero (so nothing non-zero is zeroed). The Flower local-DP mod is off in config.toml: its
  per-update clip-and-noise is calibrated for weight deltas, not for a moment matrix whose
  entries the federation adds exactly; a calibrated Gaussian mechanism on the single covariance
  (Analyze Gauss, Dwork et al. 2014) is the right tool for a formal guarantee and is left as
  follow-up work — see the README's privacy section for what is and is not protected.
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
import logging
import math
import os
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

try:  # the trust's NVFLARE site policy (percentile / gamma); stdlib-only module, absent on a bare Flower image
    from flip.nvflare.site_policy import resolve_policy
except ImportError:  # pragma: no cover - exercised where flip-utils is not importable
    resolve_policy = None

logger = logging.getLogger(__name__)

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
#: No site releases its statistics for fewer complete rows than this, whatever the researcher's
#: config.json says (``MIN_SITE_ROWS`` there can only raise it). A trust raises it further through
#: :data:`SITE_FLOOR_VARS` in the FL client's environment. With 11 numbers per row and p + p² = 132
#: released, a floor of 30 keeps the released system well under-determined; the small-cell rule
#: below covers the discrete columns, where a moment is an exact count.
HARD_MIN_SITE_ROWS = 30
#: Environment variables (the trust's FL client environment, which the researcher cannot edit) that
#: raise the row floor; the first one set wins. COHORT_QUERY_THRESHOLD is the trust's disclosure floor
#: for cohort egress; it is read here when the trust forwards it to the FL client.
SITE_FLOOR_VARS = ("FL_SITE_MIN_ROWS", "COHORT_QUERY_THRESHOLD")
#: A discrete column (three or fewer distinct values: sex, an allele count) releases, through its
#: moments, the exact count of every level and the sum of every other variable over that level. A
#: level with fewer rows than this is a small cell in the statistical-disclosure-control sense, and
#: the site refuses to release rather than publish it. ``MIN_CELL_COUNT`` in config.json can only raise it.
MIN_CELL_COUNT = 5
#: ``layer_voting`` is experimental (its leakage is unanalysed: per-bootstrap 0/1 test outcomes on
#: resamples whose indices are public). Off the simulator it runs only when the trust sets this in
#: the FL client's environment — a researcher's config.json cannot switch it on.
LAYER_VOTING_OPT_IN_VAR = "FLIP_CAUSAL_ALLOW_LAYER_VOTING"
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
    """Shape of every array in the wire format, from the variable count and bootstrap count.

    The statistics carry no bootstrap axis on purpose (see the module docstring): one mean
    vector and one second-moment matrix per site, whatever ``N_BOOTSTRAPS`` says.
    """
    p = len(config["VARIABLES"])
    b = n_bootstrap_slabs(config)
    core = {
        "site_counts": (MAX_SITES,),
        "stats_mean": (p,),
        "stats_moment": (p, p),
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


def effective_percentile_clip(env: dict | None = None) -> float:
    """The clip the trust's NVFLARE site policy actually applies: the stock gamma, or a stricter one.

    A trust may lower ``gamma`` through ``FL_SITE_PRIVACY_GAMMA`` or its governance document, and
    the stock filter clips silently, so the bound checked below must be the site's, not the default.
    """
    if resolve_policy is None:
        return PERCENTILE_CLIP
    try:
        policy, _ = resolve_policy(os.environ if env is None else env)
    except Exception as error:  # a malformed policy stops the container elsewhere; here, stay strict
        logger.warning("could not resolve the site privacy policy (%s); assuming the stock clip", error)
        return PERCENTILE_CLIP
    return PERCENTILE_CLIP if policy is None else min(PERCENTILE_CLIP, float(policy.gamma))


def assert_within_filter_bounds(target: dict[str, np.ndarray], weight: int) -> None:
    """Refuse a payload the platform's percentile-privacy clip would truncate.

    The filter divides a site's update by its weight and clips at ±:data:`PERCENTILE_CLIP`;
    an entry beyond that is corrupted silently and the run goes on to report a wrong graph.
    A variable in its raw units (a volume in mm³) overshoots by orders of magnitude, which is
    what ``LOG_TRANSFORM`` and ``SCALE`` in config.json exist for; this check turns a forgotten
    one into an error that names it.
    """
    limit = effective_percentile_clip() * max(int(weight), 1) / ENCODING_SCALE
    for key, array in target.items():
        largest = float(np.max(np.abs(array))) if array.size else 0.0
        if largest > limit:
            raise ValueError(
                f"{key} holds a value of {largest:.3g}, above the {limit:.3g} the platform's privacy filter "
                "would clip at: scale the variable down (config.json SCALE) or log-transform it (LOG_TRANSFORM)"
            )


# --- what a site may release ---------------------------------------------------------------


def site_row_floor(config: dict, env: dict | None = None) -> int:
    """Fewest complete rows a site releases statistics for: the hard floor, raised by config or the trust."""
    env = os.environ if env is None else env
    floor = max(HARD_MIN_SITE_ROWS, int(config.get("MIN_SITE_ROWS", 0)))
    for var in SITE_FLOOR_VARS:
        raw = env.get(var)
        if raw:
            with contextlib.suppress(ValueError):
                floor = max(floor, int(float(raw)))
            break
    return floor


def small_cells(matrix: np.ndarray, config: dict) -> list[int]:
    """Column indices whose discrete levels include a cell smaller than the minimum cell count.

    A column with three or fewer distinct values is treated as discrete. Its mean and its moments
    with every other column are exact level counts and per-level sums, so a level held by very few
    rows is published as such; those columns make the site refuse to release.
    """
    minimum = max(MIN_CELL_COUNT, int(config.get("MIN_CELL_COUNT", 0)))
    offenders = []
    for j in range(matrix.shape[1]):
        values, counts = np.unique(matrix[:, j], return_counts=True)
        if len(values) <= 3 and np.any(counts < minimum):
            offenders.append(j)
    return offenders


def layer_voting_allowed(local_dev: bool, env: dict | None = None) -> bool:
    """Voting mode runs on the simulator, or where the trust opted in through its own environment."""
    env = os.environ if env is None else env
    return bool(local_dev) or env.get(LAYER_VOTING_OPT_IN_VAR, "").strip() in {"1", "true", "yes"}


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
        if np.all(values[np.isfinite(values)] <= 0):
            raise ValueError(f"{column} is listed in LOG_TRANSFORM but has no positive values")
        # A stray non-positive assay value (a below-detection 0, an unmasked missing code) drops that
        # row with the other non-finite ones below, rather than taking the whole site down.
        values = np.where(values > 0, values, np.nan)
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


def site_statistics(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The one mean vector and one second-moment matrix of this site's rows that leave it."""
    return matrix.mean(axis=0), matrix.T @ matrix / matrix.shape[0]


def pooled_correlation(mean: np.ndarray, moment: np.ndarray) -> np.ndarray:
    """The correlation matrix from a size-weighted mean vector and second-moment matrix."""
    cov = moment - np.outer(mean, mean)
    scale = np.sqrt(np.clip(np.diag(cov), 1e-300, None))
    return np.clip(cov / np.outer(scale, scale), -1.0, 1.0)


def bootstrap_correlations(mean: np.ndarray, moment: np.ndarray, sample_size: int, config: dict) -> np.ndarray:
    """``B`` correlation matrices resampled from the pooled statistics — the parametric bootstrap.

    The parametric bootstrap of Efron & Tibshirani (1993, §6.5), with the fitted model the
    Gaussian one Fisher's z assumes: the sample covariance of ``n`` rows is then Wishart with
    ``n − 1`` degrees of freedom around the true covariance. Drawing from that law with the
    pooled correlation as the truth, then re-standardising, gives the spread of correlation
    matrices a resample of the pooled rows would have shown — without any site sending per-resample
    statistics. The stream is shared by every site (no site term), so the sites agree on every
    PAG. ``N_BOOTSTRAPS`` of 0 returns the pooled correlation itself, once.
    """
    from scipy.stats import wishart

    corr = pooled_correlation(mean, moment)
    if not resampling_enabled(config):
        return corr[None]
    p = corr.shape[0]
    df = max(int(sample_size) - 1, p + 1)
    # A pooled correlation can sit on the boundary of positive definiteness (a float32 round trip,
    # a near-collinear pair); nudge it inside so the Wishart law is defined.
    eigenvalues, eigenvectors = np.linalg.eigh(corr)
    floor = 1e-6
    if eigenvalues.min() < floor:
        corr = (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T
    rng = np.random.default_rng([int(config.get("SEED", 0)), 104729])
    draws = wishart(df=df, scale=corr / df).rvs(size=n_bootstrap_slabs(config), random_state=rng)
    draws = np.asarray(draws, dtype=np.float64).reshape(-1, p, p)
    scale = np.sqrt(np.einsum("bii->bi", draws))
    return np.clip(draws / (scale[:, :, None] * scale[:, None, :]), -1.0, 1.0)


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

    The partial correlation comes from the inverse of the sub-correlation matrix and the
    statistic is ``√(n − |S| − 3) · atanh(r)``, as in Kalisch & Bühlmann (JMLR 2007) — the same
    test causal-learn's ``fisherz`` computes from rows (pinned by the tests).

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
            a, c = position[i], position[j]
            if skeleton[i, j] > 0:
                graph.add_edge(Edge(nodes[a], nodes[c], Endpoint.CIRCLE, Endpoint.CIRCLE))
            elif (a, c) in forbidden and (c, a) in forbidden:
                # Never adjacent because the knowledge forbids both directions, not because a test
                # separated them: no separating set, so rule 0 does not read an (absent) independence
                # into a collider at their common neighbours — the same as fas() in pooled mode.
                continue
            else:
                members = {position[int(k)] for k in np.flatnonzero(sepsets[i, j] > 0)}
                sep_sets[(a, c)] = members
                sep_sets[(c, a)] = members
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
    """How often each (source, mark, target) edge appears across the bootstrap PAGs.

    Bootstrap frequency as confidence in a learned structural feature: Friedman, Goldszmidt &
    Wyner (UAI 1999). It is a stability measure under resampling, not a posterior probability.
    """
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
    local_dev: bool = True,
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
    # Refusals below carry no count: both backends forward a client's exception text to the hub, and a
    # below-floor count is exactly what the platform's cohort threshold keeps indistinguishable from
    # empty. The count goes to the site's own log only.
    if n < 5:
        logger.info("site %s: %d complete rows, too few for any conditional-independence test", site_name, n)
        raise ValueError(f"site {site_name!r} holds too few complete rows for a conditional-independence test")
    if mode == "layer_voting" and not layer_voting_allowed(local_dev):
        raise ValueError(
            "layer_voting is experimental (its leakage is unanalysed) and runs only on the simulator, or where "
            f"the trust sets {LAYER_VOTING_OPT_IN_VAR}=1 in its FL client environment; use pooled_statistics"
        )
    slot = site_slot(site_name)
    b_total = n_bootstrap_slabs(config)
    stable_prob = float(config.get("STABLE_EDGE_PROB", 0.5))
    target = {key: np.array(value, copy=True) for key, value in received.items()}
    target["pag"] = as_pags(received["pag"]).astype(np.float64)
    metrics: dict[str, float] = {"site_rows": float(n)}

    if mode == "pooled_statistics":
        if round_index == 0:
            floor = site_row_floor(config)
            if n < floor:
                logger.info("site %s: %d complete rows, below the release floor of %d", site_name, n, floor)
                raise ValueError(
                    f"site {site_name!r} holds too few complete rows to release its statistics (below the site "
                    "floor): a mean and second-moment matrix of that few rows is too close to the rows themselves"
                )
            cells = small_cells(matrix, config)
            if cells:
                names = ", ".join(variables[j] for j in cells)
                logger.info("site %s: small cells in discrete column(s) %s", site_name, names)
                raise ValueError(
                    f"site {site_name!r} refuses to release its statistics: a discrete variable ({names}) has a "
                    "level held by too few rows, and its moments would publish that cell"
                )
            target = empty_state(config)
            target["stats_mean"], target["stats_moment"] = site_statistics(matrix)
            target["site_counts"][slot] = n * COUNT_SCALE
            assert_within_filter_bounds(target, n)
            return target, n, metrics
        pooled_n, sizes = recover_site_counts(received["site_counts"], weighted_by_size=True)
        metrics["pooled_rows"] = float(pooled_n)
        metrics["sites_seen"] = float(len(sizes))
        if not np.any(as_pags(received["pag"])):
            correlations = bootstrap_correlations(received["stats_mean"], received["stats_moment"], pooled_n, config)
            target["pag"] = np.stack(
                [fci_from_correlation(correlations[b], pooled_n, variables, config, b) for b in range(b_total)]
            ).astype(np.float64)
        # The pooled statistics have done their job. They do not travel on: the final model the
        # researcher downloads holds the graphs and the row counts, nothing about the variables.
        target["stats_mean"][:] = 0.0
        target["stats_moment"][:] = 0.0
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
