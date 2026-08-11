from __future__ import annotations

from datetime import UTC, datetime

from src.daily_stock_analyse.database.postgres import PostgresSignalRepository
from src.daily_stock_analyse.models import (
    BattlePlan,
    DataQuality,
    IntelligenceBlock,
    MarketData,
    MarketRegime,
    ScoreBreakdown,
    StockAnalysis,
)
from src.daily_stock_analyse.signal_history import OutcomeUpdate


class _FakeCursor:
    def __init__(self, state: dict):
        self.state = state
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params=None):
        q = " ".join(query.split()).lower()
        self.rowcount = 0

        if "insert into analysis_runs" in q:
            run_id = params[0]
            if run_id not in self.state["analysis_runs"]:
                self.state["analysis_runs"][run_id] = {
                    "run_id": run_id,
                    "generated_at": params[1],
                    "market_session": params[2],
                    "market_regime": params[3],
                    "data_source": params[4],
                }
                self.rowcount = 1
            return self

        if "insert into signals" in q:
            run_id, symbol, direction = params[0], params[1], params[2]
            key = (run_id, symbol, direction)
            if key in self.state["signal_key_index"]:
                self.rowcount = 0
                return self
            signal_id = self.state["next_signal_id"]
            self.state["next_signal_id"] += 1
            record = {
                "signal_id": signal_id,
                "run_id": run_id,
                "symbol": symbol,
                "signal": direction,
                "status": params[3],
                "confidence": params[4],
                "entry_trigger_price": params[6],
                "target_1": params[7],
                "target_2": params[8],
                "stop_loss": params[10],
                "invalidation_price": params[9],
                "triggered": params[16],
                "triggered_at": params[17],
                "return_pct": params[18],
                "mfe_pct": params[19],
                "mae_pct": params[20],
                "holding_minutes": params[21],
                "expiry_at": params[22],
                "market_regime_label": params[23],
                "catalyst_category": params[13],
                "created_at": params[24],
            }
            self.state["signals"][signal_id] = record
            self.state["signal_key_index"][key] = signal_id
            self.rowcount = 1
            return self

        if "select signal_id as id" in q and "from signals" in q:
            rows = [
                {
                    "id": rec["signal_id"],
                    "symbol": rec["symbol"],
                    "signal": rec["signal"],
                    "entry_trigger_price": rec["entry_trigger_price"],
                    "target_1": rec["target_1"],
                    "target_2": rec["target_2"],
                    "stop_loss": rec["stop_loss"],
                    "invalidation_price": rec["invalidation_price"],
                    "triggered": rec["triggered"],
                    "triggered_at": rec["triggered_at"],
                    "expiry_at": rec["expiry_at"],
                    "mfe_pct": rec["mfe_pct"],
                    "mae_pct": rec["mae_pct"],
                }
                for rec in self.state["signals"].values()
                if rec["status"] == "OPEN"
            ]
            self.rows = rows
            return self

        if "update signals" in q:
            signal_id = params[8]
            rec = self.state["signals"].get(signal_id)
            if rec:
                rec["status"] = params[0]
                rec["triggered"] = params[1]
                rec["triggered_at"] = params[2]
                rec["return_pct"] = params[3]
                rec["mfe_pct"] = params[4]
                rec["mae_pct"] = params[5]
                rec["holding_minutes"] = params[6]
                self.rowcount = 1
            return self

        if "insert into signal_outcomes" in q:
            self.state["outcomes"].append(
                {
                    "signal_id": params[0],
                    "evaluation_time": params[1],
                    "evaluation_price": params[2],
                    "target_1_hit": params[3],
                    "target_2_hit": params[4],
                    "invalidated": params[5],
                    "pnl_percent": params[6],
                    "outcome": params[7],
                }
            )
            self.rowcount = 1
            return self

        if "select s.signal_id" in q and "from signals s" in q:
            limit = params[0]
            rows = [
                {
                    "signal_id": rec["signal_id"],
                    "symbol": rec["symbol"],
                    "signal": rec["signal"],
                    "status": rec["status"],
                    "return_pct": rec["return_pct"],
                    "market_regime_label": rec["market_regime_label"],
                    "catalyst_category": rec["catalyst_category"],
                    "created_at": rec["created_at"],
                }
                for rec in self.state["signals"].values()
                if rec["status"] in {"TARGET_1", "TARGET_2", "STOP", "INVALIDATED", "NO_TRIGGER", "EXPIRED"}
            ]
            self.rows = rows[:limit]
            return self

        if "select count(*) as count from signals" in q:
            self.rows = [{"count": len(self.state["signals"])}]
            return self

        return self

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _FakeConn:
    def __init__(self, state: dict):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        return None


def _analysis(symbol: str, signal: str) -> StockAnalysis:
    return StockAnalysis(
        symbol=symbol,
        name=symbol,
        signal=signal,
        trading_horizon="DAY_TRADE",
        direction_bias="LONG_BIAS" if signal == "LONG" else "SHORT_BIAS",
        market_alignment="MARKET_ALIGNED",
        setup_score=80,
        day_trade_candidate=True,
        candidate_score=82,
        candidate_status="DAY_TRADE CANDIDATE",
        confirmation_needed="x",
        confidence="MEDIUM",
        one_liner="x",
        main_reason="x",
        risk_classification="MEDIUM",
        market_data=MarketData(symbol=symbol, price=100.0),
        intelligence=IntelligenceBlock(),
        battle_plan=BattlePlan("b", "s", "95", "105", "entry", "target", "invalid", "rr", entry_trigger_price=100.0, target_1=102.0, target_2=104.0, invalidation_price=98.0),
        score=ScoreBreakdown(total=0.0, long_score=0.0, short_score=0.0, components={}, weights={}),
        data_quality=DataQuality(True, True, True, True, True, "yfinance", []),
    )


def test_repository_idempotent_persistence_and_outcomes(monkeypatch):
    state = {
        "analysis_runs": {},
        "signals": {},
        "signal_key_index": {},
        "outcomes": [],
        "next_signal_id": 1,
    }

    def _fake_postgres_connection(_database_url: str):
        class _Ctx:
            def __enter__(self_nonlocal):
                return _FakeConn(state)

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()

    monkeypatch.setattr("src.daily_stock_analyse.database.postgres.postgres_connection", _fake_postgres_connection)
    monkeypatch.setattr("src.daily_stock_analyse.database.postgres.ensure_schema", lambda conn: None)

    repo = PostgresSignalRepository("postgresql://example")
    now = datetime.now(UTC)
    regime = MarketRegime("RISK_ON", "BULLISH", "x", "y", "z", {})

    persisted_first = repo.save_signals([
        _analysis("AAA", "LONG"),
        _analysis("BBB", "SHORT"),
    ], regime, now, expiry_hours=24, market_session="US_REGULAR", data_source="Live / Intraday Regular Session", ai_provider="openai")

    persisted_second = repo.save_signals([
        _analysis("AAA", "LONG"),
        _analysis("BBB", "SHORT"),
    ], regime, now, expiry_hours=24, market_session="US_REGULAR", data_source="Live / Intraday Regular Session", ai_provider="openai")

    assert persisted_first == 2
    assert persisted_second == 0
    assert repo.count_all() == 2

    open_rows = repo.open_signals()
    updates = [
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
            outcome_note="Target reached",
        )
    ]
    touched = repo.apply_outcome_updates(updates, now)
    assert touched == 1
    rows = repo.load_backtest_rows(limit=20)
    assert any(row["status"] == "TARGET_1" for row in rows)
    assert len(state["outcomes"]) == 1


def test_repository_persists_categorical_confidence_as_text(monkeypatch):
    state = {
        "analysis_runs": {},
        "signals": {},
        "signal_key_index": {},
        "outcomes": [],
        "next_signal_id": 1,
    }

    def _fake_postgres_connection(_database_url: str):
        class _Ctx:
            def __enter__(self_nonlocal):
                return _FakeConn(state)

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()

    monkeypatch.setattr("src.daily_stock_analyse.database.postgres.postgres_connection", _fake_postgres_connection)
    monkeypatch.setattr("src.daily_stock_analyse.database.postgres.ensure_schema", lambda conn: None)

    repo = PostgresSignalRepository("postgresql://example")
    now = datetime.now(UTC)
    regime = MarketRegime("RISK_ON", "BULLISH", "x", "y", "z", {})
    analysis = _analysis("AAA", "LONG")
    analysis.confidence = "HIGH"

    persisted = repo.save_signals(
        [analysis],
        regime,
        now,
        expiry_hours=24,
        market_session="US_REGULAR",
        data_source="Live / Intraday Regular Session",
        ai_provider="openai",
    )

    assert persisted == 1
    stored = next(iter(state["signals"].values()))
    assert stored["confidence"] == "HIGH"
    assert isinstance(stored["confidence"], str)
