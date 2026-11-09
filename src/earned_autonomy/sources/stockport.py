"""Stockport Metropolitan Borough Council corporate purchase-card transactions (UK).

Published monthly under the Open Government Licence v3.0 via data.gov.uk:
https://www.data.gov.uk/dataset/purchase-card-transactions

Every file carries the raw card-network merchant string, the merchant category, the
department, the amount, and the *Purpose of Expenditure* that council finance staff booked.
That last field is real general-ledger coding ground truth, which is what makes this
dataset useful: GL-coding models can be evaluated on decisions humans actually made.

`download()` is idempotent: it fetches the CKAN resource list, downloads each monthly CSV
once, and writes `data/stockport/manifest.json` with URL, SHA-256, bytes and row count so a
reviewer can verify they are scoring on exactly the same bytes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from ..paths import DATA

CKAN = "https://ckan.publishing.service.gov.uk/api/3/action/package_show?id=purchase-card-transactions"
OUT = DATA / "stockport"
MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}
UA = {"User-Agent": "earned-autonomy research (open data; github.com/VijayChamakuri/earned-autonomy)"}


def _get(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return r.read()
        except Exception:  # noqa: BLE001 - retry any transient network error
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError("unreachable")


def period_from_url(url: str) -> str | None:
    """'Purchase+Card+-+August+2026.csv' -> '2026-08'; 'Purchasing+Card+-+February+17.csv' -> '2017-02'."""
    name = urllib.parse.unquote_plus(url.rsplit("/", 1)[-1]).lower()
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s_-]*(\d{2,4})", name)
    if not m:
        return None
    year = int(m.group(2))
    year = year + 2000 if year < 100 else year
    return f"{year:04d}-{MONTHS[m.group(1)]:02d}"


def list_resources() -> list[dict]:
    meta = json.loads(_get(CKAN))["result"]
    out = []
    for r in meta["resources"]:
        if (r.get("format") or "").upper() != "CSV":
            continue
        period = period_from_url(r["url"])
        out.append({"url": r["url"], "period": period, "resource_id": r.get("id")})
    return sorted(out, key=lambda r: (r["period"] or "9999", r["url"]))


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def download(force: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() and not force else {"files": []}
    have = {f["url"] for f in manifest["files"]}
    for res in list_resources():
        if res["url"] in have:
            continue
        raw = _get(res["url"])
        fname = f"{res['period'] or 'unknown'}_{hashlib.sha256(res['url'].encode()).hexdigest()[:8]}.csv"
        (OUT / fname).write_bytes(raw)
        text = decode(raw)
        rows = sum(1 for _ in csv.reader(io.StringIO(text, newline=""))) - 1
        manifest["files"].append(
            {
                "period": res["period"],
                "url": res["url"],
                "file": fname,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "rows_including_blank": rows,
            }
        )
    manifest["files"].sort(key=lambda f: (f["period"] or "9999", f["url"]))
    manifest["source"] = CKAN
    manifest["licence"] = "Open Government Licence v3.0"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


# ----------------------------------------------------------------------------- parsing
# The publisher changed format three times. Column names are matched after normalisation
# so every vintage lands in one schema; `schema_version` records which layout a row came from.
COLUMN_MAP = {
    "organisation name": "organisation",
    "department where exp. incurred": "department",
    "service category label": "service_category",
    "supplier (beneficiary)": "merchant_raw",
    "purpose of expenditure": "purpose",
    "procurement (merchant) category": "merchant_category",
    "purchase date": "purchase_date",
    "settlement date": "settlement_date",
    "transaction number": "transaction_number",
    "net amount (£)": "amount_net",
    "net amount (�)": "amount_net",
    "gross amount (£)": "amount_gross",
    "gross amount (�)": "amount_gross",
}
OUTPUT_COLUMNS = [
    "period",
    "source_file",
    "source_row",
    "schema_version",
    "organisation",
    "department",
    "service_category",
    "merchant_raw",
    "purpose",
    "merchant_category",
    "purchase_date",
    "settlement_date",
    "transaction_number",
    "amount",
    "amount_basis",
]


def _rows_from_file(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    if raw[:2] == b"PK":  # an .xlsx published with a .csv extension (May 2024)
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        out = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            out.append(["" if v is None else (v.strftime("%d/%m/%Y") if hasattr(v, "strftime") else str(v)) for v in row])
        return out
    return list(csv.reader(io.StringIO(decode(raw), newline="")))


def parse_file(path: Path, period: str) -> list[dict]:
    rows = _rows_from_file(path)
    hdr_idx = next(i for i, r in enumerate(rows[:10]) if any(c.strip().lower() == "organisation name" for c in r))
    header = [COLUMN_MAP.get(c.strip().lower(), c.strip().lower()) for c in rows[hdr_idx]]
    version = (
        "v3_gross_6col" if "amount_gross" in header else ("v1_net_10col" if "transaction_number" in header else "v2_net_8col")
    )
    out = []
    for i, r in enumerate(rows[hdr_idx + 1 :], start=hdr_idx + 2):
        rec = {h: (r[j].strip() if j < len(r) else "") for j, h in enumerate(header) if h}
        if not rec.get("merchant_raw") and not rec.get("amount_net") and not rec.get("amount_gross"):
            continue  # blank / footer rows
        amt = rec.get("amount_net") or rec.get("amount_gross") or ""
        out.append(
            {
                "period": period,
                "source_file": path.name,
                "source_row": i,
                "schema_version": version,
                "organisation": rec.get("organisation", ""),
                "department": rec.get("department") or None,
                "service_category": rec.get("service_category") or None,
                "merchant_raw": rec.get("merchant_raw", ""),
                "purpose": rec.get("purpose") or None,
                "merchant_category": rec.get("merchant_category") or None,
                "purchase_date": rec.get("purchase_date") or None,
                "settlement_date": rec.get("settlement_date") or None,
                "transaction_number": rec.get("transaction_number") or None,
                "amount": amt.replace(",", "").replace("£", "") or None,
                "amount_basis": "gross" if rec.get("amount_gross") else "net",
            }
        )
    return out


def load_raw() -> pd.DataFrame:
    """All files, unioned into one *untyped* table (typing and cleaning happen in dbt staging)."""

    manifest = json.loads((OUT / "manifest.json").read_text())
    recs: list[dict] = []
    seen_sha: set[str] = set()
    for f in manifest["files"]:
        if f["sha256"] in seen_sha:  # the publisher listed one file twice (2020-04)
            continue
        seen_sha.add(f["sha256"])
        recs.extend(parse_file(OUT / f["file"], f["period"]))
    return pd.DataFrame(recs, columns=OUTPUT_COLUMNS).astype(str).replace({"None": None, "": None})


def load_to_warehouse() -> dict:
    """Write raw_stockport.card_transactions (untyped) and raw_stockport.source_files."""
    import duckdb
    import pandas as pd

    from ..paths import DB_PATH

    manifest = json.loads((OUT / "manifest.json").read_text())
    df = load_raw()
    counts = df.groupby("source_file").size()
    seen: set[str] = set()
    files = []
    for f in manifest["files"]:
        included = f["sha256"] not in seen
        seen.add(f["sha256"])
        files.append(
            {
                "source_file": f["file"],
                "period": f["period"],
                "url": f["url"],
                "sha256": f["sha256"],
                "bytes": f["bytes"],
                "parsed_rows": int(counts.get(f["file"], 0)),
                "included": included,
            }
        )
    files_df = pd.DataFrame(files)
    con = duckdb.connect(str(DB_PATH))
    con.execute("create schema if not exists raw_stockport")
    for name, frame in (("card_transactions", df), ("source_files", files_df)):
        con.register("frame", frame)
        con.execute(f"create or replace table raw_stockport.{name} as select * from frame")
        con.unregister("frame")
    con.close()
    return {"files": len(files_df), "included_files": int(files_df.included.sum()), "rows": len(df)}
