"""Orchestration: raw -> dbt staging -> merchant resolution -> dbt features -> agents -> marts."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import asdict

import pandas as pd

from .extract import get_extractor
from .gl import GLCoder
from .load import connect, write_agent_table
from .paths import DB_PATH, WAREHOUSE
from .policy import Verifier, decide, verifier_features
from .resolve import MerchantResolver

TRAIN_MONTHS = range(0, 6)


def dbt(select: str) -> None:
    env = {**os.environ, "DBT_PROFILES_DIR": str(WAREHOUSE), "TRUSTGATE_DB_PATH": str(DB_PATH)}
    cmd = ["dbt", "build", "--select", select, "--indirect-selection", "cautious", "--quiet", "--project-dir", str(WAREHOUSE)]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"dbt build {select} failed:\n{res.stdout[-4000:]}\n{res.stderr[-2000:]}")


def run_resolver() -> pd.DataFrame:
    con = connect(read_only=True)
    reg = con.sql("select * from staging.stg_merchant_registry").df()
    desc = con.sql("select distinct descriptor_clean from staging.stg_card_transactions").df().descriptor_clean
    con.close()
    res = MerchantResolver(reg).resolve_many(desc)
    write_agent_table("merchant_resolutions", res)
    return res


def load_features() -> pd.DataFrame:
    con = connect(read_only=True)
    df = con.sql("""
        select f.*, date_diff('month', date '2025-11-01', f.posted_month) as month_index,
               h.booked_gl, h.review_decision
        from intermediate.int_txn_enriched f
        left join staging.stg_historical_reviews h using (txn_id)
        order by posted_date, txn_id
    """).df()
    con.close()
    df["amount"] = df.amount.astype(float)
    for col in ("memo", "receipt_text", "merchant_kind", "merchant_id", "default_gl"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df.reset_index(drop=True)


def run_agents(extractor: str = "rules") -> pd.DataFrame:
    df = load_features()
    ext = get_extractor(extractor)
    t0 = time.time()
    records = df.to_dict("records")
    sigs = [ext.extract(r) for r in records]
    out = [decide(r, s) for r, s in zip(records, sigs, strict=True)]
    df["decision"] = [o[0] for o in out]
    df["cited_sections"] = ["|".join(o[1]) for o in out]
    df["rationale"] = [o[2] for o in out]
    for k in asdict(sigs[0]):
        df[f"sig_{k}"] = [getattr(s, k) for s in sigs]

    X = verifier_features(df, sigs, df.decision.tolist(), [o[1] for o in out])
    train = df.month_index.isin(TRAIN_MONTHS) & df.review_decision.notna()
    ver = Verifier().fit(X[train], (df.decision[train] == df.review_decision[train]).to_numpy())
    df["confidence"] = ver.predict(X)

    gl_train = df.month_index.isin(TRAIN_MONTHS) & df.booked_gl.notna()
    gl = GLCoder().fit(df[gl_train], df.booked_gl[gl_train])
    df["gl_code"], df["gl_confidence"] = gl.predict(df)
    df["extractor"] = ext.name

    # Novelty guard: memo words never seen in training months. Out-of-distribution text is
    # where both the extractor and the models are least trustworthy, whatever their scores say.
    tok = lambda t: set(re.findall(r"[a-z]{3,}", (t or "").lower()))  # noqa: E731
    vocab = set().union(*df.memo[df.month_index.isin(TRAIN_MONTHS)].map(tok))
    df["novel_terms"] = df.memo.map(lambda t: " ".join(sorted(tok(t) - vocab)))
    df["is_novel"] = df.novel_terms != ""
    df.attrs["agent_seconds"] = round(time.time() - t0, 1)
    return df


def write_decisions(df: pd.DataFrame) -> None:
    write_agent_table(
        "policy_decisions", df[["txn_id", "decision", "confidence", "cited_sections", "rationale", "route", "extractor"]]
    )
    write_agent_table("gl_predictions", df[["txn_id", "gl_code", "gl_confidence", "gl_route"]])
