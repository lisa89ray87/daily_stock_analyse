from __future__ import annotations

from typing import Any


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                generated_at TIMESTAMPTZ NOT NULL,
                market_session TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                data_source TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        _migrate_legacy_signals_schema(cur)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                signal_id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT,
                entry_price DOUBLE PRECISION,
                entry_trigger_price DOUBLE PRECISION,
                target_1 DOUBLE PRECISION,
                target_2 DOUBLE PRECISION,
                invalidation_price DOUBLE PRECISION,
                stop_loss DOUBLE PRECISION,
                catalyst TEXT,
                catalyst_status TEXT,
                catalyst_category TEXT,
                catalyst_direction TEXT,
                ai_provider TEXT,
                triggered BOOLEAN NOT NULL DEFAULT FALSE,
                triggered_at TIMESTAMPTZ,
                return_pct DOUBLE PRECISION,
                mfe_pct DOUBLE PRECISION,
                mae_pct DOUBLE PRECISION,
                holding_minutes INTEGER,
                expiry_at TIMESTAMPTZ,
                market_regime_label TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(run_id, symbol, direction)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                outcome_id BIGSERIAL PRIMARY KEY,
                signal_id BIGINT NOT NULL REFERENCES signals(signal_id),
                evaluation_time TIMESTAMPTZ NOT NULL,
                evaluation_price DOUBLE PRECISION,
                target_1_hit BOOLEAN NOT NULL DEFAULT FALSE,
                target_2_hit BOOLEAN NOT NULL DEFAULT FALSE,
                invalidated BOOLEAN NOT NULL DEFAULT FALSE,
                pnl_percent DOUBLE PRECISION,
                outcome TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(signal_id, evaluation_time)
            )
            """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_direction ON signals(direction)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_run_id ON signals(run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_id ON signal_outcomes(signal_id)")

        cur.execute(
            """
            INSERT INTO schema_migrations(version)
            VALUES (1)
            ON CONFLICT (version) DO NOTHING
            """
        )

    conn.commit()


def _migrate_legacy_signals_schema(cur: Any) -> None:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'signals'
        """
    )
    columns = {row["column_name"] for row in cur.fetchall()}
    if not columns:
        return
    if "signal_id" not in columns and "id" in columns:
        cur.execute("ALTER TABLE signals RENAME COLUMN id TO signal_id")
