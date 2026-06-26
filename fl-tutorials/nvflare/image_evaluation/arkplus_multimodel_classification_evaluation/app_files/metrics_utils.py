"""Evaluation metrics for chest X-ray model evaluation.

Provides AUROC computation, DeLong's test for paired AUROC comparison,
and label-mapping from pre-trained head outputs to target labels.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.stats import norm
from sklearn.metrics import roc_auc_score


def compute_auroc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Return AUROC, or NaN if only one class is present in y_true."""
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_score))


def _delong_covariance(
    y_true: np.ndarray,
    y_score_a: np.ndarray,
    y_score_b: np.ndarray,
) -> tuple[float, float, float, float, float]:
    """Compute the covariance matrices and ψ-based AUCs for DeLong's test.

    Returns (s10, s01, auc_a, auc_b, n_pos, n_neg):
        s10 — 2×2 sample covariance matrix of V10 between models A and B
        s01 — 2×2 sample covariance matrix of V01 between models A and B
        auc_a, auc_b — AUC computed via the ψ kernel (Mann-Whitney U-statistic)
        n_pos, n_neg — number of positive/negative samples (for decomposition)

    Based on DeLong, DeLong & Clarke-Pearson (1988) Biometrics 44:837-845.
    """
    n_pos = int(np.sum(y_true == 1))
    n_neg = int(np.sum(y_true == 0))

    # ── ψ kernel ─────────────────────────────────────────────────────
    # The Mann-Whitney AUC is the average of ψ over all (pos, neg) pairs:
    #
    #   ψ(x, y) = 1     if  x > y
    #             0.5   if  x == y
    #             0     otherwise   (x < y, contributes nothing)
    #
    # We compute it as  cmp + 0.5 * eq  where:
    #   cmp[j, k] = 1 if a_pos[j] >  a_neg[k], else 0
    #   eq[j, k]  = 1 if a_pos[j] == a_neg[k], else 0
    #
    # cmp_a / eq_a are [n_pos, n_neg] Boolean matrices for model A.
    # cmp_b / eq_b are the same for model B.
    #
    # ── Shape via broadcasting ───────────────────────────────────────
    #   a_pos[:, None]   → [n_pos, 1]   (column vector)
    #   a_neg[None, :]   → [1, n_neg]   (row vector)
    #
    # Broadcasting expands both to [n_pos, n_neg]:
    #   Row j  = positive sample j  compared against  all negatives.
    #   Col k  = all positives compared against  negative sample k.
    #
    # So  cmp_a[j, k]  answers:  "does positive j  >  negative k?"
    # ────────────────────────────────────────────────────────────────
    a_pos = y_score_a[y_true == 1]
    a_neg = y_score_a[y_true == 0]
    b_pos = y_score_b[y_true == 1]
    b_neg = y_score_b[y_true == 0]

    cmp_a = (a_pos[:, None] > a_neg[None, :]).astype(float)
    eq_a  = (a_pos[:, None] == a_neg[None, :]).astype(float)
    cmp_b = (b_pos[:, None] > b_neg[None, :]).astype(float)
    eq_b  = (b_pos[:, None] == b_neg[None, :]).astype(float)

    # ── V10 (positive compartment) ──────────────────────────────────
    # For each positive sample j, compute its mean ψ against all negatives:
    #
    #   V10_a[j] = mean_k ψ(a_pos[j], a_neg[k])
    #             = mean(cmp_a[j, :]) + 0.5 * mean(eq_a[j, :])
    #
    # The ψ-based AUC for model A is then  mean_j V10_a[j]. Axis=1 means it does it on a per-row basis.
    # ────────────────────────────────────────────────────────────────
    v10_a = np.mean(cmp_a, axis=1) + 0.5 * np.mean(eq_a, axis=1)  # [n_pos]
    v10_b = np.mean(cmp_b, axis=1) + 0.5 * np.mean(eq_b, axis=1)

    # ── V10 covariance (2×2) — sample variance (÷ n_pos-1) ────────
    # Manual cross-check (skipped if only one positive — cannot estimate variance):
    if n_pos > 1:
        mean_a = np.mean(v10_a); dev_a = v10_a - mean_a
        mean_b = np.mean(v10_b); dev_b = v10_b - mean_b
        s10_var_a  = np.sum(dev_a**2)      / (n_pos - 1)   # s10[0,0]
        s10_var_b  = np.sum(dev_b**2)      / (n_pos - 1)   # s10[1,1]
        s10_cov_ab = np.sum(dev_a * dev_b) / (n_pos - 1)   # s10[0,1]
    s10 = np.cov(v10_a, v10_b, ddof=1) if n_pos > 1 else np.zeros((2, 2))
    if n_pos > 1:
        assert np.allclose([s10_var_a, s10_var_b, s10_cov_ab],
                           [s10[0, 0], s10[1, 1], s10[0, 1]]), "V10 covariance mismatch"

    # ── V01 (negative compartment) ──────────────────────────────────
    # For each negative sample k, compute its mean ψ against all positives:
    #
    #   V01_a[k] = mean_j ψ(a_pos[j], a_neg[k])
    #             = mean(cmp_a[:, k]) + 0.5 * mean(eq_a[:, k])
    # ────────────────────────────────────────────────────────────────
    v01_a = np.mean(cmp_a, axis=0) + 0.5 * np.mean(eq_a, axis=0)  # [n_neg]
    v01_b = np.mean(cmp_b, axis=0) + 0.5 * np.mean(eq_b, axis=0)

    # ── V01 covariance (2×2) — sample variance (÷ n_neg-1) ────────
    if n_neg > 1:
        mean_a = np.mean(v01_a); dev_a = v01_a - mean_a
        mean_b = np.mean(v01_b); dev_b = v01_b - mean_b
        s01_var_a  = np.sum(dev_a**2)      / (n_neg - 1)   # s01[0,0]
        s01_var_b  = np.sum(dev_b**2)      / (n_neg - 1)   # s01[1,1]
        s01_cov_ab = np.sum(dev_a * dev_b) / (n_neg - 1)   # s01[0,1]
    s01 = np.cov(v01_a, v01_b, ddof=1) if n_neg > 1 else np.zeros((2, 2))
    if n_neg > 1:
        assert np.allclose([s01_var_a, s01_var_b, s01_cov_ab],
                           [s01[0, 0], s01[1, 1], s01[0, 1]]), "V01 covariance mismatch"

    # ── ψ-based AUC ────────────────────────────────────────────────
    # v10_a[j] is already  mean_k ψ(a_pos[j], a_neg[k])  —
    # the average ψ for positive j against all negatives.
    #
    # So  mean(v10_a)  =  (1/n_pos) * Σ_j  mean_k ψ(a_pos[j], a_neg[k])
    #                   =  (1/(n_pos·n_neg)) * Σ_j Σ_k ψ(a_pos[j], a_neg[k])
    #
    # which is the exact Mann-Whitney AUC.  No recomputation needed:
    # we just re-average the per-positive averages.
    # ────────────────────────────────────────────────────────────────
    auc_a = float(np.mean(v10_a))
    auc_b = float(np.mean(v10_b))

    return s10, s01, auc_a, auc_b, n_pos, n_neg


def delong_roc_test(
    y_true: np.ndarray,
    y_score_a: np.ndarray,
    y_score_b: np.ndarray,
) -> dict[str, float]:
    """DeLong's test comparing two correlated ROC curves.

    AUCs are computed via the ψ kernel (Mann-Whitney U-statistic)
    for consistency with the test's internal covariance structure.

    Returns dict with keys: auc_a, auc_b, statistic (z), pvalue (two-sided).
    """
    if len(np.unique(y_true)) < 2:
        return {
            "auc_a": float("nan"), "auc_b": float("nan"),
            "statistic": float("nan"), "pvalue": float("nan"),
        }

    s10, s01, auc_a, auc_b, n_pos, n_neg = _delong_covariance(
        y_true, y_score_a, y_score_b
    )

    # DeLong variance decomposition (eq. 5)
    # Var(AUC_a - AUC_b) = L^T (s10/n_pos + s01/n_neg) L,  L = [1, -1]^T
    L = np.array([[1], [-1]])
    cov = s10 / n_pos + s01 / n_neg
    var_diff = float((L.T @ cov @ L)[0, 0])

    se = math.sqrt(var_diff) if var_diff > 0 else 0.0

    if se == 0.0:
        return {"auc_a": auc_a, "auc_b": auc_b, "statistic": 0.0, "pvalue": 1.0}

    z = (auc_a - auc_b) / se
    p = 2.0 * norm.sf(abs(z))

    return {
        "auc_a": auc_a,
        "auc_b": auc_b,
        "statistic": float(z),
        "pvalue": float(p),
    }


def apply_label_mapping(
    preds: np.ndarray,
    source_labels: dict[str, int],
    mapping: dict[str, tuple[str, str | list[str]]],
    decaf_labels: list[str],
) -> dict[str, np.ndarray]:
    """Map pre-trained head outputs to target labels using a registry entry.

    Args:
        preds: [num_samples, N] array of sigmoid predictions.
        source_labels: Dict mapping source label name → column index.
        mapping: Dict mapping target label name → (method, source).
        decaf_labels: Ordered list of target label names to extract.

    Returns:
        Dict mapping each target label -> [num_samples] prediction array.
    """
    result: dict[str, np.ndarray] = {}
    for decaf_label in decaf_labels:
        if decaf_label not in mapping:
            raise KeyError(
                f"Target label {decaf_label!r} has no entry in the mapping. "
                f"Available keys: {list(mapping.keys())}"
            )
        method, source = mapping[decaf_label]
        if method == "direct":
            if not isinstance(source, str):
                raise TypeError(
                    f"Expected str source for 'direct' mapping of {decaf_label!r}, "
                    f"got {type(source).__name__}"
                )
            if source not in source_labels:
                raise KeyError(
                    f"Source label {source!r} for target {decaf_label!r} "
                    f"not found in source_labels. "
                    f"Available: {list(source_labels.keys())}"
                )
            result[decaf_label] = preds[..., source_labels[source]]
        elif method == "max":
            if not isinstance(source, list):
                raise TypeError(
                    f"Expected list source for 'max' mapping of {decaf_label!r}, "
                    f"got {type(source).__name__}"
                )
            indices = []
            for src in source:
                if src not in source_labels:
                    raise KeyError(
                        f"Source label {src!r} for target {decaf_label!r} "
                        f"not found in source_labels. "
                        f"Available: {list(source_labels.keys())}"
                    )
                indices.append(source_labels[src])
            result[decaf_label] = np.maximum.reduce(
                [preds[..., i] for i in indices]
            )
        else:
            raise ValueError(
                f"Unknown mapping method {method!r} for target {decaf_label!r}. "
                f"Expected 'direct' or 'max'."
            )
    return result
