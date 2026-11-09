import numpy as np
import pandas as pd

from earned_autonomy.autonomy import choose_threshold, route, wilson_interval


def test_wilson_interval_known_values():
    lo, hi = wilson_interval(0, 100)
    assert lo == 0.0 and 0.035 < hi < 0.040  # rule of three territory
    lo, hi = wilson_interval(5, 100)
    assert 0.02 < lo < 0.03 and 0.10 < hi < 0.12
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_small_samples_never_earn_autonomy():
    conf = np.full(30, 0.99)
    correct = np.ones(30, bool)  # perfect, but only 30 examples
    assert choose_threshold(conf, correct, budget=0.01, min_n=50) is None


def test_wilson_bound_is_stricter_than_point_estimate():
    rng = np.random.default_rng(0)
    conf = rng.uniform(0.5, 1.0, 400)
    correct = rng.random(400) > (1 - conf) * 0.05  # errors concentrate at low confidence
    strict = choose_threshold(conf, correct, 0.01, 50, conservative=True)
    loose = choose_threshold(conf, correct, 0.01, 50, conservative=False)
    assert loose is not None
    assert strict is None or strict >= loose


def test_route_never_automates_needs_review_or_novel_or_locked_tiers():
    df = pd.DataFrame(
        {
            "decision": ["APPROVE", "NEEDS_REVIEW", "APPROVE", "APPROVE", "REJECT"],
            "confidence": [0.99, 0.99, 0.99, 0.99, 0.99],
            "amount": [50.0, 50.0, 50.0, 900.0, 50.0],
            "is_novel": [False, False, True, False, False],
        }
    )
    r = route(df, tau_approve=0.9, tau_reject=None, novelty_guard=True, tau_approve_high=None)
    assert r.tolist() == ["auto", "human", "human", "human", "human"]
