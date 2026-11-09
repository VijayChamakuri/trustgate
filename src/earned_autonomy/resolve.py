"""Merchant resolution: messy card descriptor -> canonical merchant, or NEW, or ESCALATE.

Retrieval combines character n-gram TF-IDF similarity with fuzzy token matching against the
registry's names and aliases. The resolver must then choose one of three constrained
actions, and guardrail tokens stop look-alike merchants from being merged (Amazon vs AWS,
Uber vs Uber Eats, Apple vs Applebee's, the three Google products).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer

NOISE = {
    "inc",
    "llc",
    "ltd",
    "pty",
    "co",
    "corp",
    "com",
    "the",
    "bill",
    "www",
    "help",
    "us",
    "usa",
    "store",
    "online",
    "subscr",
    "subscription",
    "pending",
    "ref",
    "hm",
    "mon",
    "new",
    "york",
    "san",
    "francisco",
    "austin",
    "chicago",
    "denver",
    "boston",
    "seattle",
    "atlanta",
    "miami",
    "los",
    "angeles",
    "ny",
    "ca",
    "tx",
    "il",
    "ma",
    "wa",
    "ga",
    "fl",
    "fra",
    "francis",
    "franc",
    "fr",
}


def normalize(descriptor: str) -> str:
    s = descriptor.lower()
    s = re.sub(r"[^a-z0-9.' ]+", " ", s)
    toks = []
    for tok in s.split():
        tok = tok.strip(".'")
        if not tok or tok in NOISE:
            continue
        if re.search(r"\d", tok) and not re.fullmatch(r"o365|365|x\d+", tok):
            continue  # reference numbers, store numbers, phone numbers
        toks.append(tok)
    return " ".join(toks)


@dataclass
class Resolution:
    descriptor_clean: str
    merchant_id: str | None
    merchant_key: str
    action: str  # MATCH | NEW | ESCALATE
    score: float
    runner_up_id: str | None
    guardrail_blocked: bool


class MerchantResolver:
    def __init__(self, registry: pd.DataFrame, match_threshold: float = 0.78, new_threshold: float = 0.55, margin: float = 0.08):
        self.match_threshold = match_threshold
        self.new_threshold = new_threshold
        self.margin = margin
        rows = []
        for r in registry.itertuples():
            names = {r.merchant_name.lower(), *[a.lower() for a in r.aliases]}
            for n in names:
                rows.append((r.merchant_id, normalize(n) or n))
        self.alias_df = pd.DataFrame(rows, columns=["merchant_id", "alias"])
        self.registry = registry.set_index("merchant_id")
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        self.alias_mat = self.vec.fit_transform(self.alias_df.alias)

    def _allowed(self, merchant_id: str, text: str) -> bool:
        r = self.registry.loc[merchant_id]
        req = list(r.require_any)
        if req and not any(tok in text for tok in req):
            return False
        return not any(tok in text for tok in r.forbid_any)

    def score_all(self, norm: str) -> pd.Series:
        sims = (self.vec.transform([norm]) @ self.alias_mat.T).toarray().ravel()
        fz = np.array(
            [
                max(fuzz.token_set_ratio(norm, a), fuzz.partial_ratio(a, norm) if len(a) >= 4 else 0) / 100
                for a in self.alias_df.alias
            ]
        )
        exact = np.array(
            [1.0 if len(a) >= 3 and re.search(rf"(^|\s){re.escape(a)}($|\s)", norm) else 0.0 for a in self.alias_df.alias]
        )
        combined = np.maximum(0.5 * sims + 0.5 * fz, 0.92 * exact)
        return pd.Series(combined, index=self.alias_df.merchant_id).groupby(level=0).max().sort_values(ascending=False)

    def resolve(self, descriptor_clean: str) -> Resolution:
        norm = normalize(descriptor_clean)
        text = descriptor_clean.lower()
        if not norm:
            return Resolution(descriptor_clean, None, "new:unknown", "ESCALATE", 0.0, None, False)
        scores = self.score_all(norm)
        blocked = False
        valid = []
        for mid, s in scores.items():
            if self._allowed(mid, text):
                valid.append((mid, float(s)))
            elif not valid:
                blocked = True  # the best raw candidate was a guarded look-alike
            if len(valid) == 2:
                break
        top_id, top = valid[0] if valid else (None, 0.0)
        second = valid[1][1] if len(valid) > 1 else 0.0
        runner = valid[1][0] if len(valid) > 1 else None
        if top >= self.match_threshold and (top - second) >= self.margin:
            action, mid = "MATCH", top_id
        elif top < self.new_threshold:
            action, mid = "NEW", None
        else:
            action, mid = "ESCALATE", None
        key = mid if mid else "new:" + norm.replace(" ", "_")[:40]
        return Resolution(descriptor_clean, mid, key, action, round(top, 4), runner if action == "MATCH" else top_id, blocked)

    def resolve_many(self, descriptors) -> pd.DataFrame:
        return pd.DataFrame([self.resolve(d).__dict__ for d in descriptors])


def evaluate_resolutions(res: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """truth: descriptor_clean, true_merchant_id, true_in_registry (majority per descriptor)."""
    df = res.merge(truth, on="descriptor_clean", how="inner")
    in_reg = df.true_in_registry
    match = df.action == "MATCH"
    correct_match = match & (df.merchant_id == df.true_merchant_id)
    wrong_match = match & ~correct_match
    out = {
        "descriptors": int(len(df)),
        "match_rate": float(match.mean()),
        "match_precision": float(correct_match.sum() / max(match.sum(), 1)),
        "wrong_merge_rate": float(wrong_match.mean()),
        "registry_recall": float(correct_match[in_reg].mean()) if in_reg.any() else None,
        "new_vendor_recall": float((df.action[~in_reg] == "NEW").mean()) if (~in_reg).any() else None,
        "new_vendor_wrongly_matched": int((match & ~in_reg).sum()),
        "escalation_rate": float((df.action == "ESCALATE").mean()),
        "guardrail_blocks": int(df.guardrail_blocked.sum()),
    }
    protected = {
        ("m_amazon", "m_aws"),
        ("m_aws", "m_amazon"),
        ("m_uber", "m_ubereats"),
        ("m_ubereats", "m_uber"),
        ("m_apple", "m_applebees"),
        ("m_applebees", "m_apple"),
        ("m_googleads", "m_gcp"),
        ("m_gcp", "m_googleads"),
        ("m_gworkspace", "m_gcp"),
        ("m_gcp", "m_gworkspace"),
        ("m_googleads", "m_gworkspace"),
        ("m_gworkspace", "m_googleads"),
    }
    out["protected_pair_confusions"] = int(
        sum((t, p) in protected for t, p in zip(df.true_merchant_id[match], df.merchant_id[match], strict=True))
    )
    out["wrong_merge_examples"] = (
        df[wrong_match][["descriptor_clean", "merchant_id", "true_merchant_id", "score"]].head(8).to_dict("records")
    )
    return out
