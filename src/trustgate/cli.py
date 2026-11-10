"""`trustgate` command line.

trustgate real            real data: download -> load -> dbt -> GL benchmark -> autonomy rollout
trustgate synthetic       policy-agent benchmark, default world (seed 7), report + charts
trustgate experiments     10 seeds + sensitivity sweeps + perception backends (~50 min)
trustgate site            build site/ (results dashboard + dbt docs)
trustgate readme          render README.md from results (CI checks it is up to date)
trustgate reproduce       everything above, in order
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from typing import Any

from . import pipeline


def _step(name: str, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
    t = time.time()
    out = fn(*a, **kw)
    print(f"  {name:<44} {time.time() - t:6.1f}s")
    return out


def cmd_real(args: argparse.Namespace) -> None:
    from .realgl import run_all
    from .realwh import summarise
    from .sources.stockport import download, load_to_warehouse

    print("real data (Stockport MBC purchase cards)")
    _step("download + verify manifest", download)
    _step("load raw files into the warehouse", load_to_warehouse)
    _step("dbt build (staging, marts, tests, seeds)", pipeline.dbt, "tag:stockport")
    _step("warehouse summary", summarise)
    if not args.skip_benchmark:
        _step("GL benchmark + trustgate rollout", run_all)


def cmd_synthetic(args: argparse.Namespace) -> None:
    from .evaluate import evaluate
    from .load import load
    from .report import write_report

    print("synthetic policy-agent benchmark")
    _step("generate + load raw data", load, args.seed)
    _step("dbt: staging", pipeline.dbt, "tag:pre_agent")
    res = _step("resolve merchants", pipeline.run_resolver)
    _step("dbt: agent-ready features", pipeline.dbt, "tag:post_resolution")
    df = _step(f"agents ({args.extractor} extractor)", pipeline.run_agents, args.extractor)
    df, m = _step("evaluate + earn autonomy", evaluate, df, res)
    _step("write decisions back", pipeline.write_decisions, df)
    _step("dbt: governed marts + tests", pipeline.dbt, "tag:post_agent")
    _step("render report", write_report)
    e = m["autonomy"]["earned_autonomy"]
    print(
        f"\nlive: {e['automation_rate']:.1%} auto-actioned, {e['auto_error_rate']:.2%} wrong "
        f"(95% CI up to {e['auto_error_ci'][1]:.2%})"
    )


def cmd_experiments(args: argparse.Namespace) -> None:
    from .experiments import compare_extractors, run

    _step("seeds + sensitivity sweeps", run, args.seeds)
    _step("perception backends", compare_extractors)


def cmd_site(args: argparse.Namespace) -> None:
    from .site import build

    print(_step("build site", build, not args.no_dbt_docs))


def cmd_readme(args: argparse.Namespace) -> None:
    from .readme import render

    _step("render README.md", render, True)


def cmd_reproduce(args: argparse.Namespace) -> None:
    args.skip_benchmark = False
    args.seed, args.extractor, args.no_dbt_docs = 7, "rules", False
    cmd_real(args)
    cmd_experiments(args)
    cmd_synthetic(args)  # last, so the warehouse holds the default synthetic world
    cmd_site(args)
    cmd_readme(args)


def cmd_queue(args: argparse.Namespace) -> None:
    from .load import connect

    con = connect(read_only=True)
    print(
        con.sql(
            f"select posted_date, merchant_name, amount, decision, rationale from marts.mart_review_queue limit {int(args.n)}"
        )
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="earned_autonomy", description="Earned autonomy for finance agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real", help="real-data track")
    r.add_argument("--skip-benchmark", action="store_true", help="warehouse only (fast)")
    r.set_defaults(fn=cmd_real)
    s = sub.add_parser("synthetic", aliases=["all"], help="synthetic policy-agent benchmark")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--extractor", choices=["rules", "embed", "rules+embed", "llm"], default="rules")
    s.set_defaults(fn=cmd_synthetic)
    e = sub.add_parser("experiments", help="seeds, sensitivity, perception backends")
    e.add_argument("--seeds", type=int, default=10)
    e.set_defaults(fn=cmd_experiments)
    st = sub.add_parser("site", help="build the GitHub Pages site")
    st.add_argument("--no-dbt-docs", action="store_true")
    st.set_defaults(fn=cmd_site)
    sub.add_parser("readme", help="render README.md from results").set_defaults(fn=cmd_readme)
    rp = sub.add_parser("reproduce", help="run everything")
    rp.add_argument("--seeds", type=int, default=10)
    rp.set_defaults(fn=cmd_reproduce)
    q = sub.add_parser("queue", help="top of the human review queue")
    q.add_argument("-n", type=int, default=10)
    q.set_defaults(fn=cmd_queue)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
