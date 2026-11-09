"""Synthetic company generator.

Produces one year of corporate-card activity for a ~160-person company, with the kinds of
mess real spend data has: card-network descriptors, misleading MCCs, free-text memos,
receipts whose important facts (fare class, nightly rate, a cocktail) live only in line
items, duplicate processor charges, split purchases, zombie SaaS seats and price creep.

Three layers are written:
  raw.*    what a finance platform actually sees (agents may read these, via dbt staging)
  raw.historical_reviews   what human reviewers did in the first 8 months (noisy, lenient)
  gold.*   audited truth for every transaction, used ONLY by the eval harness
"""

from __future__ import annotations

import datetime as dt
import re
import string
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .catalog import (
    CITIES,
    CLIENTS,
    DEPARTMENTS,
    EVENTS,
    GL_ACCOUNTS,
    HIGH_COST_CITIES,
    LONGTAIL_PREFIXES,
    LONGTAIL_RESTAURANTS,
    LONGTAIL_SHOPS,
    MERCHANTS,
    MERCHANTS_BY_ID,
)

START = dt.date(2025, 11, 1)
N_MONTHS = 12
N_EMPLOYEES = 420

FIRST = [
    "Ava",
    "Liam",
    "Maya",
    "Noah",
    "Priya",
    "Ethan",
    "Sofia",
    "Lucas",
    "Zara",
    "Owen",
    "Mei",
    "Diego",
    "Amara",
    "Jonas",
    "Leila",
    "Ravi",
    "Hana",
    "Marco",
    "Isla",
    "Kofi",
    "Nina",
    "Theo",
    "Yuki",
    "Sam",
]
LAST = [
    "Patel",
    "Nguyen",
    "Garcia",
    "Kim",
    "Okafor",
    "Silva",
    "Cohen",
    "Rossi",
    "Haddad",
    "Larsen",
    "Chen",
    "Mensah",
    "Novak",
    "Ibrahim",
    "Dubois",
    "Tanaka",
    "Reyes",
    "Walsh",
    "Singh",
    "Moreau",
]

# Receipt vocab. Alcohol items deliberately include named cocktails that a naive keyword
# list will miss -- this is the realistic gap that motivates an LLM extractor.
FOOD = [
    "Caesar salad",
    "Margherita pizza",
    "Chicken tikka",
    "Pad thai",
    "Burger",
    "Salmon",
    "Ribeye 12oz",
    "Tacos (3)",
    "Ramen",
    "Garden salad",
    "Fries",
    "Dumplings",
    "Risotto",
    "Bibimbap",
    "Club sandwich",
    "Sparkling water",
    "Iced tea",
    "Coffee",
    "Lemonade",
    "Tiramisu",
    "Cheesecake",
    "Bread service",
]
ALCOHOL = [
    "IPA draft",
    "House Cabernet",
    "Pinot Noir btl",
    "Margarita",
    "Old Fashioned",
    "Negroni",
    "Sake carafe",
    "Prosecco",
    "Lager",
    "Moscow Mule",
    "Aperol Spritz",
    "Whiskey neat",
    "Hazy pale ale",
    "Espresso Martini",
    "Chardonnay gl",
    "Paloma",
]
FARES = {
    "economy": ["Main Cabin", "Economy", "Basic Economy", "Coach (Y)", "Blue Basic"],
    "premium": ["Comfort+", "Economy Plus", "Premium Economy", "Even More Space"],
    "business": ["Business", "Delta One", "Polaris", "Mint", "First Class", "Fare class J", "Flagship First"],
}
AIRPORTS = {
    "New York": "JFK",
    "San Francisco": "SFO",
    "Austin": "AUS",
    "Chicago": "ORD",
    "Denver": "DEN",
    "Boston": "BOS",
    "Seattle": "SEA",
    "Atlanta": "ATL",
    "Miami": "MIA",
    "Los Angeles": "LAX",
}
DESTS = list(AIRPORTS)

DRIFT_FROM, DRIFT_RATE = 8, 0.35
LENIENCY = 0.06  # share of review-worthy items historical reviewers waved through

POLICY = {
    "meal_client_pp": 75.0,
    "meal_team_pp": 30.0,
    "lodging_cap": 350.0,
    "lodging_cap_high": 450.0,
    "receipt_required_over": 75.0,
    "new_software_review_over": 500.0,
}


@dataclass
class Txn:
    txn_id: str
    posted: dt.date
    employee_id: str
    merchant_id: str  # true merchant (gold)
    descriptor: str
    mcc: str
    amount: float
    memo: str
    kind: str
    gold_gl: str
    # latent truths --------------------------------------------------------------------
    is_travel: bool = False
    attendees: int | None = None
    meal_type: str | None = None  # client | team | solo
    has_alcohol: bool = False
    fare: str | None = None  # economy | premium | business
    nightly_rate: float | None = None
    dest_city: str | None = None
    personal: bool = False
    split_group: str | None = None
    duplicate_of: str | None = None
    auto_billed: bool = False
    receipt: dict | None = None
    receipt_mismatch: bool = False
    extra: dict = field(default_factory=dict)


class World:
    def __init__(self, seed: int = 7, drift_rate: float = DRIFT_RATE, leniency: float = LENIENCY):
        self.drift_rate, self.leniency = drift_rate, leniency
        self.rng = np.random.default_rng(seed)
        self.txns: list[Txn] = []
        self._n = 0
        self.employees = self._make_employees()
        self.days = [START + dt.timedelta(days=i) for i in range((self._month_start(N_MONTHS) - START).days)]

    # ------------------------------------------------------------------ helpers
    def r(self) -> float:
        return float(self.rng.random())

    def choice(self, seq):
        return seq[int(self.rng.integers(len(seq)))]

    def ref(self, k: int | None = None) -> str:
        k = k or int(self.rng.integers(6, 10))
        alphabet = string.ascii_uppercase + string.digits
        return "".join(self.choice(alphabet) for _ in range(k))

    @staticmethod
    def _month_start(m: int) -> dt.date:
        y, mo = START.year + (START.month - 1 + m) // 12, (START.month - 1 + m) % 12 + 1
        return dt.date(y, mo, 1)

    def month_days(self, m: int) -> list[dt.date]:
        a, b = self._month_start(m), self._month_start(m + 1)
        return [a + dt.timedelta(days=i) for i in range((b - a).days)]

    def weekday_in(self, m: int, allow_weekend: float = 0.04) -> dt.date:
        days = self.month_days(m)
        if self.r() < allow_weekend:
            return self.choice(days)
        return self.choice([d for d in days if d.weekday() < 5])

    def drifted(self, mo: int, base: list[str], drift: list[str]) -> str:
        """Memo phrasing drifts in the last 4 months: new hires write memos the models never saw."""
        if mo >= DRIFT_FROM and self.r() < self.drift_rate:
            return self.choice(drift)
        return self.choice(base)

    def money(self, lo: float, hi: float) -> float:
        # log-uniform: most purchases are small, a few are large
        return round(float(np.exp(self.rng.uniform(np.log(lo), np.log(hi)))), 2)

    def new_id(self) -> str:
        self._n += 1
        return f"tx_{self._n:06d}"

    # ------------------------------------------------------------------ people
    def _make_employees(self) -> pd.DataFrame:
        weights = np.array([w for *_, w in DEPARTMENTS])
        rows = []
        for i in range(N_EMPLOYEES):
            dept = DEPARTMENTS[int(self.rng.choice(len(DEPARTMENTS), p=weights / weights.sum()))][0]
            city, st = self.choice(CITIES)
            manager = self.r() < 0.18
            traveler = (dept in ("SAL", "CS") and self.r() < 0.75) or self.r() < 0.12
            term = None
            if self.r() < 0.08:
                term = self._month_start(int(self.rng.integers(3, 9))) + dt.timedelta(days=int(self.rng.integers(0, 27)))
            rows.append(
                dict(
                    employee_id=f"e_{i:03d}",
                    full_name=f"{self.choice(FIRST)} {self.choice(LAST)}",
                    dept_id=dept,
                    title=("Manager" if manager else "IC") + f", {dept}",
                    is_manager=manager,
                    city=city,
                    state=st,
                    is_traveler=traveler,
                    single_txn_limit=5000.0 if manager else 2000.0,
                    termination_date=term,
                )
            )
        return pd.DataFrame(rows)

    def active(self, emp, day: dt.date) -> bool:
        t = emp.termination_date
        return t is None or pd.isna(t) or day < t

    # ------------------------------------------------------------------ descriptors
    def descriptor(self, merchant_id: str, city: str = "", st: str = "") -> str:
        m = MERCHANTS_BY_ID[merchant_id]
        tpl = self.choice(m.descriptors)
        s = tpl.format(
            ref=self.ref(),
            n=int(self.rng.integers(100, 99999)),
            store=f"#{int(self.rng.integers(10, 9999))}",
            city=city.upper(),
            st=st,
            rest=self.choice(LONGTAIL_RESTAURANTS).upper().replace("'", ""),
            event=self.choice(EVENTS),
        )
        return self._network_format(s)

    def longtail_descriptor(self, name: str, city: str) -> str:
        nm = name.upper()
        if self.r() < 0.5:
            nm = nm.replace("'", "").replace("&", "AND" if self.r() < 0.5 else "&")
        s = f"{self.choice(LONGTAIL_PREFIXES)}{nm}"
        if self.r() < 0.5:
            s += f" {city.upper()}"
        return self._network_format(s)

    def _network_format(self, s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        if self.r() < 0.55 and len(s) > 25:
            s = s[:25].rstrip()  # card networks truncate merchant names
        if self.r() < 0.1:
            s = s.lower()
        return s

    def mcc_for(self, merchant_id: str) -> str:
        m = MERCHANTS_BY_ID[merchant_id]
        if self.r() < 0.05:
            return self.choice(["5999", "7399", "8999"])  # generic / misleading MCC
        return m.mcc

    # ------------------------------------------------------------------ emit
    def add(self, **kw) -> Txn:
        t = Txn(txn_id=self.new_id(), **kw)
        self.txns.append(t)
        return t

    def emit_known(self, emp, day, merchant_id, amount, memo, gold_gl=None, city=None, st=None, **latent) -> Txn:
        m = MERCHANTS_BY_ID[merchant_id]
        return self.add(
            posted=day,
            employee_id=emp.employee_id,
            merchant_id=merchant_id,
            descriptor=self.descriptor(merchant_id, city or emp.city, st or emp.state),
            mcc=self.mcc_for(merchant_id),
            amount=round(amount, 2),
            memo=memo,
            kind=m.kind,
            gold_gl=gold_gl or m.gl,
            **latent,
        )

    # ------------------------------------------------------------------ generators
    def gen_saas(self):
        saas = [m for m in MERCHANTS if m.kind in ("saas", "cloud")]
        emps = self.employees
        admins = emps[emps.dept_id.isin(["GA", "ENG", "DAT"])].to_dict("records")
        for m in saas:
            n_subs = 2 if m.merchant_id in ("m_loom", "m_zoom", "m_notion", "m_openai", "m_anthropic", "m_miro") else 1
            for k in range(n_subs):
                owner = self.choice(admins) if k == 0 else self.choice(emps.to_dict("records"))
                start_m = 0 if m.in_registry else int(self.rng.integers(2, 9))
                base = self.money(*m.price)
                creep_m = int(self.rng.integers(4, 11)) if self.r() < 0.35 else None
                creep = float(self.rng.uniform(1.06, 1.30))
                bill_day = int(self.rng.integers(1, 28))
                price = base
                for mo in range(start_m, N_MONTHS):
                    if creep_m is not None and mo == creep_m:
                        price = round(price * creep, 2)
                    amt = price
                    if m.kind == "cloud":  # usage-based
                        amt = round(price * float(self.rng.uniform(0.85, 1.25)), 2)
                    elif self.r() < 0.15:  # seat changes
                        amt = round(price * float(self.rng.uniform(0.95, 1.1)), 2)
                    day = self.month_days(mo)[min(bill_day, len(self.month_days(mo))) - 1]
                    zombie = not self.active(type("E", (), owner)(), day)
                    seats = int(self.rng.integers(5, 60))
                    t = self.emit_known(
                        type("E", (), owner)(),
                        day,
                        m.merchant_id,
                        amt,
                        self.choice(["", "", "monthly subscription", f"{m.name} plan", "renewal"]),
                        auto_billed=True,
                        receipt=dict(
                            total=amt,
                            lines=f"Invoice INV-{self.ref(6)} | Plan: {self.choice(['Business', 'Team', 'Pro', 'Enterprise'])} | Seats: {seats}",
                        )
                        if self.r() < 0.95
                        else None,
                    )
                    t.extra.update(
                        sub_key=f"{m.merchant_id}:{owner['employee_id']}",
                        zombie=zombie,
                        owner_dept=owner["dept_id"],
                        creep_month=creep_m,
                    )

    def gen_ads(self):
        mkt = self.employees[self.employees.dept_id == "MKT"].to_dict("records")
        for mo in range(N_MONTHS):
            for mid in ("m_googleads", "m_linkedin", "m_meta"):
                for _ in range(int(self.rng.integers(1, 4))):
                    e = type("E", (), self.choice(mkt))()
                    m = MERCHANTS_BY_ID[mid]
                    amt = self.money(*m.price) / 2
                    self.emit_known(
                        e,
                        self.weekday_in(mo, 0.3),
                        mid,
                        amt,
                        self.choice(["", "campaign spend", "Q-pipeline ads"]),
                        auto_billed=True,
                        receipt=dict(total=round(amt, 2), lines="Advertising services"),
                    )
            if self.r() < 0.5:
                e = type("E", (), self.choice(mkt))()
                amt = self.money(60, 900)
                self.emit_known(
                    e,
                    self.weekday_in(mo),
                    "m_vistaprint",
                    amt,
                    self.choice(["booth banners", "business cards", "event swag"]),
                    receipt=dict(total=amt, lines="Print order"),
                )

    def _meal_receipt(self, amount: float, alcohol: bool) -> tuple[dict, bool]:
        items = [self.choice(FOOD) for _ in range(int(self.rng.integers(2, 7)))]
        if alcohol:
            items += [self.choice(ALCOHOL) for _ in range(int(self.rng.integers(1, 4)))]
        self.rng.shuffle(items)
        subtotal = round(amount / float(self.rng.uniform(1.15, 1.22)), 2)  # tip + tax on top
        mismatch = False
        if self.r() < 0.03:
            subtotal = round(subtotal * float(self.rng.uniform(0.45, 0.75)), 2)
            mismatch = True
        return dict(total=subtotal, lines=" | ".join(items)), mismatch

    def gen_trips(self):
        for emp in self.employees.itertuples():
            if not emp.is_traveler:
                continue
            rate = 1.0 if emp.dept_id == "SAL" else 0.6 if emp.dept_id == "CS" else 0.2
            for mo in range(N_MONTHS):
                for _ in range(int(self.rng.poisson(rate))):
                    start = self.weekday_in(mo, 0.0)
                    if not self.active(emp, start):
                        continue
                    nights = int(self.rng.integers(1, 4))
                    dest = self.choice([d for d in DESTS if d != emp.city])
                    trip = dict(is_travel=True, dest_city=dest)
                    # airfare (booked ~2 weeks ahead, may land on a weekend)
                    fare = "business" if self.r() < 0.08 else "premium" if self.r() < 0.13 else "economy"
                    air = self.choice(["m_delta", "m_united", "m_american", "m_jetblue"])
                    amt = self.money(220, 900) * (2.6 if fare == "business" else 1.25 if fare == "premium" else 1)
                    route = f"{AIRPORTS[emp.city]}-{AIRPORTS[dest]} RT"
                    label = self.choice(FARES[fare])
                    self.emit_known(
                        emp,
                        max(START, start - dt.timedelta(days=int(self.rng.integers(5, 25)))),
                        air,
                        amt,
                        self.choice([f"flight to {dest}", f"{dest} customer visit", f"travel {route}", "flights"]),
                        fare=fare,
                        receipt=dict(total=round(amt, 2), lines=f"E-ticket {self.ref(6)} | {route} | Cabin: {label}"),
                        **trip,
                    )
                    # lodging
                    cap = POLICY["lodging_cap_high"] if dest in HIGH_COST_CITIES else POLICY["lodging_cap"]
                    nightly = round(
                        cap * float(self.rng.uniform(1.03, 1.45))
                        if self.r() < 0.12
                        else cap * float(self.rng.uniform(0.45, 0.97)),
                        2,
                    )
                    total = round(nightly * nights * 1.15, 2)
                    hotel = self.choice(["m_marriott", "m_hilton", "m_hyatt", "m_airbnb"])
                    rate_txt = self.choice(
                        [
                            f"Room rate {nightly:.2f}/night x {nights}",
                            f"{nights} nights @ ${nightly:.2f}",
                            f"Nightly: ${nightly:.2f} ({nights} nts)",
                            f"Accommodation {nights}n avg {nightly:.0f}",
                        ]
                    )
                    self.emit_known(
                        emp,
                        start + dt.timedelta(days=nights),
                        hotel,
                        total,
                        self.choice([f"hotel {dest}", f"lodging - {dest} trip", "hotel", ""]),
                        nightly_rate=nightly,
                        city=dest,
                        st="",
                        receipt=dict(total=total, lines=f"{rate_txt} | Taxes & fees"),
                        **trip,
                    )
                    # ground + meals during trip
                    for d in range(nights + 1):
                        day = start + dt.timedelta(days=d)
                        for _ in range(int(self.rng.integers(0, 3))):
                            g = "m_uber" if self.r() < 0.6 else "m_lyft"
                            amt = self.money(14, 85)
                            self.emit_known(
                                emp,
                                day,
                                g,
                                amt,
                                self.choice(["", "airport", "ride to customer office", "uber to hotel"]),
                                city=dest,
                                st="",
                                receipt=dict(total=amt, lines="Trip fare") if amt >= 75 or self.r() < 0.5 else None,
                                **trip,
                            )
                        if self.r() < 0.7:
                            self._longtail_meal(emp, day, "solo", dest, trip)
                    if self.r() < 0.1:
                        amt = self.money(120, 420)
                        self.emit_known(
                            emp,
                            start + dt.timedelta(days=nights),
                            "m_hertz",
                            amt,
                            "rental car",
                            city=dest,
                            st="",
                            receipt=dict(total=amt, lines="Rental agreement"),
                            **trip,
                        )

    def _longtail_meal(self, emp, day, meal_type, city, latent=None, attendees=None, memo=None):
        latent = dict(latent or {})
        name = self.choice(LONGTAIL_RESTAURANTS)
        n = attendees or 1
        pp = self.money(14, 45) if meal_type == "solo" else self.money(28, 115) if meal_type == "client" else self.money(12, 48)
        amount = round(pp * n, 2)
        alcohol = (
            (meal_type == "client" and self.r() < 0.45)
            or (meal_type == "team" and self.r() < 0.2)
            or (meal_type == "solo" and self.r() < 0.06)
        )
        receipt, mismatch = self._meal_receipt(amount, alcohol)
        if amount >= POLICY["receipt_required_over"] and self.r() < 0.06:
            receipt = None
        mcc = "5813" if (alcohol and self.r() < 0.3) else self.choice(["5812", "5812", "5814"])
        gl = "6300" if meal_type == "client" else "6305"
        return self.add(
            posted=day,
            employee_id=emp.employee_id,
            merchant_id=f"lt_{re.sub(r'[^a-z]', '', name.lower())}",
            descriptor=self.longtail_descriptor(name, city),
            mcc=mcc,
            amount=amount,
            memo=memo if memo is not None else self.choice(["", "dinner", "lunch", "meal while traveling", "breakfast"]),
            kind="meal",
            gold_gl=gl,
            attendees=n,
            meal_type=meal_type,
            has_alcohol=alcohol,
            receipt=receipt,
            receipt_mismatch=mismatch and receipt is not None,
            **latent,
        )

    def gen_meals(self):
        for emp in self.employees.itertuples():
            client_rate = 1.3 if emp.dept_id == "SAL" else 0.6 if emp.dept_id == "CS" else 0.05
            team_rate = 0.5 if emp.is_manager else 0.03
            for mo in range(N_MONTHS):
                for _ in range(int(self.rng.poisson(client_rate))):
                    day = self.weekday_in(mo)
                    if not self.active(emp, day):
                        continue
                    n = int(self.rng.integers(2, 7))
                    c = self.choice(CLIENTS)
                    memo = self.drifted(
                        mo,
                        [
                            f"dinner w/ {c} ({n} ppl)",
                            f"client mtg - {c}, {n} attendees",
                            f"{c} QBR dinner, party of {n}",
                            f"lunch with {c} team x{n}",
                            f"{c} dinner",
                            "client dinner",
                            f"drinks + dinner {c} {n}pax",
                        ],
                        [
                            f"hosted {c} execs ({n})",
                            f"{c} x {n} - steakhouse",
                            f"wined & dined {c}, {n} guests",
                            f"renewal celebration w {c}",
                            f"{c} buying committee dinner, {n}",
                        ],
                    )
                    self._longtail_meal(emp, day, "client", emp.city, attendees=n, memo=memo)
                for _ in range(int(self.rng.poisson(team_rate))):
                    day = self.weekday_in(mo)
                    if not self.active(emp, day):
                        continue
                    n = int(self.rng.integers(4, 13))
                    memo = self.drifted(
                        mo,
                        [
                            f"team lunch ({n})",
                            f"offsite dinner w/ team, {n} ppl",
                            "sprint retro pizza",
                            f"team celebration x{n}",
                            f"onboarding lunch for new hires - {n}",
                            "team dinner",
                        ],
                        [
                            f"eng pod boba + dinner ({n})",
                            f"design crit lunch x{n}",
                            f"hackweek dinner, {n} of us",
                            "launch night food",
                            f"q3 kickoff lunch - {n}",
                        ],
                    )
                    if self.r() < 0.35:
                        mid = self.choice(["m_doordash", "m_ubereats"])
                        amt = round(self.money(12, 40) * n, 2)
                        alcohol = self.r() < 0.12
                        receipt, mismatch = self._meal_receipt(amt, alcohol)
                        self.emit_known(
                            emp,
                            day,
                            mid,
                            amt,
                            memo,
                            gold_gl="6305",
                            attendees=n,
                            meal_type="team",
                            has_alcohol=alcohol,
                            receipt=receipt,
                            receipt_mismatch=mismatch,
                        )
                    else:
                        self._longtail_meal(emp, day, "team", emp.city, attendees=n, memo=memo)
                if self.r() < 0.25:  # coffee chats
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        mid = self.choice(["m_starbucks", "m_sweetgreen", "m_chipotle"])
                        amt = self.money(5, 40)
                        self.emit_known(
                            emp,
                            day,
                            mid,
                            amt,
                            self.choice(["coffee w/ candidate", "1:1 coffee", "lunch interview", ""]),
                            meal_type="solo",
                            attendees=2,
                            receipt=dict(total=amt, lines="Coffee | Pastry") if self.r() < 0.6 else None,
                        )
                if self.r() < 0.02:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        self.emit_known(
                            emp,
                            day,
                            "m_applebees",
                            self.money(40, 160),
                            "team dinner",
                            gold_gl="6305",
                            meal_type="team",
                            attendees=4,
                            receipt=dict(total=60.0, lines="Riblets | Fries | Iced tea"),
                        )

    def gen_office_and_misc(self):
        office_memos = [
            "usb-c hub",
            "monitor for new hire",
            "standing desk mat",
            "printer toner",
            "keyboard + mouse",
            "webcam for conf room",
            "HDMI cables",
            "whiteboard markers",
            "laptop stand",
        ]
        training_memos = [
            "books for team reading group",
            "kindle edition - designing data-intensive apps",
            "o'reilly book on dbt",
            "textbook for ML course",
            "certification exam prep book",
        ]
        snack_memos = ["snacks for office", "coffee pods for kitchen", "team snacks for offsite", "LaCroix for the office"]
        office_drift = ["dongle", "ergonomic chair cushion", "label maker", "replacement charger", "desk lamp for wfh setup"]
        training_drift = [
            "dbt fundamentals book",
            "kindle: staff engineer's path",
            "llm engineering handbook",
            "stats refresher book",
        ]
        snack_drift = ["boba for the pod", "kombucha restock", "fruit delivery for floor", "snack box for hackweek"]
        personal_drift = [
            "this one's on me, will pay back",
            "mixed up my cards",
            "meant to use my own card",
            "for home, not work",
        ]
        personal_memos = [
            "personal - will repay",
            "personal purchase, will reimburse",
            "accidentally used corp card",
            "wrong card, sorry",
            "oops personal",
            "my bad - not business",
        ]
        for emp in self.employees.itertuples():
            for mo in range(N_MONTHS):
                if self.r() < 0.35:
                    day = self.weekday_in(mo)
                    if not self.active(emp, day):
                        continue
                    roll = self.r()
                    if roll < 0.06:
                        amt = self.money(15, 240)
                        self.emit_known(
                            emp,
                            day,
                            self.choice(["m_amazon", "m_amazon", "m_uber"]),
                            amt,
                            self.drifted(mo, personal_memos, personal_drift),
                            gold_gl="9999",
                            personal=True,
                            receipt=dict(total=amt, lines="Order") if self.r() < 0.5 else None,
                        )
                    elif roll < 0.62:
                        amt = self.money(15, 400)
                        self.emit_known(
                            emp,
                            day,
                            "m_amazon",
                            amt,
                            self.drifted(mo, office_memos, office_drift),
                            receipt=dict(total=amt, lines="Order details"),
                        )
                    elif roll < 0.77:
                        amt = self.money(20, 120)
                        self.emit_known(
                            emp,
                            day,
                            "m_amazon",
                            amt,
                            self.drifted(mo, training_memos, training_drift),
                            gold_gl="6800",
                            receipt=dict(total=amt, lines="Books"),
                        )
                    else:
                        amt = self.money(20, 180)
                        self.emit_known(
                            emp,
                            day,
                            "m_amazon",
                            amt,
                            self.drifted(mo, snack_memos, snack_drift),
                            gold_gl="6305",
                            receipt=dict(total=amt, lines="Grocery"),
                        )
                if self.r() < 0.06:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        if self.r() < 0.5:
                            amt = self.money(1200, 2700)
                            self.emit_known(
                                emp,
                                day,
                                "m_apple",
                                amt,
                                self.choice(["MacBook for new hire", "laptop replacement", "iPad for demos"]),
                                receipt=dict(total=amt, lines="Hardware"),
                            )
                        else:
                            amt = self.money(3, 30)
                            self.emit_known(
                                emp,
                                day,
                                "m_apple",
                                amt,
                                self.choice(["iCloud storage", "app subscription", ""]),
                                gold_gl="6100",
                                extra={"apple_bill": True},
                                receipt=None,
                            )
                if self.r() < 0.05:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        mid = self.choice(["m_staples", "m_bestbuy"])
                        amt = self.money(*MERCHANTS_BY_ID[mid].price)
                        self.emit_known(
                            emp, day, mid, amt, self.choice(office_memos), receipt=dict(total=amt, lines="Store receipt")
                        )
                if self.r() < 0.06:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        mid = self.choice(["m_fedex", "m_ups", "m_usps"])
                        amt = self.money(*MERCHANTS_BY_ID[mid].price)
                        self.emit_known(
                            emp,
                            day,
                            mid,
                            amt,
                            self.choice(["ship laptop to remote hire", "return equipment", "contract mailing", ""]),
                            receipt=dict(total=amt, lines="Shipment") if amt >= 75 or self.r() < 0.5 else None,
                        )
                if self.r() < 0.04:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        mid = self.choice(["m_coursera", "m_oreilly", "m_eventbrite"])
                        amt = self.money(*MERCHANTS_BY_ID[mid].price)
                        gl = "6800"
                        memo = self.choice(["conference ticket", "online course", "learning budget", "workshop registration"])
                        if mid == "m_eventbrite" and emp.dept_id == "MKT" and self.r() < 0.6:
                            gl, memo = "6500", self.choice(["meetup sponsorship", "booth at community event", "sponsor table"])
                        self.emit_known(emp, day, mid, amt, memo, gold_gl=gl, receipt=dict(total=amt, lines="Registration"))
                if emp.dept_id in ("MKT", "ENG", "PRD") and self.r() < 0.05:
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        mid = self.choice(["m_upwork", "m_fiverr"])
                        amt = self.money(*MERCHANTS_BY_ID[mid].price)
                        self.emit_known(
                            emp,
                            day,
                            mid,
                            amt,
                            self.choice(["contract designer", "landing page copy", "data labeling contractor", "logo refresh"]),
                            receipt=dict(total=amt, lines="Contract payment"),
                        )
                if self.r() < 0.012:  # prohibited merchants
                    day = self.weekday_in(mo, 0.4)
                    if self.active(emp, day):
                        mid = self.choice(["m_netflix", "m_spotify", "m_equinox", "m_draftkings", "m_giftcard", "m_atm"])
                        amt = self.money(*MERCHANTS_BY_ID[mid].price)
                        memo = self.choice(["", "", "team gift cards", "client gift", "wellness", "streaming for demo"])
                        self.emit_known(emp, day, mid, amt, memo, personal=True, receipt=None)
                if self.r() < 0.02:  # long-tail shop
                    day = self.weekday_in(mo)
                    if self.active(emp, day):
                        name = self.choice(LONGTAIL_SHOPS)
                        amt = self.money(20, 600)
                        self.add(
                            posted=day,
                            employee_id=emp.employee_id,
                            merchant_id=f"lt_{re.sub(r'[^a-z]', '', name.lower())}",
                            descriptor=self.longtail_descriptor(name, emp.city),
                            mcc=self.choice(["5251", "5943", "5992", "5999", "5732"]),
                            amount=amt,
                            memo=self.choice(["office supplies", "event flowers", "printing for offsite", "keys cut", ""]),
                            kind="office",
                            gold_gl="6400" if "Florist" not in name else "6500",
                            receipt=dict(total=amt, lines="Receipt") if self.r() < 0.9 else None,
                        )

    def gen_splits(self):
        for emp in self.employees.itertuples():
            if self.r() < 0.12:
                mo = int(self.rng.integers(0, N_MONTHS))
                day = self.weekday_in(mo, 0.0)
                if not self.active(emp, day):
                    continue
                mid = self.choice(["m_bestbuy", "m_apple", "m_upwork", "m_amazon"])
                total = emp.single_txn_limit * float(self.rng.uniform(1.1, 2.0))
                k = 2 if total < emp.single_txn_limit * 1.8 else 3
                parts = np.array(self.rng.dirichlet(np.ones(k) * 6)) * total
                if parts.max() > emp.single_txn_limit:
                    parts = np.full(k, total / k)
                grp = f"split_{emp.employee_id}_{mo}"
                memo = self.choice(["equipment for team", "contractor milestone", "conference room AV", "monitors for pod"])
                for p in parts:
                    self.emit_known(
                        emp,
                        day,
                        mid,
                        float(p) * float(self.rng.uniform(0.98, 1.02)),
                        memo,
                        split_group=grp,
                        receipt=dict(total=round(float(p), 2), lines="Order") if self.r() < 0.8 else None,
                    )

    def gen_duplicates(self):
        candidates = [t for t in self.txns if not t.auto_billed and t.split_group is None and t.amount > 20]
        idx = self.rng.choice(len(candidates), size=int(len(candidates) * 0.005), replace=False)
        for i in idx:
            o = candidates[int(i)]
            d = self.add(
                posted=o.posted + dt.timedelta(days=int(self.rng.integers(0, 3))),
                employee_id=o.employee_id,
                merchant_id=o.merchant_id,
                descriptor=o.descriptor,
                mcc=o.mcc,
                amount=o.amount,
                memo=o.memo,
                kind=o.kind,
                gold_gl=o.gold_gl,
                is_travel=o.is_travel,
                attendees=o.attendees,
                meal_type=o.meal_type,
                has_alcohol=o.has_alcohol,
                fare=o.fare,
                nightly_rate=o.nightly_rate,
                dest_city=o.dest_city,
                personal=o.personal,
                receipt=o.receipt,
                receipt_mismatch=o.receipt_mismatch,
            )
            d.duplicate_of = o.txn_id

    # ------------------------------------------------------------------ gold labels
    def gold_decision(self, t: Txn, emp) -> tuple[str, list[str]]:
        reject, review = [], []
        m = MERCHANTS_BY_ID.get(t.merchant_id)
        if t.personal:
            reject.append("5.1" if (m and m.kind == "personal") else "5.2")
        if t.split_group:
            reject.append("7.1")
        if t.has_alcohol and t.meal_type != "client":
            reject.append("2.2")
        if t.duplicate_of:
            review.append("9.1")
        if t.fare == "business":
            review.append("3.1")
        if t.nightly_rate is not None:
            cap = POLICY["lodging_cap_high"] if t.dest_city in HIGH_COST_CITIES else POLICY["lodging_cap"]
            if t.nightly_rate > cap:
                review.append("3.2")
        if t.meal_type in ("client", "team") and t.attendees:
            cap = POLICY["meal_client_pp"] if t.meal_type == "client" else POLICY["meal_team_pp"]
            if t.amount / t.attendees > cap:
                review.append("2.1")
        if t.amount >= POLICY["receipt_required_over"] and t.receipt is None and not t.auto_billed:
            review.append("6.1")
        if t.receipt_mismatch:
            review.append("6.2")
        if m is not None and not m.in_registry and t.amount > POLICY["new_software_review_over"]:
            review.append("4.1")
        if t.amount > emp.single_txn_limit and not t.auto_billed and not t.is_travel:
            review.append("7.2")
        if t.posted.weekday() >= 5 and not t.is_travel and not t.auto_billed and not t.personal:
            review.append("8.1")
        if reject:
            return "REJECT", sorted(set(reject))
        if review:
            return "NEEDS_REVIEW", sorted(set(review))
        return "APPROVE", []

    # ------------------------------------------------------------------ build
    def build(self) -> dict[str, pd.DataFrame]:
        self.gen_saas()
        self.gen_ads()
        self.gen_trips()
        self.gen_meals()
        self.gen_office_and_misc()
        self.gen_splits()
        self.gen_duplicates()
        self.txns.sort(key=lambda t: (t.posted, t.txn_id))
        emp_by_id = {e.employee_id: e for e in self.employees.itertuples()}

        txn_rows, rec_rows, gold_rows = [], [], []
        for t in self.txns:
            txn_rows.append(
                dict(
                    txn_id=t.txn_id,
                    posted_date=t.posted,
                    employee_id=t.employee_id,
                    descriptor=t.descriptor,
                    mcc=t.mcc,
                    amount=t.amount,
                    currency="USD",
                    memo=t.memo,
                    card_last4=f"{(int(t.employee_id[2:]) * 7919) % 9000 + 1000}",
                )
            )
            if t.receipt is not None:
                rec_rows.append(
                    dict(
                        receipt_id=f"r_{t.txn_id[3:]}",
                        txn_id=t.txn_id,
                        receipt_total=round(t.receipt["total"], 2),
                        line_items=t.receipt["lines"],
                    )
                )
            decision, reasons = self.gold_decision(t, emp_by_id[t.employee_id])
            m = MERCHANTS_BY_ID.get(t.merchant_id)
            gold_rows.append(
                dict(
                    txn_id=t.txn_id,
                    true_merchant_id=t.merchant_id,
                    true_in_registry=bool(m and m.in_registry),
                    gold_gl=t.gold_gl,
                    gold_decision=decision,
                    gold_reasons="|".join(reasons),
                    month_index=(t.posted.year - START.year) * 12 + t.posted.month - START.month,
                    has_alcohol=t.has_alcohol,
                    fare=t.fare,
                    nightly_rate=t.nightly_rate,
                    attendees=t.attendees,
                    meal_type=t.meal_type,
                    personal=t.personal,
                    split_group=t.split_group,
                    duplicate_of=t.duplicate_of,
                    zombie=bool(t.extra.get("zombie", False)),
                    sub_key=t.extra.get("sub_key"),
                )
            )
        txns = pd.DataFrame(txn_rows)
        gold = pd.DataFrame(gold_rows)

        # Historical human reviews for the first 8 months: lenient and a bit noisy, the
        # way real reviewer behaviour is (Ramp notes finance teams are lenient graders).
        # Assumed rates: 6% of review-worthy items waved through, 0.2% of clean items flagged,
        # 2% GL booking errors.
        hist = gold[gold.month_index < 8][["txn_id", "gold_gl", "gold_decision"]].copy()
        noise = self.rng.random(len(hist))
        lenient = (hist.gold_decision == "NEEDS_REVIEW") & (noise < self.leniency)
        hist.loc[lenient, "gold_decision"] = "APPROVE"
        strict = (hist.gold_decision == "APPROVE") & (noise > 0.998)
        hist.loc[strict, "gold_decision"] = "NEEDS_REVIEW"
        gl_noise = self.rng.random(len(hist)) < 0.02
        codes = list(GL_ACCOUNTS)
        hist.loc[gl_noise, "gold_gl"] = [self.choice(codes) for _ in range(int(gl_noise.sum()))]
        hist = hist.rename(columns={"gold_gl": "booked_gl", "gold_decision": "review_decision"})

        registry = pd.DataFrame(
            [
                dict(
                    merchant_id=m.merchant_id,
                    merchant_name=m.name,
                    aliases="|".join(m.aliases),
                    default_gl=m.gl,
                    merchant_kind=m.kind,
                    require_any="|".join(m.require_any),
                    forbid_any="|".join(m.forbid_any),
                )
                for m in MERCHANTS
                if m.in_registry
            ]
        )
        emps = self.employees.copy()
        return dict(
            raw_card_transactions=txns,
            raw_receipts=pd.DataFrame(rec_rows),
            raw_employees=emps,
            raw_departments=pd.DataFrame([dict(dept_id=d, dept_name=n) for d, n, _ in DEPARTMENTS]),
            raw_merchant_registry=registry,
            raw_gl_accounts=pd.DataFrame([dict(gl_code=k, gl_name=v) for k, v in GL_ACCOUNTS.items()]),
            raw_historical_reviews=hist,
            gold_transactions=gold,
        )


def generate(seed: int = 7, **scenario) -> dict[str, pd.DataFrame]:
    """`scenario` may override `drift_rate` and `leniency` (used by the sensitivity analysis)."""
    return World(seed, **scenario).build()
