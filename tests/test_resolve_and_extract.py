import json

import pandas as pd

from earned_autonomy.catalog import MERCHANTS
from earned_autonomy.extract import LLMExtractor
from earned_autonomy.resolve import MerchantResolver


def registry():
    return pd.DataFrame(
        [
            dict(
                merchant_id=m.merchant_id,
                merchant_name=m.name,
                aliases=list(m.aliases),
                require_any=list(m.require_any),
                forbid_any=list(m.forbid_any),
            )
            for m in MERCHANTS
            if m.in_registry
        ]
    )


def test_lookalike_guardrails():
    r = MerchantResolver(registry())
    assert r.resolve("AMAZON WEB SERVICES AWS.AMAZON.CO").merchant_id == "m_aws"
    assert r.resolve("AMZN MKTP US 2K4ZZ1").merchant_id == "m_amazon"
    assert r.resolve("UBER EATS PENDING").merchant_id == "m_ubereats"
    assert r.resolve("UBER TRIP HELP.UBER.COM").merchant_id == "m_uber"
    assert r.resolve("APPLEBEES 4411 DENVER").merchant_id != "m_apple"
    assert r.resolve("GOOGLE GSUITE_ABC123").merchant_id == "m_gworkspace"


def test_unknown_local_business_is_new_not_forced_match():
    r = MerchantResolver(registry())
    res = r.resolve("HARBOR OYSTER BAR CHICAGO")
    assert res.action in ("NEW", "ESCALATE") and res.merchant_id is None


class FakeBlock:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        assert kw["tool_choice"]["name"] == "report_signals"
        return type(
            "Msg",
            (),
            {
                "content": [
                    FakeBlock(
                        {
                            "meal_type": "team",
                            "attendees": 6,
                            "has_alcohol": True,
                            "premium_cabin": False,
                            "nightly_rate": None,
                            "personal_hint": False,
                        }
                    )
                ]
            },
        )()


def test_llm_extractor_uses_tool_schema_and_caches(tmp_path):
    client = FakeClient()
    cache = tmp_path / "cache.jsonl"
    ex = LLMExtractor(model="test-model", client=client, cache_path=cache)
    row = {"memo": "design crit lunch x6", "receipt_text": "Negroni | Salad", "mcc": "5812", "descriptor_clean": "X"}
    s = ex.extract(row)
    assert s.has_alcohol and s.attendees == 6 and s.meal_type == "team"
    ex2 = LLMExtractor(model="test-model", client=client, cache_path=cache)
    ex2.extract(row)
    assert client.calls == 1 and len(cache.read_text().splitlines()) == 1
    assert json.loads(cache.read_text())["signals"]["attendees"] == 6
