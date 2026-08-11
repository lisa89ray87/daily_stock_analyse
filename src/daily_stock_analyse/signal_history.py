from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import MarketRegime, StockAnalysis


@dataclass
class OutcomeUpdate:
    signal_id: int
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
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at_utc TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    confidence TEXT,
                    setup_score INTEGER,
                    candidate_score INTEGER,
                    trading_horizon TEXT,
                    market_regime_label TEXT,
                    market_regime_bias TEXT,
                    session_state TEXT,
                    selected_data_source TEXT,
                    data_session TEXT,
                    data_source TEXT,
                    quote_timestamp TEXT,
                    is_extended_hours INTEGER DEFAULT 0,
                    entry_price REAL,
                    entry_trigger_price REAL,
                    target_1 REAL,
                    target_2 REAL,
                    stop_loss REAL,
                    invalidation_price REAL,
                    triggered INTEGER DEFAULT 0,
                    triggered_at TEXT,
                    status TEXT DEFAULT 'OPEN',
                    catalyst_status TEXT,
                    catalyst_category TEXT,
                    catalyst_direction TEXT,
                    outcome_note TEXT,
                    outcome_updated_at TEXT,
                    exit_price REAL,
                    return_pct REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    holding_minutes INTEGER,
                    expiry_at TEXT,
                    UNIQUE(generated_at_utc, symbol, signal)
                )
                """
            )

    def save_signals(
        self,
        analyses: list[StockAnalysis],
        regime: MarketRegime,
        generated_at_utc: datetime,
        expiry_hours: int,
    ) -> int:
        expiry_at = (generated_at_utc + timedelta(hours=max(1, expiry_hours))).isoformat()
        persisted = 0
        with self._connect() as conn:
            for analysis in analyses:
                if analysis.signal not in {"LONG", "SHORT"}:
                    continue
                catalyst = analysis.intelligence.structured_catalysts[0] if analysis.intelligence.structured_catalysts else None
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_history (
                        generated_at_utc,
                        symbol,
                        signal,
                        confidence,
                        setup_score,
                        candidate_score,
                        trading_horizon,
                        market_regime_label,
                        market_regime_bias,
                        session_state,
                        selected_data_source,
                        data_session,
                        data_source,
                        quote_timestamp,
                        is_extended_hours,
                        entry_price,
                        entry_trigger_price,
                        target_1,
                        target_2,
                        stop_loss,
                        invalidation_price,
                        catalyst_status,
                        catalyst_category,
                        catalyst_direction,
                        outcome_note,
                        expiry_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generated_at_utc.isoformat(),
                        analysis.symbol,
                        analysis.signal,
                        analysis.confidence,
                        analysis.setup_score,
                        analysis.candidate_score,
                        analysis.trading_horizon,
                        regime.label,
                        regime.bias,
                        analysis.market_data.session_state,
                        analysis.market_data.selected_data_source,
                        analysis.market_data.data_session,
                        analysis.market_data.data_source,
                        analysis.market_data.quote_timestamp,
                        1 if analysis.market_data.is_extended_hours else 0,
                        None,
                        analysis.battle_plan.entry_trigger_price,
                        analysis.battle_plan.target_1,
                        analysis.battle_plan.target_2,
                        analysis.battle_plan.invalidation_price,
                        analysis.battle_plan.invalidation_price,
                        analysis.intelligence.catalyst_status,
                        catalyst.category if catalyst else "NONE",
                        catalyst.catalyst_direction if catalyst else "UNKNOWN",
                        "Signal recorded",
                        expiry_at,
                    ),
                )
                persisted += max(0, cursor.rowcount)
        return persisted

    def open_signals(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM signal_history
                WHERE status = 'OPEN'
                ORDER BY generated_at_utc ASC
                """
            ).fetchall()
        return rows

    def apply_outcome_updates(self, updates: list[OutcomeUpdate], as_of_utc: datetime) -> int:
        if not updates:
            return 0
        with self._connect() as conn:
            touched = 0
            for item in updates:
                cursor = conn.execute(
                    """
                    UPDATE signal_history
                    SET status = ?,
                        triggered = ?,
                        triggered_at = ?,
                        outcome_updated_at = ?,
                        exit_price = ?,
                        return_pct = ?,
                        mfe_pct = ?,
                        mae_pct = ?,
                        holding_minutes = ?,
                        outcome_note = ?
                    WHERE id = ?
                    """,
                    (
                        item.status,
                        1 if item.triggered else 0,
                        item.triggered_at,
                        as_of_utc.isoformat(),
                        item.exit_price,
                        item.return_pct,
                        item.mfe_pct,
                        item.mae_pct,
                        item.holding_minutes,
                        item.outcome_note,
                        item.signal_id,
                    ),
                )
                touched += max(0, cursor.rowcount)
            return touched

    def load_backtest_rows(self, limit: int = 5000) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM signal_history
                WHERE status IN ('TARGET_1', 'TARGET_2', 'STOP', 'INVALIDATED', 'NO_TRIGGER', 'EXPIRED')
                ORDER BY generated_at_utc DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return rows

    def count_all(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM signal_history").fetchone()
        return int(row["count"] if row else 0)


def resolve_signal_db_path(base_path: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return base_path / path
