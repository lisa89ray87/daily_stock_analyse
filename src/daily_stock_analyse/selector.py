from __future__ import annotations

from .models import StockAnalysis


def select_dynamic_opportunities(
    scored: list[StockAnalysis],
    fixed_watchlist: list[str],
    top_n: int = 3,
    min_setup_score: int = 0,
    min_relative_volume: float = 0.0,
) -> list[StockAnalysis]:
    fixed = {x.upper() for x in fixed_watchlist}
    eligible = [
        x
        for x in scored
        if x.symbol.upper() not in fixed
        and x.setup_score >= min_setup_score
        and (
            x.market_data.relative_volume is None
            or x.market_data.relative_volume >= min_relative_volume
        )
    ]

    if len(eligible) < top_n:
        eligible = [x for x in scored if x.symbol.upper() not in fixed]

    # Prioritize directional conviction while allowing both long and short setups.
    ranked = sorted(
        eligible,
        key=lambda x: (
            x.setup_score,
            max(abs(x.score.long_score), abs(x.score.short_score)),
            x.market_data.relative_volume or 0.0,
        ),
        reverse=True,
    )

    return ranked[:top_n]
