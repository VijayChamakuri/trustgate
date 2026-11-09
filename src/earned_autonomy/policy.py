"""Expense-policy agent: cited decisions plus a learned verifier that estimates P(correct).

Design (mirrors how Ramp describes its policy agent):
  1. A deterministic policy engine maps governed features + extracted Signals to one of
     APPROVE / REJECT / NEEDS_REVIEW, citing the policy sections that fired. No numeric
     "confidence" is shown to the user; the decision and the citation are the product.
  2. A verifier model, trained on historical reviewer outcomes, estimates the probability
     that the engine's decision is right for this kind of transaction.
  3. The autonomy gate (autonomy.py) decides, per action, which verifier score is high
     enough to act without a human, using audited gold data, not reviewer clicks.
"""

from __future__ import annotations

import re
from dataclasses import asdict

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier

from .extract import Signals

PROHIBITED_MCC = {"7995", "6011", "6540", "7997", "4899", "5815"}
SOFTWARE_MCC = {"7372", "5734", "4816"}
TRAVEL_KINDS = {"air", "hotel", "ground"}
AUTO_BILLED_KINDS = {"saas", "cloud", "ads"}
CAPS = {"meal_client_pp": 75.0, "meal_team_pp": 30.0, "lodging": 350.0, "lodging_high": 450.0, "receipt": 75.0, "new_sw": 500.0}
HIGH_COST = re.compile(r"\b(new york|san fran|nyc|sfo|jfk)\b", re.I)
TRAVEL_MEMO = re.compile(r"\b(travel|trip|traveling|airport|hotel|visit)\b")

SECTIONS = ["2.1", "2.2", "3.1", "3.2", "4.1", "5.1", "5.2", "6.1", "6.2", "7.1", "7.2", "8.1", "9.1"]
REJECT_SECTIONS = {"2.2", "5.1", "5.2", "7.1"}


def is_auto_billed(row: dict) -> bool:
    return row.get("merchant_kind") in AUTO_BILLED_KINDS or (
        row.get("resolution_action") != "MATCH" and str(row.get("mcc")) in SOFTWARE_MCC
    )


def decide(row: dict, s: Signals) -> tuple[str, list[str], str]:
    """Apply the written policy. Returns (decision, cited sections, rationale)."""
    amt = float(row["amount"])
    kind = row.get("merchant_kind")
    mcc = str(row.get("mcc"))
    memo = row.get("memo") or ""
    auto = is_auto_billed(row)
    travel = kind in TRAVEL_KINDS or bool(TRAVEL_MEMO.search(memo))
    hits: dict[str, str] = {}

    if kind == "personal" or mcc in PROHIBITED_MCC:
        hits["5.1"] = f"prohibited merchant/MCC {mcc}"
    if s.personal_hint:
        hits["5.2"] = f"memo indicates personal purchase: '{memo}'"
    if row.get("is_possible_split"):
        hits["7.1"] = (
            f"{row['same_day_merchant_count']} same-day charges totalling ${row['same_day_merchant_total']:.2f} > limit ${row['single_txn_limit']:.0f}"
        )
    if s.has_alcohol and s.meal_type != "client":
        hits["2.2"] = "alcohol on a non-client meal"
    if row.get("is_possible_duplicate"):
        hits["9.1"] = "same card, merchant and amount within 3 days"
    if s.premium_cabin:
        hits["3.1"] = "premium cabin airfare"
    if s.nightly_rate is not None:
        cap = CAPS["lodging_high"] if HIGH_COST.search(row.get("descriptor_clean", "") + " " + memo) else CAPS["lodging"]
        if s.nightly_rate > cap:
            hits["3.2"] = f"nightly rate ${s.nightly_rate:.0f} > ${cap:.0f} cap"
    if s.meal_type in ("client", "team"):
        cap = CAPS["meal_client_pp"] if s.meal_type == "client" else CAPS["meal_team_pp"]
        if s.attendees:
            if amt / s.attendees > cap:
                hits["2.1"] = f"${amt / s.attendees:.0f}/person > ${cap:.0f} {s.meal_type} cap"
        elif amt > cap:
            hits["2.1"] = f"{s.meal_type} meal over ${cap:.0f} with no attendee count"
    if amt >= CAPS["receipt"] and not row.get("has_receipt") and not auto:
        hits["6.1"] = f"no receipt for ${amt:.2f}"
    gap = row.get("receipt_gap_ratio")
    if gap is not None and not pd.isna(gap):
        allowed = 0.25 if (s.meal_type or kind == "meal") else 0.10
        if gap > allowed or gap < -0.10:
            hits["6.2"] = f"receipt differs from charge by {gap:.0%}"
    if row.get("resolution_action") != "MATCH" and mcc in SOFTWARE_MCC and amt > CAPS["new_sw"]:
        hits["4.1"] = "unregistered software vendor over $500"
    if row.get("is_over_single_limit") and not auto and not travel:
        hits["7.2"] = f"${amt:.2f} over single-transaction limit"
    if row.get("is_weekend") and not travel and not auto and "5.1" not in hits and "5.2" not in hits:
        hits["8.1"] = "weekend non-travel spend"

    sections = sorted(hits)
    if any(h in REJECT_SECTIONS for h in sections):
        decision = "REJECT"
    elif sections:
        decision = "NEEDS_REVIEW"
    else:
        decision = "APPROVE"
    rationale = "; ".join(f"§{k} {v}" for k, v in sorted(hits.items())) or "within policy"
    return decision, sections, rationale


# ---------------------------------------------------------------------------- verifier
KIND_CODES = {
    k: i
    for i, k in enumerate(
        ["saas", "cloud", "air", "hotel", "ground", "meal", "office", "ads", "ship", "training", "services", "personal"]
    )
}
MEAL_CODES = {None: 0, "solo": 1, "team": 2, "client": 3}
DEC_CODES = {"APPROVE": 0, "NEEDS_REVIEW": 1, "REJECT": 2}


def verifier_features(df: pd.DataFrame, sigs: list[Signals], decisions: list[str], sections: list[list[str]]) -> pd.DataFrame:
    sd = pd.DataFrame([asdict(s) for s in sigs], index=df.index)
    X = pd.DataFrame(index=df.index)
    X["decision"] = [DEC_CODES[d] for d in decisions]
    for sec in SECTIONS:
        X[f"sec_{sec}"] = [sec in s for s in sections]
    X["n_sections"] = [len(s) for s in sections]
    X["log_amount"] = np.log1p(df.amount.astype(float))
    X["kind"] = df.merchant_kind.map(KIND_CODES).fillna(-1)
    X["mcc2"] = pd.to_numeric(df.mcc.str[:2], errors="coerce").fillna(-1)
    X["resolution"] = df.resolution_action.map({"MATCH": 0, "NEW": 1, "ESCALATE": 2}).fillna(3)
    X["resolution_score"] = df.resolution_score.astype(float).fillna(0)
    X["has_receipt"] = df.has_receipt.astype(bool)
    X["receipt_gap"] = df.receipt_gap_ratio.astype(float).fillna(-9)
    X["receipt_len"] = df.receipt_text.fillna("").str.len()
    X["memo_len"] = df.memo.fillna("").str.len()
    X["is_weekend"] = df.is_weekend.astype(bool)
    X["meal_type"] = sd.meal_type.map(MEAL_CODES).fillna(0)
    X["attendees"] = sd.attendees.astype(float).fillna(-1)
    X["per_person"] = np.where(sd.attendees.fillna(0) > 0, df.amount.astype(float) / sd.attendees.fillna(1).clip(lower=1), -1)
    X["has_alcohol"] = sd.has_alcohol.astype(bool)
    X["mcc_bar"] = df.mcc.eq("5813")
    X["nightly_known"] = sd.nightly_rate.notna()
    X["is_hotel"] = df.merchant_kind.eq("hotel")
    X["amount_vs_limit"] = df.amount.astype(float) / df.single_txn_limit.astype(float)
    X["is_traveler"] = df.is_traveler.astype(bool)
    X["is_manager"] = df.is_manager.astype(bool)
    return X.astype(float)


class Verifier:
    """P(engine decision is correct | transaction). Isotonic-calibrated gradient boosting."""

    def __init__(self, seed: int = 0):
        base = HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.06, max_leaf_nodes=24, l2_regularization=1.0, random_state=seed
        )
        self.model = CalibratedClassifierCV(base, method="isotonic", cv=3)

    def fit(self, X: pd.DataFrame, correct: np.ndarray) -> Verifier:
        self.model.fit(X, correct.astype(int))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X)[:, 1]
