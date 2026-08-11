from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .database import PostgresSignalRepository
from .models import MarketRegime, StockAnalysis


@dataclass
class OutcomeUpdate:
    signal_id: str | int | UUID
    status: str
    triggered: bool
    triggered_at: str | None
    exit_price: float | None
    return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    holding_minutes: int | None
    outcome_note: str


class SignalHistoryStore:
    def __init__(self, database_url: str):
        self._repo = PostgresSignalRepository(database_url)

    def save_signals(
        self,
        analyses: list[StockAnalysis],
        regime: MarketRegime,
        generated_at_utc: datetime,
        expiry_hours: int,
        market_session: str,
        data_source: str,
        ai_provider: str | None,
    ) -> int:
        return self._repo.save_signals(
            analyses,
            regime,
            generated_at_utc,
            expiry_hours,
            market_session,
            data_source,
            ai_provider,
        )

    def open_signals(self) -> list[dict]:
        return self._repo.open_signals()

    def apply_outcome_updates(self, updates: list[OutcomeUpdate], as_of_utc: datetime) -> int:
        if not updates:
            return 0
        return self._repo.apply_outcome_updates(updates, as_of_utc)

    def load_backtest_rows(self, limit: int = 5000) -> list[dict]:
        return self._repo.load_backtest_rows(limit)

    def count_all(self) -> int:
        return self._repo.count_all()
