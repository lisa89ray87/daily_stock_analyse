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

        _ensure_signals_schema(cur)
        signal_id_type = _get_signal_id_type(cur) or "bigint"
        _ensure_signal_outcomes_schema(cur, signal_id_type)
        _ensure_indexes(cur)

        cur.execute(
            """
            INSERT INTO schema_migrations(version)
            VALUES (1)
            ON CONFLICT (version) DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO schema_migrations(version)
            VALUES (2)
            ON CONFLICT (version) DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO schema_migrations(version)
            VALUES (3)
            ON CONFLICT (version) DO NOTHING
            """
        )

    conn.commit()


def _ensure_signals_schema(cur: Any) -> None:
    if not _table_exists(cur, "signals"):
        cur.execute(
            """
            CREATE TABLE signals (
                signal_id BIGSERIAL PRIMARY KEY,
                run_id TEXT REFERENCES analysis_runs(run_id),
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
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        return

    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'signals'
        """
    )
    columns = {row["column_name"] for row in cur.fetchall()}
    if "signal_id" not in columns and "id" in columns:
        cur.execute("ALTER TABLE signals RENAME COLUMN id TO signal_id")
        columns.remove("id")
        columns.add("signal_id")

    _ensure_text_compatible_column(cur, "signals", "confidence", legacy_column_name="legacy_confidence_numeric")

    for ddl in [
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS run_id TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS direction TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS status TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_price DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_trigger_price DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_1 DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_2 DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS invalidation_price DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS catalyst TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS catalyst_status TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS catalyst_category TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS catalyst_direction TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS ai_provider TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS triggered BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS triggered_at TIMESTAMPTZ",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS return_pct DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS mfe_pct DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS mae_pct DOUBLE PRECISION",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS holding_minutes INTEGER",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS expiry_at TIMESTAMPTZ",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_regime_label TEXT",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ]:
        cur.execute(ddl)

    refreshed_columns = _column_map(cur, "signals")
    if "signal" in refreshed_columns:
        cur.execute(
            """
            UPDATE signals
            SET direction = COALESCE(direction, signal::text)
            WHERE direction IS NULL AND signal IS NOT NULL
            """
        )

    if not _constraint_exists(cur, "fk_signals_run_id"):
        cur.execute(
            """
            ALTER TABLE signals
            ADD CONSTRAINT fk_signals_run_id
            FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
            NOT VALID
            """
        )


def _ensure_signal_outcomes_schema(cur: Any, signal_id_type: str) -> None:
    signal_id_sql_type = _signal_id_sql_type(signal_id_type)
    if not _table_exists(cur, "signal_outcomes"):
        cur.execute(
            f"""
            CREATE TABLE signal_outcomes (
                outcome_id BIGSERIAL PRIMARY KEY,
                signal_id {signal_id_sql_type} NOT NULL REFERENCES signals(signal_id),
                evaluation_time TIMESTAMPTZ NOT NULL,
                evaluation_price DOUBLE PRECISION,
                target_1_hit BOOLEAN NOT NULL DEFAULT FALSE,
                target_2_hit BOOLEAN NOT NULL DEFAULT FALSE,
                invalidated BOOLEAN NOT NULL DEFAULT FALSE,
                pnl_percent DOUBLE PRECISION,
                outcome TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        return

    columns = _column_map(cur, "signal_outcomes")
    current_signal_id_type = _normalize_column_type(columns.get("signal_id")) if "signal_id" in columns else None

    if current_signal_id_type is not None and current_signal_id_type != signal_id_type and "legacy_signal_id" not in columns:
        cur.execute("ALTER TABLE signal_outcomes RENAME COLUMN signal_id TO legacy_signal_id")
        columns = _column_map(cur, "signal_outcomes")
        current_signal_id_type = None

    if current_signal_id_type != signal_id_type:
        cur.execute(f"ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS signal_id {signal_id_sql_type}")
        columns = _column_map(cur, "signal_outcomes")

    for ddl in [
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS evaluation_time TIMESTAMPTZ",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS evaluation_price DOUBLE PRECISION",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS target_1_hit BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS target_2_hit BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS invalidated BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS pnl_percent DOUBLE PRECISION",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS outcome TEXT",
        "ALTER TABLE signal_outcomes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ]:
        cur.execute(ddl)

    if "legacy_signal_id" in columns:
        cur.execute(
            """
            UPDATE signal_outcomes so
            SET signal_id = s.signal_id
            FROM signals s
            WHERE so.signal_id IS NULL
              AND so.legacy_signal_id IS NOT NULL
              AND s.signal_id::text = so.legacy_signal_id::text
            """
        )

    if not _constraint_exists(cur, "fk_signal_outcomes_signal_id"):
        cur.execute(
            """
            ALTER TABLE signal_outcomes
            ADD CONSTRAINT fk_signal_outcomes_signal_id
            FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
            NOT VALID
            """
        )


def _ensure_indexes(cur: Any) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_direction ON signals(direction)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_run_id ON signals(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_id ON signal_outcomes(signal_id)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_run_symbol_direction ON signals(run_id, symbol, direction)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_signal_outcomes_signal_eval_time ON signal_outcomes(signal_id, evaluation_time)")


def _get_signal_id_type(cur: Any) -> str | None:
    columns = _column_map(cur, "signals")
    return _normalize_column_type(columns.get("signal_id")) if "signal_id" in columns else None


def _table_exists(cur: Any, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table_name}",))
    row = cur.fetchone() or {}
    return bool(row.get("name"))


def _column_map(cur: Any, table_name: str) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {row["column_name"]: row for row in cur.fetchall()}


def _normalize_column_type(column: dict[str, Any] | None) -> str | None:
    if not column:
        return None
    udt_name = str(column.get("udt_name") or "").lower()
    data_type = str(column.get("data_type") or "").lower()
    if udt_name == "uuid" or data_type == "uuid":
        return "uuid"
    if udt_name in {"int8", "bigint"} or data_type == "bigint":
        return "bigint"
    if udt_name in {"int4", "integer"} or data_type == "integer":
        return "integer"
    if udt_name in {"text", "varchar", "bpchar"}:
        return "text"
    return udt_name or data_type or None


def _ensure_text_compatible_column(cur: Any, table_name: str, column_name: str, *, legacy_column_name: str) -> None:
    columns = _column_map(cur, table_name)
    current_type = _normalize_column_type(columns.get(column_name)) if column_name in columns else None
    if current_type in {None, "text"}:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} TEXT")
        return

    if legacy_column_name not in columns:
        cur.execute(f"ALTER TABLE {table_name} RENAME COLUMN {column_name} TO {legacy_column_name}")

    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} TEXT")
    cur.execute(
        f"""
        UPDATE {table_name}
        SET {column_name} = COALESCE({column_name}, {legacy_column_name}::text)
        WHERE {column_name} IS NULL AND {legacy_column_name} IS NOT NULL
        """
    )


def _signal_id_sql_type(signal_id_type: str) -> str:
    normalized = signal_id_type.lower()
    if normalized == "uuid":
        return "UUID"
    if normalized == "integer":
        return "INTEGER"
    return "BIGINT"


def _constraint_exists(cur: Any, constraint_name: str) -> bool:
    cur.execute(
        """
        SELECT 1 AS present
        FROM pg_constraint
        WHERE conname = %s
        LIMIT 1
        """,
        (constraint_name,),
    )
    return bool(cur.fetchone())
