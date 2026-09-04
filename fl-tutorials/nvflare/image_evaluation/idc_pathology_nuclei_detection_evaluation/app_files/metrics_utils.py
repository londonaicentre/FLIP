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

"""Detection metrics derived from TP/FP/FN counts, and their federated aggregation.

Two rules drive this module, both of which the tutorial README explains:

* Zero denominators are explicit. A site that predicts nothing has undefined precision, not 0.0 —
  reporting 0.0 would silently drag a federated average down.
* Federated metrics are computed from **pooled counts**, never by averaging per-site F1. Averaging
  weights a site with 200 nuclei the same as one with 20,000; pooling weights by evidence. Both are
  reported, because the gap between them *is* the cross-site heterogeneity the tutorial is about.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from matching import MatchCounts


def _ratio(numerator: int, denominator: int) -> float | None:
    """Return numerator / denominator, or None when the denominator is zero."""
    return numerator / denominator if denominator else None


def detection_metrics(counts: MatchCounts) -> dict[str, float | int | None]:
    """Compute precision, recall and F1 from one set of counts.

    Returns:
        A mapping with the raw counts alongside precision, recall and f1. Each derived
        value is None where its denominator is zero — including F1, which is undefined when
        precision and recall are both undefined or both zero.
    """
    precision = _ratio(counts.tp, counts.n_predictions)
    recall = _ratio(counts.tp, counts.n_references)
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": counts.tp,
        "fp": counts.fp,
        "fn": counts.fn,
        "n_predictions": counts.n_predictions,
        "n_references": counts.n_references,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def pool_counts(per_site: Mapping[str, MatchCounts]) -> MatchCounts:
    """Sum every site's counts into one federated confusion matrix."""
    pooled = MatchCounts()
    for counts in per_site.values():
        pooled = pooled + counts
    return pooled


def _mean(values: Iterable[float | None]) -> float | None:
    """Unweighted mean over the values that are defined, or None if none are."""
    defined = [v for v in values if v is not None]
    return sum(defined) / len(defined) if defined else None


def federated_summary(per_site: Mapping[str, MatchCounts]) -> dict[str, object]:
    """Build the federation-wide result from each site's counts.

    pooled metrics come from the summed confusion matrix and are the headline numbers.
    macro_site_* are unweighted means across sites and are reported separately, never conflated
    with the pooled figures. The best/worst/gap triple is the tutorial's actual point: it quantifies
    how differently one fixed model behaves across institutions.
    """
    site_metrics = {site: detection_metrics(counts) for site, counts in per_site.items()}
    pooled = detection_metrics(pool_counts(per_site))

    f1_by_site = {site: metrics["f1"] for site, metrics in site_metrics.items() if metrics["f1"] is not None}
    summary: dict[str, object] = {
        "sites": site_metrics,
        "pooled": pooled,
        "macro_site_precision": _mean(m["precision"] for m in site_metrics.values()),
        "macro_site_recall": _mean(m["recall"] for m in site_metrics.values()),
        "macro_site_f1": _mean(m["f1"] for m in site_metrics.values()),
    }
    if f1_by_site:
        best_site = max(f1_by_site, key=lambda s: f1_by_site[s])
        worst_site = min(f1_by_site, key=lambda s: f1_by_site[s])
        summary.update(
            {
                "best_site": best_site,
                "best_site_f1": f1_by_site[best_site],
                "worst_site": worst_site,
                "worst_site_f1": f1_by_site[worst_site],
                "site_f1_gap": f1_by_site[best_site] - f1_by_site[worst_site],
            }
        )
    return summary
