"""Signal extraction from unstructured text (memos and receipt line items).

Two interchangeable backends produce the same `Signals` record:

* `RuleExtractor`: regex/lexicon. Fast, free, deterministic, and blind to phrasing it has
  never seen (a "Negroni" is not obviously alcohol to a word list).
* `LLMExtractor`: Claude with a strict JSON tool schema. Used only when ANTHROPIC_API_KEY is
  set; responses are cached to disk so evals are reproducible and cheap to re-run.

The policy engine never reads raw text directly. It only reads Signals, so swapping the
extractor is a controlled experiment: same rules, same verifier, different perception.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .paths import DATA

ALCOHOL_WORDS = r"\b(wine|beer|ipa|ale|lager|stout|pilsner|cabernet|pinot|merlot|chardonnay|sauvignon|prosecco|champagne|ros[eé]|margarita|martini|cocktail|whiske?y|vodka|gin|tequila|bourbon|rum|sake|mezcal|soju)\b"
PREMIUM_CABIN = r"\b(business|first|fare class j)\b"
PERSONAL = r"\b(personal|repay|reimburse me|will reimburse|not business)\b"
CLIENT = r"\b(client|customer|qbr|prospect|partner)\b|\bw/|\bwith\b"
TEAM = r"\b(team|offsite|retro|onboarding|new hires|celebration|sprint|all-hands)\b"
ATTENDEES = [
    r"\((\d{1,2})\)",
    r"(\d{1,2})\s*(?:ppl|people|pax|attendees|guests|persons)",
    r"party of (\d{1,2})",
    r"\bx\s?(\d{1,2})\b",
    r"-\s*(\d{1,2})\s*$",
]
NIGHTLY = [r"([\d,]+\.\d{2})\s*/\s*night", r"@\s*\$([\d,]+\.\d{2})", r"nightly:\s*\$([\d,]+\.\d{2})"]


@dataclass
class Signals:
    meal_type: str | None = None  # client | team | solo | None (not a meal)
    attendees: int | None = None
    has_alcohol: bool = False
    premium_cabin: bool = False
    nightly_rate: float | None = None
    personal_hint: bool = False


class RuleExtractor:
    name = "rules"

    def extract(self, row: dict) -> Signals:
        memo = (row.get("memo") or "").lower()
        receipt = (row.get("receipt_text") or "").lower()
        mcc = str(row.get("mcc") or "")
        is_meal = mcc in {"5812", "5813", "5814"} or row.get("merchant_kind") == "meal"
        s = Signals()
        if is_meal:
            s.meal_type = "team" if re.search(TEAM, memo) else "client" if re.search(CLIENT, memo) else "solo"
            for pat in ATTENDEES:
                m = re.search(pat, memo)
                if m:
                    s.attendees = int(m.group(1))
                    break
            s.has_alcohol = bool(re.search(ALCOHOL_WORDS, receipt))
        if mcc.startswith("3") or mcc == "4511" or row.get("merchant_kind") == "air":
            s.premium_cabin = bool(re.search(PREMIUM_CABIN, receipt))
        for pat in NIGHTLY:
            m = re.search(pat, receipt)
            if m:
                s.nightly_rate = float(m.group(1).replace(",", ""))
                break
        s.personal_hint = bool(re.search(PERSONAL, memo))
        return s


LLM_SYSTEM = """You extract facts from a corporate card transaction for an expense-policy engine.
Only report what the memo and receipt text support. Use null when a fact is not stated.
- meal_type: "client" if the meal entertains an external customer/prospect/partner (a company name
  in the memo usually means a client), "team" for internal group meals, "solo" for one person,
  null if the charge is not a meal.
- attendees: number of people at the meal if stated.
- has_alcohol: true if any receipt line item is an alcoholic drink (including named cocktails).
- premium_cabin: true if the airfare cabin is business or first class under any airline brand name.
- nightly_rate: pre-tax nightly room rate in USD if a hotel receipt states or implies it.
- personal_hint: true if the memo indicates the purchase was personal or made on the wrong card."""

LLM_TOOL = {
    "name": "report_signals",
    "description": "Report extracted transaction facts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meal_type": {"type": ["string", "null"], "enum": ["client", "team", "solo", None]},
            "attendees": {"type": ["integer", "null"]},
            "has_alcohol": {"type": "boolean"},
            "premium_cabin": {"type": "boolean"},
            "nightly_rate": {"type": ["number", "null"]},
            "personal_hint": {"type": "boolean"},
        },
        "required": ["meal_type", "attendees", "has_alcohol", "premium_cabin", "nightly_rate", "personal_hint"],
    },
}


class LLMExtractor:
    """Claude-backed extractor with an on-disk cache. Falls back to rules for rows with no text."""

    name = "llm"

    def __init__(self, model: str | None = None, client=None, cache_path=None):
        self.model = model or os.environ.get("EA_LLM_MODEL", "claude-haiku-5-5")
        self.cache_path = cache_path or DATA / f"llm_cache_{self.model}.jsonl"
        self.cache: dict[str, dict] = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text().splitlines():
                rec = json.loads(line)
                self.cache[rec["key"]] = rec["signals"]
        if client is None:
            import anthropic  # optional dependency: `uv sync --extra llm`

            client = anthropic.Anthropic()
        self.client = client
        self.fallback = RuleExtractor()

    def _key(self, row: dict) -> str:
        payload = json.dumps([row.get("descriptor_clean"), row.get("mcc"), row.get("memo"), row.get("receipt_text")])
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def extract(self, row: dict) -> Signals:
        if not (row.get("memo") or row.get("receipt_text")):
            return self.fallback.extract(row)
        key = self._key(row)
        if key not in self.cache:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=LLM_SYSTEM,
                tools=[LLM_TOOL],
                tool_choice={"type": "tool", "name": "report_signals"},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Descriptor: {row.get('descriptor_clean')}\nMCC: {row.get('mcc')}\nAmount: ${row.get('amount')}\n"
                            f"Memo: {row.get('memo') or ''}\nReceipt: {row.get('receipt_text') or ''}"
                        ),
                    }
                ],
            )
            block = next(b for b in msg.content if getattr(b, "type", "") == "tool_use")
            self.cache[key] = dict(block.input)
            with self.cache_path.open("a") as f:
                f.write(json.dumps({"key": key, "signals": self.cache[key]}) + "\n")
        d = self.cache[key]
        fields: dict[str, Any] = {k: d.get(k) for k in asdict(Signals())}
        return Signals(**fields)


# ----------------------------------------------------------------------------- embeddings
# Prototype phrases are generic category descriptions written once, never tuned on the
# generator's vocabulary. Items are compared to "positive" vs "negative" prototypes with a
# local embedding model (bge-small via fastembed): free, offline and deterministic.
PROTOTYPES = {
    "alcohol": (
        ["an alcoholic drink", "a cocktail", "a glass of wine", "a beer", "liquor or spirits"],
        ["a soft drink or water", "a coffee or tea", "a main course dish", "a dessert", "a side dish"],
    ),
    "premium_cabin": (
        ["business class airline cabin", "first class flight seat", "premium lie-flat cabin"],
        ["economy class airline seat", "basic economy fare", "standard coach seat", "extra legroom economy"],
    ),
    "personal": (
        [
            "a personal purchase that is not for work",
            "I used the company card by mistake",
            "this is my own expense and I will pay it back",
        ],
        ["office supplies for the team", "a business expense", "equipment for work", "a client meal"],
    ),
}


class EmbeddingExtractor(RuleExtractor):
    """Rules for structure (attendees, nightly rate, meal type) + embeddings for open-vocabulary
    signals. `combine="or"` keeps every rule hit and adds embedding hits (rules+embed)."""

    name = "embed"

    def __init__(self, margin: float = 0.02, combine: str = "or"):
        from .embeddings import Embedder

        self.emb, self.margin, self.combine = Embedder(), margin, combine
        self.name = "rules+embed" if combine == "or" else "embed"
        self.protos = {k: (self.emb.encode(p), self.emb.encode(n)) for k, (p, n) in PROTOTYPES.items()}

    def _hit(self, kind: str, texts: list[str]) -> bool:
        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return False
        V = self.emb.encode(texts)
        pos, neg = self.protos[kind]
        return bool(((V @ pos.T).max(1) - (V @ neg.T).max(1) > self.margin).any())

    def extract(self, row: dict) -> Signals:
        s = super().extract(row)
        rules = Signals(**s.__dict__)
        receipt = row.get("receipt_text") or ""
        memo = row.get("memo") or ""
        if s.meal_type is not None:
            s.has_alcohol = self._hit("alcohol", receipt.split("|"))
        cabin = re.search(r"cabin:\s*([^|]+)", receipt, re.I)
        if cabin:
            s.premium_cabin = self._hit("premium_cabin", [cabin.group(1)])
        s.personal_hint = self._hit("personal", [memo]) if memo else False
        if self.combine == "or":
            s.has_alcohol |= rules.has_alcohol
            s.premium_cabin |= rules.premium_cabin
            s.personal_hint |= rules.personal_hint
        return s


def get_extractor(name: str):
    if name == "rules":
        return RuleExtractor()
    if name == "embed":
        return EmbeddingExtractor(combine="replace")
    if name == "rules+embed":
        return EmbeddingExtractor(combine="or")
    if name == "llm":
        return LLMExtractor()
    raise ValueError(f"unknown extractor {name!r}")
