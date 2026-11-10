"""Write generated data into the warehouse (raw.*) and keep gold labels outside it.

Gold labels are written to parquet under data/gold/, never into the warehouse, so no agent
code path that reads the warehouse can see them.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .generate import generate
from .paths import DB_PATH, GOLD_DIR


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def load(seed: int = 7, **scenario) -> dict[str, int]:
    tables = generate(seed, **scenario)
    con = connect()
    # Rebuild only the synthetic track's schemas; the real-data schemas are left alone.
    for schema in ("raw", "agent", "staging", "intermediate", "marts"):
        con.execute(f"drop schema if exists {schema} cascade")
    con.execute("create schema raw; create schema agent;")
    counts = {}
    for name, df in tables.items():
        if name.startswith("gold_"):
            df.to_parquet(GOLD_DIR / f"{name[5:]}.parquet", index=False)
            counts[name] = len(df)
            continue
        con.register("df_view", df)
        con.execute(f"create or replace table raw.{name[4:]} as select * from df_view")
        con.unregister("df_view")
        counts[name] = len(df)
    # Empty agent tables so dbt sources resolve before the agents have run.
    con.execute("""
        create or replace table agent.merchant_resolutions (descriptor_clean varchar, merchant_id varchar,
            merchant_key varchar, action varchar, score double, runner_up_id varchar, guardrail_blocked boolean);
        create or replace table agent.gl_predictions (txn_id varchar, gl_code varchar, gl_confidence double,
            gl_route varchar);
        create or replace table agent.policy_decisions (txn_id varchar, decision varchar, confidence double,
            cited_sections varchar, rationale varchar, route varchar, extractor varchar);
    """)
    con.close()
    return counts


def read_gold() -> pd.DataFrame:
    return pd.read_parquet(GOLD_DIR / "transactions.parquet")


def write_agent_table(name: str, df: pd.DataFrame) -> None:
    con = connect()
    con.register("df_view", df)
    con.execute(f"create or replace table agent.{name} as select * from df_view")
    con.close()
