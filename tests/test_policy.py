from trustgate.extract import RuleExtractor, Signals
from trustgate.policy import decide

BASE = dict(
    amount=40.0,
    mcc="5812",
    merchant_kind="meal",
    memo="",
    descriptor_clean="SOME BISTRO",
    has_receipt=True,
    receipt_gap_ratio=0.15,
    resolution_action="NEW",
    is_possible_split=False,
    is_possible_duplicate=False,
    is_over_single_limit=False,
    is_weekend=False,
    single_txn_limit=2000.0,
    same_day_merchant_count=1,
    same_day_merchant_total=40.0,
)


def row(**kw):
    return {**BASE, **kw}


def test_clean_solo_meal_is_approved():
    assert decide(row(), Signals(meal_type="solo"))[0] == "APPROVE"


def test_alcohol_on_team_meal_is_rejected_with_citation():
    d, sections, why = decide(row(amount=200.0), Signals(meal_type="team", attendees=8, has_alcohol=True))
    assert d == "REJECT" and "2.2" in sections and "§2.2" in why


def test_alcohol_at_client_dinner_is_fine_but_cap_applies():
    assert decide(row(amount=200.0), Signals(meal_type="client", attendees=4, has_alcohol=True))[0] == "APPROVE"
    d, sections, _ = decide(row(amount=400.0), Signals(meal_type="client", attendees=4, has_alcohol=True))
    assert d == "NEEDS_REVIEW" and sections == ["2.1"]


def test_prohibited_mcc_rejects_and_split_rejects():
    assert decide(row(mcc="7995", merchant_kind="personal"), Signals())[0] == "REJECT"
    d, s, _ = decide(row(is_possible_split=True, same_day_merchant_count=2, same_day_merchant_total=3000.0), Signals())
    assert d == "REJECT" and "7.1" in s


def test_missing_receipt_and_weekend_go_to_review():
    d, s, _ = decide(row(amount=120.0, has_receipt=False, receipt_gap_ratio=None, merchant_kind="office", mcc="5942"), Signals())
    assert d == "NEEDS_REVIEW" and "6.1" in s
    d, s, _ = decide(row(merchant_kind="office", mcc="5942", is_weekend=True, receipt_gap_ratio=0.0), Signals())
    assert d == "NEEDS_REVIEW" and s == ["8.1"]


def test_rule_extractor_parses_common_memo_formats():
    ex = RuleExtractor()
    for memo, n in [
        ("dinner w/ acme corp (4 ppl)", 4),
        ("client mtg - globex, 5 attendees", 5),
        ("qbr dinner, party of 3", 3),
    ]:
        s = ex.extract({"memo": memo, "mcc": "5812", "receipt_text": "Burger | IPA draft"})
        assert s.meal_type == "client" and s.attendees == n and s.has_alcohol


def test_rule_extractor_blind_spots_are_real():
    """Documented limitations: named cocktails, airline cabin brands and "<client> team" memos."""
    ex = RuleExtractor()
    assert ex.extract({"memo": "lunch with hooli team x4", "mcc": "5812"}).meal_type == "team"  # actually a client meal
    assert not ex.extract({"memo": "team dinner", "mcc": "5812", "receipt_text": "Negroni | Fries"}).has_alcohol
    assert not ex.extract({"memo": "", "mcc": "3058", "merchant_kind": "air", "receipt_text": "Cabin: Delta One"}).premium_cabin
