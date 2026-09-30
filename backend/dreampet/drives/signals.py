"""The inputs to satisfaction s(e) = w_kind × novelty × learnability × habituation, and the
learning-progress estimator used for topic choice."""

from __future__ import annotations

import math
from collections.abc import Sequence


def knn_novelty(dists_today: Sequence[float], dists_all: Sequence[float], alpha: float) -> float:
    """novelty = α × mean kNN distance to today's memories + (1 − α) × to all memories.

    Distances are cosine distances (0 = identical). An empty set counts as fully novel.
    """

    def mean_or_one(d: Sequence[float]) -> float:
        return min(1.0, sum(d) / len(d)) if d else 1.0

    return max(0.0, min(1.0, alpha * mean_or_one(dists_today) + (1 - alpha) * mean_or_one(dists_all)))


def learning_progress(errors: Sequence[float], window: int) -> float:
    """LP = mean error of the older half of the window − mean error of the newer half.

    Mastered topics (flat low error) and noise (flat high error) both give LP ≈ 0.
    """
    w = list(errors)[-window:]
    if len(w) < 2:
        return 0.0
    half = len(w) // 2
    older, newer = w[: len(w) - half], w[len(w) - half:]
    return sum(older) / len(older) - sum(newer) / len(newer)


def lp_with_prior(errors: Sequence[float], window: int, prior: float = 0.08) -> float:
    """Unexplored clusters get an optimistic prior so they get tried at least a few times."""
    n = len(errors)
    if n >= 4:
        return learning_progress(errors, window)
    # blend toward the observed value as evidence accumulates
    observed = learning_progress(errors, window) if n >= 2 else 0.0
    return prior * (1 - n / 4) + observed * (n / 4)


def learnability(error: float, lp: float, centre: float = 0.45, width: float = 0.25) -> float:
    """Predict-then-read reward: moderate error (a Gaussian bump around `centre`), scaled up
    when the cluster's error is falling (LP > 0)."""
    moderate = math.exp(-(((error - centre) / width) ** 2))
    falling = max(0.25, min(1.0, 0.6 + 4.0 * lp))
    return moderate * falling


def habituation(h: float, n: int) -> float:
    """h^n, where n = reads in the same cluster in the current window."""
    return h ** max(0, n)


def softmax_sample(scores: Sequence[float], tau: float, u: float) -> int:
    """Pick an index with probability softmax(scores / tau), using a uniform draw u ∈ [0,1)."""
    if not scores:
        raise ValueError("no scores")
    m = max(scores)
    weights = [math.exp((s - m) / max(tau, 1e-6)) for s in scores]
    total = sum(weights)
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w / total
        if u < acc:
            return i
    return len(scores) - 1


def softmax_scale(lps: Sequence[float]) -> list[float]:
    """LP values are small (≈ ±0.2); rescale to a unit range so tau behaves the same
    whether errors are big or small."""
    if not lps:
        return []
    span = max(lps) - min(lps)
    if span < 1e-9:
        return [0.0 for _ in lps]
    return [(x - min(lps)) / span for x in lps]
