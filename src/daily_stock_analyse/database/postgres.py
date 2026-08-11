from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..models import MarketRegime, StockAnalysis
from .connection import postgres_connection
from .schema import ensure_schema


@dataclass
class OutcomeRecord:
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


class PostgresSignalRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._initialized = False

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with postgres_connection(self.database_url) as conn:
            ensure_schema(conn)
        self._initialized = True

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
        self.ensure_initialized()
        run_id = self._build_run_id(generated_at_utc, market_session, regime.label, data_source)
        expiry_at = generated_at_utc + timedelta(hours=max(1, expiry_hours))
        persisted = 0

        with postgres_connection(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO analysis_runs (run_id, generated_at, market_session, market_regime, data_source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (run_id, generated_at_utc, market_session, regime.label, data_source),
                )

                for analysis in analyses:
                    if analysis.signal not in {"LONG", "SHORT"}:
                        continue
                    catalyst = analysis.intelligence.structured_catalysts[0] if analysis.intelligence.structured_catalysts else None
                    cur.execute(
                        """
                        INSERT INTO signals (
                            run_id,
                            symbol,
                            direction,
                            status,
                            confidence,
                            entry_price,
                            entry_trigger_price,
                            target_1,
                            target_2,
                            invalidation_price,
                            stop_loss,
                            catalyst,
                            catalyst_status,
                            catalyst_category,
                            catalyst_direction,
                            ai_provider,
                            triggered,
                            triggered_at,
                            return_pct,
                            mfe_pct,
                            mae_pct,
                            holding_minutes,
                            expiry_at,
                            market_regime_label,
                            created_at,
                            updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (run_id, symbol, direction) DO NOTHING
                        """,
                        (
                            run_id,
                            analysis.symbol,
                            analysis.signal,
                            "OPEN",
                            analysis.confidence,
                            None,
                            analysis.battle_plan.entry_trigger_price,
                            analysis.battle_plan.target_1,
                            analysis.battle_plan.target_2,
                            analysis.battle_plan.invalidation_price,
                            analysis.battle_plan.invalidation_price,
                            catalyst.headline if catalyst else None,
                            analysis.intelligence.catalyst_status,
                            catalyst.category if catalyst else "NONE",
                            catalyst.catalyst_direction if catalyst else "UNKNOWN",
                            ai_provider or "UNAVAILABLE",
                            False,
                            None,
                            None,
                            None,
                            None,
                            None,
                            expiry_at,
                            regime.label,
                            generated_at_utc,
                            generated_at_utc,
                        ),
                    )
                    persisted += max(0, cur.rowcount)
            conn.commit()
        return persisted

    def open_signals(self) -> list[dict]:
        self.ensure_initialized()
        with postgres_connection(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        signal_id AS id,
                        symbol,
                        direction AS signal,
                        entry_trigger_price,
                        target_1,
                        target_2,
                        stop_loss,
                        invalidation_price,
                        triggered,
                        triggered_at,
                        expiry_at,
                        mfe_pct,
                        mae_pct
                    FROM signals
                    WHERE status = 'OPEN'
                    ORDER BY created_at ASC
                    """
                )
                return list(cur.fetchall())

    def apply_outcome_updates(self, updates: list[OutcomeRecord], as_of_utc: datetime) -> int:
        self.ensure_initialized()
        if not updates:
            return 0

        touched = 0
        with postgres_connection(self.database_url) as conn:
            with conn.cursor() as cur:
                for item in updates:
                    cur.execute(
                        """
                        UPDATE signals
                        SET status = %s,
                            triggered = %s,
                            triggered_at = %s,
                            return_pct = %s,
                            mfe_pct = %s,
                            mae_pct = %s,
                            holding_minutes = %s,
                            updated_at = %s
                        WHERE signal_id = %s
                        """,
                        (
                            item.status,
                            item.triggered,
                            item.triggered_at,
                            item.return_pct,
                            item.mfe_pct,
                            item.mae_pct,
                            item.holding_minutes,
                            as_of_utc,
                            item.signal_id,
                        ),
                    )
                    touched += max(0, cur.rowcount)

                    cur.execute(
                        """
                        INSERT INTO signal_outcomes (
                            signal_id,
                            evaluation_time,
                            evaluation_price,
                            target_1_hit,
                            target_2_hit,
                            invalidated,
                            pnl_percent,
                            outcome
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (signal_id, evaluation_time)
                        DO UPDATE SET
                            evaluation_price = EXCLUDED.evaluation_price,
                            target_1_hit = EXCLUDED.target_1_hit,
                            target_2_hit = EXCLUDED.target_2_hit,
                            invalidated = EXCLUDED.invalidated,
                            pnl_percent = EXCLUDED.pnl_percent,
                            outcome = EXCLUDED.outcome
                        """,
                        (
                            item.signal_id,
                            as_of_utc,
                            item.exit_price,
                            item.status in {"TARGET_1", "TARGET_2"},
                            item.status == "TARGET_2",
                            item.status == "INVALIDATED",
                            item.return_pct,
                            item.status,
                        ),
                    )
            conn.commit()
        return touched

    def load_backtest_rows(self, limit: int = 5000) -> list[dict]:
        self.ensure_initialized()
        with postgres_connection(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        s.signal_id,
                        s.symbol,
                        s.direction AS signal,
                        s.status,
                        s.return_pct,
                        s.market_regime_label,
                        s.catalyst_category,
                        s.created_at
                    FROM signals s
                    WHERE s.status IN ('TARGET_1', 'TARGET_2', 'STOP', 'INVALIDATED', 'NO_TRIGGER', 'EXPIRED')
                    ORDER BY s.created_at DESC
                    LIMIT %s
                    """,
                    (max(1, limit),),
                )
                return list(cur.fetchall())

    def count_all(self) -> int:
        self.ensure_initialized()
        with postgres_connection(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM signals")
                row = cur.fetchone()
                return int(row["count"] if row else 0)

    @staticmethod
    def _build_run_id(generated_at_utc: datetime, market_session: str, market_regime: str, data_source: str) -> str:
        key = f"{generated_at_utc.isoformat()}|{market_session}|{market_regime}|{data_source}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"run_{digest[:24]}"
