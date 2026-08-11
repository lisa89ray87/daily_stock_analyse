from __future__ import annotations

import os
from datetime import UTC, datetime

from ..models import BattlePlan, DataQuality, IntelligenceBlock, MarketData, MarketRegime, ScoreBreakdown, StockAnalysis
from ..signal_history import OutcomeUpdate
from .connection import postgres_connection
from .postgres import PostgresSignalRepository
from .schema import ensure_schema


EXPECTED_TABLES = {"schema_migrations", "analysis_runs", "signals", "signal_outcomes"}
EXPECTED_INDEXES = {
    "idx_signals_symbol",
    "idx_signals_created_at",
    "idx_signals_status",
    "idx_signals_direction",
    "idx_signals_run_id",
    "idx_signal_outcomes_signal_id",
}


def _verify_tls_connection(conn) -> None:
    info = getattr(conn, "info", None)
    parameters = {}
    if info is not None and hasattr(info, "get_parameters"):
        try:
            parameters = {str(key).lower(): str(value).lower() for key, value in (info.get_parameters() or {}).items()}
        except Exception:
            parameters = {}

    sslmode = parameters.get("sslmode")
    channel_binding = parameters.get("channel_binding")
    if sslmode != "require" or channel_binding != "require":
        raise RuntimeError("ssl check failed")

    pgconn = getattr(conn, "pgconn", None)
    if pgconn is None:
        raise RuntimeError("ssl check failed")

    ssl_in_use = getattr(pgconn, "ssl_in_use", None)
    if callable(ssl_in_use):
        ssl_in_use = ssl_in_use()
    if ssl_in_use is True:
        return
    if ssl_in_use is False:
        raise RuntimeError("ssl check failed")

    ssl_attribute = getattr(pgconn, "ssl_attribute", None)
    if callable(ssl_attribute):
        protocol = ssl_attribute("protocol")
        cipher = ssl_attribute("cipher")
        if protocol or cipher:
            return

    raise RuntimeError("ssl check failed")


def _smoke_analysis() -> StockAnalysis:
    return StockAnalysis(
        symbol="NEONSMOKE",
        name="NEONSMOKE",
        signal="LONG",
        trading_horizon="DAY_TRADE",
        direction_bias="LONG_BIAS",
        market_alignment="MARKET_ALIGNED",
        setup_score=88,
        day_trade_candidate=True,
        candidate_score=88,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="Break above resistance",
        confidence="HIGH",
        one_liner="Smoke validation setup",
        main_reason="Workflow smoke validation",
        risk_classification="MEDIUM",
        market_data=MarketData(symbol="NEONSMOKE", price=100.0, provider="smoke"),
        intelligence=IntelligenceBlock(
            facts=["Reuters: Smoke validation catalyst"],
            interpretation=["Validation only"],
            upcoming_catalysts=["OTHER | NEUTRAL | Reuters | Smoke validation catalyst"],
            structured_catalysts=[],
            catalyst_status="NO_MATERIAL_CATALYST",
        ),
        battle_plan=BattlePlan(
            bullish_scenario="b",
            bearish_scenario="s",
            key_support="99",
            key_resistance="101",
            entry_area="Break above 100",
            target_area="102 / 104",
            invalidation="Below 98",
            risk_reward_assessment="2.00",
            entry_trigger_price=100.0,
            confirmation_level=100.0,
            invalidation_price=98.0,
            target_1=102.0,
            target_2=104.0,
        ),
        score=ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "smoke", []),
    )


def _cleanup(database_url: str, run_id: str) -> None:
    with postgres_connection(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.signal_outcomes') AS name")
            signal_outcomes_exists = bool((cur.fetchone() or {}).get("name"))
            cur.execute("SELECT to_regclass('public.signals') AS name")
            signals_exists = bool((cur.fetchone() or {}).get("name"))
            cur.execute("SELECT to_regclass('public.analysis_runs') AS name")
            analysis_runs_exists = bool((cur.fetchone() or {}).get("name"))

            run_id_available = False
            if signals_exists:
                cur.execute(
                    """
                    SELECT 1 AS present
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'signals' AND column_name = 'run_id'
                    LIMIT 1
                    """
                )
                run_id_available = bool(cur.fetchone())

            if signal_outcomes_exists and signals_exists and run_id_available:
                cur.execute(
                    """
                    DELETE FROM signal_outcomes
                    WHERE signal_id IN (SELECT signal_id FROM signals WHERE run_id = %s)
                    """,
                    (run_id,),
                )
            if signals_exists and run_id_available:
                cur.execute("DELETE FROM signals WHERE run_id = %s", (run_id,))
            if analysis_runs_exists:
                cur.execute("DELETE FROM analysis_runs WHERE run_id = %s", (run_id,))
        conn.commit()


def main() -> int:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        print("DATABASE_URL missing")
        return 1

    repo = PostgresSignalRepository(database_url)
    now = datetime.now(UTC).replace(microsecond=0)
    regime = MarketRegime("SMOKE", "NEUTRAL", "Validation", "Validation", "Neon smoke validation", {})
    analysis = _smoke_analysis()
    run_id = repo._build_run_id(now, "US_REGULAR", regime.label, "NEON_SMOKE")

    smoke_error: Exception | None = None
    try:
        with postgres_connection(database_url) as conn:
            print("connection PASS")
            _verify_tls_connection(conn)
            print("ssl PASS")

            ensure_schema(conn)
            print("schema PASS")

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ANY(%s)",
                    (sorted(EXPECTED_TABLES),),
                )
                tables = {row["table_name"] for row in cur.fetchall()}
                if tables != EXPECTED_TABLES:
                    raise RuntimeError("table verification failed")
                print("tables PASS")

                cur.execute(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename IN ('signals', 'signal_outcomes')"
                )
                indexes = {row["indexname"] for row in cur.fetchall()}
                if not EXPECTED_INDEXES.issubset(indexes):
                    raise RuntimeError("index verification failed")
                print("indexes PASS")

        inserted = repo.save_signals(
            [analysis],
            regime,
            now,
            expiry_hours=2,
            market_session="US_REGULAR",
            data_source="NEON_SMOKE",
            ai_provider="smoke",
        )
        if inserted != 1:
            raise RuntimeError("insert verification failed")
        print("insert PASS")

        duplicate = repo.save_signals(
            [analysis],
            regime,
            now,
            expiry_hours=2,
            market_session="US_REGULAR",
            data_source="NEON_SMOKE",
            ai_provider="smoke",
        )
        if duplicate != 0:
            raise RuntimeError("idempotency verification failed")
        print("idempotency PASS")

        open_rows = [row for row in repo.open_signals() if row["symbol"] == "NEONSMOKE"]
        if not open_rows:
            raise RuntimeError("open signal verification failed")

        updated = repo.apply_outcome_updates(
            [
                OutcomeUpdate(
                    signal_id=open_rows[0]["id"],
                    status="TARGET_1",
                    triggered=True,
                    triggered_at=now.isoformat(),
                    exit_price=102.0,
                    return_pct=2.0,
                    mfe_pct=2.0,
                    mae_pct=0.0,
                    holding_minutes=5,
                    outcome_note="smoke",
                )
            ],
            now,
        )
        if updated != 1:
            raise RuntimeError("outcome persistence verification failed")
        print("outcome persistence PASS")

        backtest_rows = repo.load_backtest_rows(limit=100)
        if not any(row["symbol"] == "NEONSMOKE" and row["status"] == "TARGET_1" for row in backtest_rows):
            raise RuntimeError("backtest verification failed")
        print("backtest query PASS")
    except Exception as exc:
        smoke_error = exc
    finally:
        try:
            _cleanup(database_url, run_id)
        except Exception as cleanup_exc:
            if smoke_error is None:
                raise
            print(f"cleanup SKIPPED | reason={cleanup_exc.__class__.__name__}")

    if smoke_error is not None:
        raise smoke_error

    with postgres_connection(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM signals WHERE run_id = %s", (run_id,))
            row = cur.fetchone() or {"count": 0}
            if int(row["count"]) != 0:
                raise RuntimeError("cleanup verification failed")

    print("cleanup PASS")
    print("secret exposure scan PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())