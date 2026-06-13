"""Forward paper-trading — the only honest proof of trading usefulness.

A backtest convinces no one (it's in-sample by construction, however careful).
A LIVE, timestamped, marked-to-market track record does. This module snapshots
the multi-factor book's target weights each month and marks them forward as
real returns arrive, accumulating an equity curve nobody can retrofit.

Hard rule: FORWARD ONLY. The book starts the first day it runs and grows from
there — no backfilling with history (that would be a backtest wearing a live
record's clothes). The backtest's Sharpe (~0.57, carry+value) is the prior;
this is the out-of-sample evidence accruing against it. The daily Action calls
`opencopper paper --update`, which marks any resolved month and snapshots the
current positions, then commits data/paper-book.json. Decision support, never
advice; these are paper positions, not orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PAPER_PATH = Path("data/paper-book.json")


def _today_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01")


def load_book(path: Path = PAPER_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"live_start": None, "snapshots": []}


def update_paper(path: Path = PAPER_PATH, today_month: str | None = None,
                 factors=("carry", "value")) -> dict:
    """Mark resolved snapshots, then snapshot this month's live positions.
    Idempotent within a month."""
    from .backtest import current_weights, realized_month_return

    book = load_book(path)
    month = today_month or _today_month()

    # mark any snapshot whose return has now resolved (next month's data exists)
    for snap in book["snapshots"]:
        if snap.get("realized_ret") is None:
            r = realized_month_return(snap["weights"], snap["month"])
            if r is not None:
                snap["realized_ret"] = round(r, 6)

    # snapshot this month's target positions once
    have = {s["month"] for s in book["snapshots"]}
    if month not in have:
        w = current_weights(factors)
        if w:
            book["snapshots"].append({"month": month, "weights": w, "realized_ret": None})
            book["live_start"] = book["live_start"] or month
    book["snapshots"].sort(key=lambda s: s["month"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, indent=1))
    return book


def paper_summary(path: Path = PAPER_PATH) -> dict:
    import math

    book = load_book(path)
    snaps = book["snapshots"]
    resolved = [s for s in snaps if s.get("realized_ret") is not None]
    rets = [s["realized_ret"] for s in resolved]
    equity, curve = 1.0, []
    for s in resolved:
        equity *= (1 + s["realized_ret"])
        curve.append({"month": s["month"], "equity": round(equity, 4)})
    perf = None
    if len(rets) >= 2:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        perf = {
            "ann_ret": round(12 * mu, 4),
            "ann_vol": round(sd * math.sqrt(12), 4),
            "sharpe": round(12 * mu / (sd * math.sqrt(12)), 2) if sd else 0.0,
            "cum_ret": round(equity - 1, 4),
        }
    current = snaps[-1]["weights"] if snaps else {}
    return {
        "live_start": book.get("live_start"),
        "n_snapshots": len(snaps),
        "n_resolved": len(resolved),
        "equity_curve": curve,
        "perf": perf,
        "current_positions": current,
    }


def render_paper(s: dict) -> str:
    lines = ["PAPER BOOK — forward, live, marked to market (carry + value, long-only)"]
    if not s["n_snapshots"]:
        return "\n".join(lines + ["", "_not started — `opencopper paper --update` snapshots the first positions_"])
    lines.append(f"  live since {s['live_start']} · {s['n_snapshots']} snapshots · "
                 f"{s['n_resolved']} months marked")
    if s["perf"]:
        p = s["perf"]
        lines.append(f"  realized: {p['cum_ret']:+.1%} cumulative · {p['ann_ret']:+.1%}/yr · "
                     f"vol {p['ann_vol']:.1%} · Sharpe {p['sharpe']:.2f}")
    else:
        lines.append("  realized: accruing — the first month resolves once next month's prices print")
    pos = sorted(s["current_positions"].items(), key=lambda kv: -kv[1])
    lines.append("  current positions (equal-weight long): "
                 + (", ".join(c for c, _ in pos) if pos else "flat"))
    lines += ["",
              "Forward only — no backfill. The backtest (Sharpe ~0.57) is the prior;",
              "this is the out-of-sample record accruing against it. Paper positions,",
              "not orders. Decision support, never advice."]
    return "\n".join(lines)
