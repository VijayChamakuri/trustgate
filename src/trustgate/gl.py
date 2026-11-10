"""GL coding: predict the general-ledger account for each transaction, with calibrated confidence.

Merchant -> default GL is right most of the time, but not for ambiguous merchants:
Amazon can be office supplies, training books or team snacks; meals split between client
entertainment and team/travel; Eventbrite is a conference ticket or a marketing sponsorship.
The model learns from booked history (which contains human booking errors) and uses the memo,
merchant, MCC and department to disambiguate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

CAT_COLS = ["merchant_key", "mcc", "dept_id", "merchant_kind", "default_gl"]


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    X = df[CAT_COLS].astype(str).copy()
    X["text"] = df.memo.fillna("") + " " + df.descriptor_clean.fillna("").str.lower()
    X["log_amount"] = np.log1p(df.amount.astype(float))
    return X


class GLCoder:
    def __init__(self):
        features = ColumnTransformer(
            [
                ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=3), CAT_COLS),
                ("text", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True), "text"),
                ("num", "passthrough", ["log_amount"]),
            ]
        )
        clf = LogisticRegression(C=4.0, max_iter=3000)
        self.model = CalibratedClassifierCV(Pipeline([("f", features), ("clf", clf)]), method="sigmoid", cv=3)

    def fit(self, df: pd.DataFrame, labels: pd.Series) -> GLCoder:
        self.model.fit(_prep(df), labels.astype(str))
        return self

    def predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        proba = self.model.predict_proba(_prep(df))
        classes = self.model.classes_
        return classes[proba.argmax(1)], proba.max(1)


def default_gl_baseline(df: pd.DataFrame) -> np.ndarray:
    """What a merchant->GL lookup table gives you (unknown merchants fall back by MCC)."""
    mcc_map = {"5812": "6305", "5813": "6305", "5814": "6305", "4121": "6220", "7372": "6100", "5734": "6100"}
    fallback = df.mcc.map(mcc_map).fillna("6400")
    return df.default_gl.fillna(fallback).astype(str).to_numpy()
