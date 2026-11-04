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
"""Choose the fewest SAM prompt clicks that segment the spleen on one slice, before recording.

The demo shows an annotator building the spleen mask from nothing with sam_2d. Which clicks to show
is decided here, off camera, against the same server the viewer calls (SAM is deterministic, so the
viewer reproduces each result): the first click is always positive and deepest inside the spleen;
extra positive and negative clicks are searched, smallest set first, until the slice's Dice against
the reference passes the target. The reference is the session's ground-truth label when it has one,
otherwise segmentation_spleen's mask. Candidate clicks come only from segmentation_spleen's mask,
never from the ground truth, so the clicks are ones a recorder could know without it.
"""

import itertools
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

Point = tuple[int, int]  # (column, row) on the slice
Infer = Callable[[list[Point], list[Point]], np.ndarray]  # (positive, negative) -> boolean slice mask


@dataclass
class Click:
    point: Point
    positive: bool
    dice_after: float  # slice Dice against the reference once this click is added
    fixes_error: bool = True  # lands where the previous mask was wrong (a leak, or a miss), not just near an edge


def dice(a: np.ndarray, b: np.ndarray) -> float:
    return float(2 * (a & b).sum() / max(a.sum() + b.sum(), 1))


def deepest(mask: np.ndarray) -> Point:
    """The pixel farthest from the mask's edge: the least ambiguous place to click inside it."""
    distance = ndimage.distance_transform_edt(mask)
    row, col = np.unravel_index(distance.argmax(), distance.shape)
    return int(col), int(row)


def candidates(oracle: np.ndarray, first_result: np.ndarray) -> list[tuple[bool, Point]]:
    """Plausible extra clicks: negatives where the first guess leaked and around the organ, positives inside it."""
    negative: list[Point] = []
    leaks, count = ndimage.label(first_result & ~oracle)
    for label in np.argsort(np.bincount(leaks.ravel())[1:])[::-1][: min(3, count)]:
        negative.append(deepest(leaks == label + 1))
    ring = ndimage.binary_dilation(oracle, iterations=14) & ~ndimage.binary_dilation(oracle, iterations=10)
    rows, cols = np.nonzero(ring)
    centre_row, centre_col = np.nonzero(oracle)[0].mean(), np.nonzero(oracle)[1].mean()
    angle = np.arctan2(rows - centre_row, cols - centre_col)
    for target in np.linspace(-np.pi, np.pi, 8, endpoint=False):
        i = np.argmin(np.abs(np.angle(np.exp(1j * (angle - target)))))
        negative.append((int(cols[i]), int(rows[i])))
    positive: list[Point] = []
    inner = ndimage.binary_erosion(oracle, iterations=3)
    rows, cols = np.nonzero(inner)
    if rows.size:
        for half in (rows < np.median(rows), rows >= np.median(rows)):
            part = np.zeros_like(oracle)
            part[rows[half], cols[half]] = True
            if part.any():
                positive.append(deepest(part))
    return [(True, p) for p in dict.fromkeys(positive)] + [(False, p) for p in dict.fromkeys(negative)]


def choose(infer: Infer, oracle: np.ndarray, reference: np.ndarray, target: float = 0.95, max_extra: int = 3):
    """The fewest clicks reaching ``target`` Dice, in the order that tells the story best.

    Returns the clicks (each with the Dice after it) or raises when no set of up to 1 + max_extra clicks
    gets there. Extra clicks are ordered greedily: each next click is the one that helps most.
    """
    first = deepest(oracle)
    cache: dict = {}

    def run(extra: tuple[tuple[bool, Point], ...]) -> np.ndarray:
        key = tuple(sorted(extra))
        if key not in cache:
            positive = [first] + [p for is_positive, p in extra if is_positive]
            negative = [p for is_positive, p in extra if not is_positive]
            cache[key] = infer(positive, negative)
        return cache[key]

    def score(extra: tuple[tuple[bool, Point], ...]) -> float:
        return dice(run(extra), reference)

    pool = candidates(oracle, infer([first], []))
    for size in range(0, max_extra + 1):
        scored = [(score(combo), combo) for combo in itertools.combinations(pool, size)]
        best_dice, best = max(scored, key=lambda s: s[0])
        if best_dice > target:
            break
    else:
        raise RuntimeError(f"no set of up to {1 + max_extra} clicks reached Dice {target} (best {best_dice:.3f})")

    clicks = [Click(first, True, score(()))]
    remaining, chosen = list(best), ()
    while remaining:
        nxt = max(remaining, key=lambda c: score(chosen + (c,)))
        remaining.remove(nxt)
        (col, row), previous = nxt[1], run(chosen)
        fixes = (not previous[row, col]) if nxt[0] else bool(previous[row, col])
        chosen += (nxt,)
        clicks.append(Click(nxt[1], nxt[0], score(chosen), fixes))
    return clicks
