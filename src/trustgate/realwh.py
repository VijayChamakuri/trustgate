"""Summarise the real-data warehouse: data quality, schema drift and spend findings.

Reads only governed dbt models (stockport_* schemas) and writes results/real/warehouse.json.
"""

from __future__ import annotations

import json

import duckdb

from .paths import DB_PATH, RESULTS


def summarise() -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    q = lambda sql: con.sql(sql).fetchall()  # noqa: E731
    files = q(
        "select count(*), count(*) filter (where included), sum(bytes), min(period), max(period) from raw_stockport.source_files"
    )[0]
    lines = q(
        "select count(*), sum(amount), count(*) filter (where is_refund), count(*) filter (where gl_account is not null),"
        " count(*) filter (where is_placeholder_label), count(*) filter (where is_settled_before_purchase),"
        " count(distinct merchant_key), count(distinct merchant_raw) from stockport_staging.stg_stockport__card_transactions"
    )[0]
    versions = q(
        "select schema_version, min(statement_month), max(statement_month), count(*) "
        "from stockport_staging.stg_stockport__card_transactions group by 1 order by 2"
    )
    conc = q(
        "select year, merchants, spend, top10_share, hhi from stockport_marts.mart_stockport_vendor_concentration order by year"
    )
    recurring = q(
        "select merchant_key, months_billed, first_month, last_month, first_ticket, latest_ticket, price_change_pct "
        "from stockport_marts.mart_stockport_recurring_charges order by months_billed desc limit 12"
    )
    dups = q("select count(*), sum(amount) from stockport_marts.mart_stockport_duplicate_candidates")[0]
    top = q(
        "select merchant_category, sum(spend) s from stockport_marts.mart_stockport_spend_monthly "
        "group by 1 order by 2 desc limit 8"
    )
    con.close()
    out = {
        "summary": {
            "Source files downloaded (SHA-256 manifest)": f"{files[0]} ({files[1]} unique; {files[2] / 1e6:.1f} MB)",
            "Statement months covered": f"{files[3]} to {files[4]}",
            "Card lines in the warehouse": f"{lines[0]:,} (£{float(lines[1]):,.0f} after refunds; amounts are net of VAT to 2022-06 and gross after, as published)",
            "Lines with a booked GL account (incl. refunds; scoring excludes them)": f"{lines[3]:,}",
            "Placeholder labels excluded from scoring": f"{lines[4]:,}",
            "Refund lines": f"{lines[2]:,}",
            "Distinct raw merchant strings → normalised merchant keys": f"{lines[7]:,} → {lines[6]:,}",
            "Publisher layouts reconciled": ", ".join(
                f"{v[0]} ({str(v[1])[:7]} to {str(v[2])[:7]}, {v[3]:,} lines)" for v in versions
            ),
            "Settlement-before-purchase dates (dbt warning)": f"{lines[5]:,} lines",
            "Possible duplicate charges (same merchant + amount within 3 days)": f"{dups[0]:,} (£{float(dups[1] or 0):,.0f})",
        },
        "schema_versions": [{"version": v[0], "from": str(v[1]), "to": str(v[2]), "lines": v[3]} for v in versions],
        "vendor_concentration": [
            {"year": c[0], "merchants": c[1], "spend": float(c[2]), "top10_share": float(c[3]), "hhi": float(c[4])} for c in conc
        ],
        "recurring": [
            {
                "merchant": r[0],
                "months": r[1],
                "first": str(r[2]),
                "last": str(r[3]),
                "first_ticket": float(r[4]),
                "latest_ticket": float(r[5]),
                "price_change_pct": None if r[6] is None else float(r[6]),
            }
            for r in recurring
        ],
        "top_categories": [{"category": t[0], "spend": float(t[1])} for t in top],
    }
    (RESULTS / "real").mkdir(parents=True, exist_ok=True)
    (RESULTS / "real" / "warehouse.json").write_text(json.dumps(out, indent=2))
    return out
