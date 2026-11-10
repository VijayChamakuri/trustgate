from pathlib import Path

import openpyxl

from trustgate.sources.stockport import parse_file, period_from_url

FIX = Path(__file__).parent / "fixtures"


def test_period_from_both_url_styles():
    assert period_from_url("https://x/Purchasing+Card+-+February+17.csv") == "2017-02"
    assert period_from_url("https://x/Purchase+Card+-+August+2026.csv") == "2026-08"
    assert period_from_url("https://x/Purchasing+Card+-+March+17+.csv") == "2017-03"


def test_v1_layout_keeps_labels_and_drops_blank_rows():
    rows = parse_file(FIX / "stockport_v1.csv", "2019-05")
    assert len(rows) == 2
    assert rows[0]["schema_version"] == "v1_net_10col"
    assert rows[0]["purpose"] == "Furniture and Equipment"
    assert rows[1]["amount"] == "1007.45" and rows[1]["amount_basis"] == "net"


def test_v3_layout_skips_banner_and_has_no_label():
    rows = parse_file(FIX / "stockport_v3.csv", "2024-06")
    assert [r["merchant_raw"] for r in rows] == ["INVISIONAPP, INC", "FASTHOSTS"]
    assert all(r["schema_version"] == "v3_gross_6col" and r["purpose"] is None for r in rows)
    assert rows[1]["amount"] == "1437.19" and rows[1]["amount_basis"] == "gross"


def test_xlsx_published_with_csv_extension(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["MAY 2024"])
    ws.append([])
    ws.append(
        [
            "Organisation Name",
            "Supplier (Beneficiary)",
            "Procurement (Merchant) Category",
            "Purchase Date",
            "Settlement Date",
            "Gross Amount (£)",
        ]
    )
    ws.append(["STOCKPORT MBC", "TRAINLINE", "PASSENGER RAILWAYS", "02/05/2024", "16/06/2024", 54.1])
    path = tmp_path / "may.csv"
    wb.save(path)
    rows = parse_file(path, "2024-05")
    assert len(rows) == 1 and rows[0]["merchant_raw"] == "TRAINLINE" and rows[0]["amount"] == "54.1"
