"""Probe Vnstock Free fundamental coverage without printing financial values."""

import contextlib
import io
import os
import time

from dotenv import load_dotenv


load_dotenv()


def describe(frame) -> str:
    columns = {str(c).strip().lower() for c in frame.columns}
    item_ids = ({str(v).strip().lower() for v in frame["item_id"].dropna()}
                if "item_id" in frame.columns else set())
    targets = {
        "pe": {"p/e", "pe_ratio", "pe"},
        "pb": {"p/b", "pb_ratio", "pb"},
        "ev_ebitda": {"ev/ebitda", "ev_to_ebitda"},
        "roe": {"roe (%)", "roe"},
        "roic": {"roic"},
        "de": {"debt/equity", "debt to equity", "debt_to_equity"},
        "ebit_margin": {"ebit margin (%)", "ebit_margin"},
    }
    available = [name for name, aliases in targets.items() if (columns | item_ids) & aliases]
    periods = len([c for c in frame.columns if c not in {"item", "item_en", "item_id"}])
    return f"rows={len(frame)} periods={periods} metrics={','.join(available) or 'none'}"


def main() -> int:
    from vnstock.api.financial import Finance

    failures = 0
    for symbol in ("FPT", "TCB", "SSI", "BVH"):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                finance = Finance(source="VCI", symbol=symbol, period="year", show_log=False)
                ratio = finance.ratio(period="year", lang="en", dropna=True, show_log=False)
                income = finance.income_statement(period="year", lang="en", dropna=True, show_log=False)
                balance = finance.balance_sheet(period="year", lang="en", dropna=True, show_log=False)
                cashflow = finance.cash_flow(period="year", lang="en", dropna=True, show_log=False)
            print(f"{symbol}: ratio[{describe(ratio)}] income[{len(income)}x{len(income.columns)}] "
                  f"balance[{len(balance)}x{len(balance.columns)}] cashflow[{len(cashflow)}x{len(cashflow.columns)}]")
        except Exception as exc:
            print(f"{symbol}: FAILED ({type(exc).__name__})")
            failures += 1
        time.sleep(1)
    print("RESULT=" + ("PASS" if failures == 0 else f"PARTIAL failures={failures}"))
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
