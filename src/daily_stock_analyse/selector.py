from __future__ import annotations

from .models import StockAnalysis


def select_dynamic_opportunities(
    scored: list[StockAnalysis],
    fixed_watchlist: list[str],
    top_n: int = 3,
) -> list[StockAnalysis]:
    fixed = {x.upper() for x in fixed_watchlist}
    eligible = [x for x in scored if x.symbol.upper() not in fixed]

    # Prioritize directional conviction while allowing both long and short setups.
    ranked = sorted(
        eligible,
        key=lambda x: max(abs(x.score.long_score), abs(x.score.short_score)),
        reverse=True,
    )

    return ranked[:top_n]
