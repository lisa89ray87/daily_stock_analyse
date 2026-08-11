from __future__ import annotations

from collections import defaultdict


def summarize_backtest(rows: list[dict]) -> dict:
    total = len(rows)
    if total == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "by_symbol": {},
            "by_direction": {},
            "by_regime": {},
            "by_catalyst_category": {},
        }

    wins = 0
    losses = 0
    return_values: list[float] = []

    by_symbol = defaultdict(list)
    by_direction = defaultdict(list)
    by_regime = defaultdict(list)
    by_catalyst_category = defaultdict(list)

    for row in rows:
        status = str(row["status"])
        ret = row["return_pct"]
        if isinstance(ret, (int, float)):
            return_values.append(float(ret))

        if status in {"TARGET_1", "TARGET_2"}:
            wins += 1
        if status in {"STOP", "INVALIDATED"}:
            losses += 1

        by_symbol[str(row["symbol"])].append(row)
        by_direction[str(row["signal"])].append(row)
        by_regime[str(row["market_regime_label"] or "UNKNOWN")].append(row)
        by_catalyst_category[str(row["catalyst_category"] or "NONE")].append(row)

    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total) * 100.0, 2),
        "avg_return_pct": round(sum(return_values) / len(return_values), 4) if return_values else 0.0,
        "by_symbol": {k: _bucket(v) for k, v in by_symbol.items()},
        "by_direction": {k: _bucket(v) for k, v in by_direction.items()},
        "by_regime": {k: _bucket(v) for k, v in by_regime.items()},
        "by_catalyst_category": {k: _bucket(v) for k, v in by_catalyst_category.items()},
    }


def _bucket(rows: list[dict]) -> dict:
    trades = len(rows)
    wins = sum(1 for row in rows if str(row["status"]) in {"TARGET_1", "TARGET_2"})
    losses = sum(1 for row in rows if str(row["status"]) in {"STOP", "INVALIDATED"})
    returns = [float(row["return_pct"]) for row in rows if isinstance(row["return_pct"], (int, float))]
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / trades) * 100.0, 2) if trades else 0.0,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0.0,
    }
