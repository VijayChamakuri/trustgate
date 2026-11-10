"""Real-data benchmark: GL coding of council card spend (Stockport, 2017 to mid-2022).

Task: given what a spend platform sees about a card line, predict the account finance staff
actually booked ("Purpose of Expenditure", mapped to 21 canonical accounts in dbt).

Two feature settings, reported separately:
  card_feed  merchant string, cardholder department and service, amount. This is what a
             card program sees, and is the headline setting.
  upstream   card_feed + the council's own procurement category. That field was assigned by
             staff alongside the label, so it nearly leaks the answer; reported as a reference.

Evaluation is rolling and strictly temporal: for each year t in 2018-2022 the model trains on
every labelled line before t and is scored on year t. Nothing from the future is used.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.preprocessing import OneHotEncoder

from .autonomy import choose_threshold, wilson_interval
from .embeddings import Embedder
from .paths import DB_PATH, RESULTS

TEST_YEARS = [2018, 2019, 2020, 2021, 2022]
CARD_FEED = ["department", "service_category"]
UPSTREAM = ["department", "service_category", "merchant_category"]


def load_lines() -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    df = con.sql("select * from stockport_intermediate.int_stockport__labelled_lines order by purchase_date, card_line_id").df()
    con.close()
    df["amount"] = df.amount.astype(float)
    for c in ("department", "service_category", "merchant_category"):
        df[c] = df[c].fillna("∅")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------- models
class Lookup:
    """Most frequent account per merchant key; falls back to department+service, then global."""

    name = "merchant_lookup"

    def fit(self, tr: pd.DataFrame, cats: list[str]):
        self.cats = cats
        self.by_merchant = tr.groupby("merchant_key").gl_account.agg(lambda s: s.value_counts().index[0])
        self.by_cat = tr.groupby(cats).gl_account.agg(lambda s: s.value_counts().index[0])
        self.glob = tr.gl_account.value_counts().index[0]
        self.classes_ = np.array(sorted(tr.gl_account.unique()))
        return self

    def predict(self, te: pd.DataFrame) -> np.ndarray:
        cat = pd.Series(
            [self.by_cat.get(tuple(r) if len(self.cats) > 1 else r[0], np.nan) for r in te[self.cats].itertuples(index=False)],
            index=te.index,
        )
        return te.merchant_key.map(self.by_merchant).fillna(cat).fillna(self.glob).to_numpy()


class LinearCoder:
    """Multinomial logistic regression over sparse text + one-hot + (optional) embedding features."""

    def __init__(self, cats: list[str], use_tfidf=True, use_embed=False, embedder: Embedder | None = None, C: float = 6.0):
        self.cats, self.use_tfidf, self.use_embed, self.embedder, self.C = cats, use_tfidf, use_embed, embedder, C
        self.name = "+".join([n for n, on in (("tfidf", use_tfidf), ("embed", use_embed)) if on]) + "_logreg"

    def _X(self, df: pd.DataFrame, fit: bool):
        parts = []
        if fit:
            self.ohe = OneHotEncoder(handle_unknown="ignore", min_frequency=2).fit(df[self.cats])
        parts.append(self.ohe.transform(df[self.cats]))
        if self.use_tfidf:
            if fit:
                self.tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True).fit(
                    df.merchant_raw
                )
            parts.append(self.tfidf.transform(df.merchant_raw))
        if self.use_embed:
            assert self.embedder is not None, "use_embed requires an embedder"
            parts.append(sparse.csr_matrix(self.embedder.encode(df.merchant_raw.tolist()) * 2.0))
        parts.append(sparse.csr_matrix(np.log1p(df.amount.clip(lower=0).to_numpy())[:, None] / 5))
        return sparse.hstack(parts).tocsr()

    def fit(self, tr: pd.DataFrame, cats=None):
        self.model = LogisticRegression(C=self.C, max_iter=2000).fit(self._X(tr, True), tr.gl_account)
        self.classes_ = self.model.classes_
        return self

    def predict_proba(self, te: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._X(te, False))

    def predict(self, te: pd.DataFrame) -> np.ndarray:
        return self.classes_[self.predict_proba(te).argmax(1)]


class KNNCoder:
    """Cosine kNN over local merchant-string embeddings (distance-weighted vote)."""

    name = "embed_knn"

    def __init__(self, embedder: Embedder, k: int = 15):
        self.embedder, self.k = embedder, k

    def fit(self, tr: pd.DataFrame, cats=None):
        self.E = self.embedder.encode(tr.merchant_raw.tolist())
        self.y = tr.gl_account.to_numpy()
        self.classes_ = np.array(sorted(set(self.y)))
        self.idx = {c: i for i, c in enumerate(self.classes_)}
        return self

    def predict_proba(self, te: pd.DataFrame) -> np.ndarray:
        Q = self.embedder.encode(te.merchant_raw.tolist())
        S = Q @ self.E.T
        top = np.argpartition(-S, self.k, axis=1)[:, : self.k]
        P = np.zeros((len(te), len(self.classes_)))
        for i in range(len(te)):
            w = np.exp(S[i, top[i]] * 20)
            for j, wt in zip(top[i], w, strict=True):
                P[i, self.idx[self.y[j]]] += wt
        return P / P.sum(1, keepdims=True)

    def predict(self, te: pd.DataFrame) -> np.ndarray:
        return self.classes_[self.predict_proba(te).argmax(1)]


# ----------------------------------------------------------------------------- metrics
def ece(conf: np.ndarray, correct: np.ndarray, bins: int = 15) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(conf, edges) - 1, 0, bins - 1)
    return float(
        sum(abs(conf[idx == b].mean() - correct[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any())
    )


def bootstrap_ci(correct: np.ndarray, n: int = 2000, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    means = correct[rng.integers(0, len(correct), (n, len(correct)))].mean(1)
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def topk(P: np.ndarray, classes: np.ndarray, y: np.ndarray, k: int = 3) -> float:
    top = classes[np.argsort(-P, axis=1)[:, :k]]
    return float(np.mean([t in row for t, row in zip(y, top, strict=True)]))


def label_ceiling(df: pd.DataFrame, cols: list[str]) -> float:
    """Upper bound from staff disagreement: the best any model can do on identical inputs."""
    g = df.groupby(cols).gl_account
    return float((g.transform(lambda s: s.value_counts().iloc[0]) / g.transform("size")).mean())


# ----------------------------------------------------------------------------- benchmark
def rolling_benchmark(df: pd.DataFrame, emb: Embedder) -> dict:
    settings = {"card_feed": CARD_FEED, "upstream": UPSTREAM}
    out: dict[str, Any] = {"settings": {}, "per_year": [], "predictions": {}}
    for setting, cats in settings.items():
        models: dict[str, Any] = {
            "majority": None,
            "merchant_lookup": lambda c=cats: Lookup(),
            "tfidf_logreg": lambda c=cats: LinearCoder(c),
            "embed_knn": lambda: KNNCoder(emb),
            "embed_logreg": lambda c=cats: LinearCoder(c, use_tfidf=False, use_embed=True, embedder=emb),
            "tfidf+embed_logreg": lambda c=cats: LinearCoder(c, use_tfidf=True, use_embed=True, embedder=emb),
        }
        if setting == "upstream":
            models = {k: v for k, v in models.items() if k in ("merchant_lookup", "tfidf+embed_logreg")}
            models["category_lookup"] = "category"
        pooled: dict[str, dict[str, list]] = {
            m: {"y": [], "p": [], "conf": [], "top3": [], "year": [], "seen": []} for m in models
        }
        for ty in TEST_YEARS:
            tr, te = df[df.statement_year < ty], df[df.statement_year == ty]
            seen = te.merchant_key.isin(set(tr.merchant_key)).to_numpy()
            for name, make in models.items():
                t0 = time.time()
                if make is None:
                    pred = np.full(len(te), tr.gl_account.value_counts().index[0])
                    conf, t3 = np.ones(len(te)), np.nan
                elif make == "category":
                    mc = tr.groupby("merchant_category").gl_account.agg(lambda s: s.value_counts().index[0])
                    pred = te.merchant_category.map(mc).fillna(tr.gl_account.value_counts().index[0]).to_numpy()
                    conf, t3 = np.ones(len(te)), np.nan
                else:
                    m = make().fit(tr, cats)
                    if hasattr(m, "predict_proba"):
                        P = m.predict_proba(te)
                        pred, conf = m.classes_[P.argmax(1)], P.max(1)
                        t3 = topk(P, m.classes_, te.gl_account.to_numpy())
                    else:
                        pred, conf, t3 = m.predict(te), np.ones(len(te)), np.nan
                y = te.gl_account.to_numpy()
                out["per_year"].append(
                    {
                        "setting": setting,
                        "model": name,
                        "year": ty,
                        "n": len(te),
                        "accuracy": float((pred == y).mean()),
                        "top3": t3,
                        "macro_f1": float(f1_score(y, pred, average="macro", zero_division=0)),
                        "seen_merchant_share": float(seen.mean()),
                        "accuracy_new_merchants": float((pred[~seen] == y[~seen]).mean()) if (~seen).any() else None,
                        "seconds": round(time.time() - t0, 2),
                    }
                )
                d = pooled[name]
                d["y"] += list(y)
                d["p"] += list(pred)
                d["conf"] += list(conf)
                d["year"] += [ty] * len(te)
                d["seen"] += list(seen)
        summary = {}
        for name, d in pooled.items():
            y, p, conf = np.array(d["y"]), np.array(d["p"]), np.array(d["conf"], float)
            seen = np.array(d["seen"])
            correct = (y == p).astype(float)
            per = [r for r in out["per_year"] if r["setting"] == setting and r["model"] == name]
            summary[name] = {
                "n": len(y),
                "accuracy": float(correct.mean()),
                "accuracy_ci": bootstrap_ci(correct),
                "macro_f1": float(f1_score(y, p, average="macro", zero_division=0)),
                "top3": float(np.nanmean([r["top3"] for r in per])) if not all(np.isnan(r["top3"]) for r in per) else None,
                "accuracy_seen_merchants": float(correct[seen].mean()),
                "accuracy_new_merchants": float(correct[~seen].mean()),
                "ece": ece(conf, correct) if conf.std() > 0 else None,
            }
            if setting == "card_feed" and name == "tfidf+embed_logreg":
                out["predictions"] = {"y": y.tolist(), "p": p.tolist(), "conf": conf.tolist(), "year": d["year"]}
        out["settings"][setting] = summary
    out["ceiling"] = {
        "card_feed": label_ceiling(df, ["merchant_raw", *CARD_FEED]),
        "upstream": label_ceiling(df, ["merchant_raw", *UPSTREAM]),
    }
    out["confusions"] = top_confusions(df, out["predictions"])
    return out


def top_confusions(df: pd.DataFrame, pr: dict, n: int = 8) -> list[dict]:
    te = df[df.statement_year.isin(TEST_YEARS)].reset_index(drop=True)
    te = te.assign(pred=pr["p"])
    wrong = te[te.pred != te.gl_account]
    pairs = wrong.groupby(["gl_account", "pred"]).agg(
        count=("merchant_raw", "size"), examples=("merchant_raw", lambda s: list(s.value_counts().index[:3]))
    )
    return (
        pairs.sort_values("count", ascending=False)
        .head(n)
        .reset_index()
        .rename(columns={"gl_account": "booked", "pred": "predicted"})
        .to_dict("records")
    )


# ----------------------------------------------------------------------------- autonomy
@dataclass
class RealRollout:
    first_month: str = "2020-01-01"
    shadow_months: int = 6
    audits_per_month: int = 40
    budgets: tuple[float, ...] = (0.02, 0.05, 0.10)
    high_value: float = 250.0
    retrain_every_months: int = 3
    seed: int = 0


POLICIES = ("auto_all", "point_estimate", "wilson", "wilson+novelty", "wilson+novelty+tiers")


def _predictions(df: pd.DataFrame, emb: Embedder, cats: list[str], cfg: RealRollout) -> tuple[pd.DataFrame, list]:
    """Expanding-window predictions; the model is refit every `retrain_every_months` on all prior months."""
    months = sorted(m for m in df.statement_month.unique() if m >= pd.Timestamp(cfg.first_month))
    rng = np.random.default_rng(cfg.seed)
    preds, model, last_fit = [], None, None
    for i, m in enumerate(months):
        tr, te = df[df.statement_month < m], df[df.statement_month == m]
        if model is None or i % cfg.retrain_every_months == 0:
            model = LinearCoder(cats, use_tfidf=True, use_embed=True, embedder=emb).fit(tr)
            last_fit = m
        P = model.predict_proba(te)
        audited = np.zeros(len(te), bool)
        audited[rng.choice(len(te), size=min(cfg.audits_per_month, len(te)), replace=False)] = True
        seen = set(df[df.statement_month < last_fit].merchant_key)
        top3 = model.classes_[np.argsort(-P, axis=1)[:, :3]]
        preds.append(
            te.assign(
                pred=model.classes_[P.argmax(1)],
                conf=P.max(1),
                audited=audited,
                novel=~te.merchant_key.isin(seen),
                in_top3=[y in row for y, row in zip(te.gl_account, top3, strict=True)],
            )
        )
    allp = pd.concat(preds, ignore_index=True)
    allp["correct"] = allp.pred == allp.gl_account
    return allp, months


def _run_policy(allp: pd.DataFrame, live_months: list, policy: str, budget: float, cfg: RealRollout) -> dict:
    rows, auto_mask = [], pd.Series(False, index=allp.index)
    for m in live_months:
        past = allp[(allp.statement_month < m) & allp.audited]
        cur = allp[allp.statement_month == m]
        if policy == "auto_all":
            auto = pd.Series(True, index=cur.index)
            taus: tuple[float | None, float | None] = (0.0, 0.0)
        else:
            guard = "novelty" in policy
            if guard:
                past = past[~past.novel]
            cons = policy != "point_estimate"
            if policy.endswith("tiers"):
                lo, hi = past[past.amount < cfg.high_value], past[past.amount >= cfg.high_value]
                t_lo = choose_threshold(lo.conf, lo.correct, budget, 50, cons)
                t_hi = choose_threshold(hi.conf, hi.correct, budget, 50, cons)
            else:
                t_lo = t_hi = choose_threshold(past.conf, past.correct, budget, 50, cons)
            taus = (t_lo, t_hi)
            hv = cur.amount >= cfg.high_value
            auto = ((~hv) & (t_lo is not None) & (cur.conf >= (t_lo or 2))) | (
                hv & (t_hi is not None) & (cur.conf >= (t_hi or 2))
            )
            if guard:
                auto &= ~cur.novel
        auto_mask.loc[cur.index] = auto.to_numpy()
        a = cur[auto]
        rows.append(
            {
                "month": str(pd.Timestamp(m).date()),
                "n": len(cur),
                "auto": len(a),
                "errors": int((~a.correct).sum()),
                "tau_low": taus[0],
                "tau_high": taus[1],
            }
        )
    r = pd.DataFrame(rows)
    n, a, e = int(r.n.sum()), int(r.auto.sum()), int(r.errors.sum())
    lo, hi = wilson_interval(e, a)
    live = allp[allp.statement_month.isin(live_months)]
    wrong = live[auto_mask.loc[live.index] & ~live.correct]
    return {
        "automation_rate": a / max(n, 1),
        "auto_error_rate": e / max(a, 1),
        "auto_error_ci": [lo, hi],
        "auto": a,
        "auto_errors": e,
        "n": n,
        "misposted_gbp": float(wrong.amount.sum()),
        "months_over_budget": int(((r.errors / r.auto.clip(lower=1)) > budget).sum()),
        "by_month": r.to_dict("records"),
    }


def autonomy_rollout(df: pd.DataFrame, emb: Embedder, cfg: RealRollout | None = None) -> dict:
    cfg = cfg or RealRollout()
    out: dict[str, Any] = {"config": {**cfg.__dict__, "budgets": list(cfg.budgets)}, "settings": {}}
    for setting, cats in (("card_feed", CARD_FEED), ("upstream", UPSTREAM)):
        allp, months = _predictions(df, emb, cats, cfg)
        live_months = months[cfg.shadow_months :]
        live = allp[allp.statement_month.isin(live_months)]
        res: dict[str, Any] = {
            "live_months": len(live_months),
            "live_lines": int(len(live)),
            "model_accuracy_live": float(live.correct.mean()),
            "top3_live": float(live.in_top3.mean()),
            "novel_share_live": float(live.novel.mean()),
            "accuracy_by_confidence": [
                {
                    "min_conf": t,
                    "coverage": float((live.conf >= t).mean()),
                    "error": float(1 - live.correct[live.conf >= t].mean()) if (live.conf >= t).any() else None,
                }
                for t in (0.5, 0.7, 0.8, 0.9, 0.95, 0.97)
            ],
            "budgets": {},
        }
        for b in cfg.budgets:
            res["budgets"][str(b)] = {p: _run_policy(allp, live_months, p, b, cfg) for p in POLICIES}
        out["settings"][setting] = res
    return out


def run_all(log=print) -> dict:
    """Full real-data run: rolling benchmark -> results/real/gl_benchmark.json,
    trustgate rollout -> results/real/autonomy.json."""
    t0 = time.time()
    df = load_lines()
    emb = Embedder()
    emb.encode(df.merchant_raw.unique().tolist())  # warm the cache once
    out_dir = RESULTS / "real"
    out_dir.mkdir(parents=True, exist_ok=True)
    bench: dict[str, Any] = {
        "dataset": {
            "labelled_lines": len(df),
            "accounts": int(df.gl_account.nunique()),
            "merchants": int(df.merchant_key.nunique()),
            "years": sorted(df.statement_year.unique().tolist()),
            "lines_per_year": df.statement_year.value_counts().sort_index().to_dict(),
            "account_distribution": df.gl_account.value_counts().to_dict(),
        },
        "embedding_model": emb.model_name,
        "benchmark": rolling_benchmark(df, emb),
    }
    bench["benchmark"].pop("predictions", None)
    bench["seconds"] = round(time.time() - t0, 1)
    (out_dir / "gl_benchmark.json").write_text(json.dumps(bench, indent=2, default=_jd))
    log(f"[real] benchmark done in {bench['seconds']}s")
    auto: dict[str, Any] = autonomy_rollout(df, emb)
    auto["seconds"] = round(time.time() - t0 - bench["seconds"], 1)
    (out_dir / "autonomy.json").write_text(json.dumps(auto, indent=2, default=_jd))
    log(f"[real] autonomy rollout done in {auto['seconds']}s")
    return {"benchmark": bench, "autonomy": auto}


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    return str(o)
