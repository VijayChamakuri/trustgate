import hashlib

import pandas as pd

from trustgate.generate import generate


def _digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def test_generation_is_deterministic_and_gold_is_separate():
    a, b = generate(3), generate(3)
    assert _digest(a["raw_card_transactions"]) == _digest(b["raw_card_transactions"])
    raw_cols = set(a["raw_card_transactions"].columns)
    assert not raw_cols & {"gold_decision", "gold_gl", "true_merchant_id", "has_alcohol", "fare"}
    gold = a["gold_transactions"]
    shares = gold.gold_decision.value_counts(normalize=True)
    assert 0.8 < shares["APPROVE"] < 0.95 and shares["REJECT"] > 0.01
    assert set(gold.txn_id) == set(a["raw_card_transactions"].txn_id)


def test_reviewer_history_stops_before_shadow_ends():
    d = generate(3)
    hist_ids = set(d["raw_historical_reviews"].txn_id)
    gold = d["gold_transactions"]
    assert gold[gold.txn_id.isin(hist_ids)].month_index.max() <= 7
