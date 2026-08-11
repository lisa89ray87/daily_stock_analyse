from __future__ import annotations

from datetime import UTC, datetime
import re
from uuid import uuid4

import pytest

from src.daily_stock_analyse.database import neon_smoke
from src.daily_stock_analyse.database.schema import ensure_schema
from src.daily_stock_analyse.outcomes import evaluate_signal_outcomes


def _meta(type_name: str) -> dict:
    if isinstance(type_name, dict):
        normalized = str(type_name.get("type") or "text").lower()
        is_nullable = str(type_name.get("is_nullable") or "YES")
        column_default = type_name.get("column_default")
    else:
        normalized = type_name.lower()
        is_nullable = "YES"
        column_default = None
    if normalized == "uuid":
        return {"data_type": "uuid", "udt_name": "uuid", "is_nullable": is_nullable, "column_default": column_default}
    if normalized in {"bigint", "int8"}:
        return {"data_type": "bigint", "udt_name": "int8", "is_nullable": is_nullable, "column_default": column_default}
    if normalized in {"integer", "int4"}:
        return {"data_type": "integer", "udt_name": "int4", "is_nullable": is_nullable, "column_default": column_default}
    return {"data_type": "text", "udt_name": "text", "is_nullable": is_nullable, "column_default": column_default}


class _SchemaCursor:
    def __init__(self, state: dict):
        self.state = state
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params=None):
        sql = " ".join(query.split())
        lower = sql.lower()
        self.rows = []
        self.rowcount = 0

        if lower.startswith("create table if not exists schema_migrations"):
            self.state["tables"].setdefault("schema_migrations", {"columns": {"version": "integer", "applied_at": "text"}, "rows": []})
            return self

        if lower.startswith("create table if not exists analysis_runs"):
            self.state["tables"].setdefault(
                "analysis_runs",
                {"columns": {"run_id": "text", "generated_at": "text", "market_session": "text", "market_regime": "text", "data_source": "text", "created_at": "text"}, "rows": []},
            )
            return self

        if lower.startswith("create table signals"):
            self.state["tables"]["signals"] = {
                "columns": {
                    "signal_id": "bigint",
                    "run_id": "text",
                    "symbol": "text",
                    "direction": "text",
                    "status": "text",
                    "created_at": "text",
                    "updated_at": "text",
                },
                "rows": [],
            }
            return self

        if lower.startswith("create table signal_outcomes"):
            signal_id_type = "uuid" if "signal_id uuid" in lower else "bigint"
            self.state["tables"]["signal_outcomes"] = {
                "columns": {
                    "outcome_id": "bigint",
                    "signal_id": signal_id_type,
                    "evaluation_time": "text",
                    "evaluation_price": "bigint",
                    "target_1_hit": "text",
                    "target_2_hit": "text",
                    "invalidated": "text",
                    "pnl_percent": "bigint",
                    "outcome": "text",
                    "created_at": "text",
                },
                "rows": [],
            }
            return self

        if "select to_regclass" in lower:
            if params:
                raw = params[0]
            else:
                match = re.search(r"to_regclass\('public\.([a-z_]+)'\)", lower)
                raw = f"public.{match.group(1)}" if match else "public.unknown"
            table = str(raw).split(".")[-1]
            self.rows = [{"name": table if table in self.state["tables"] else None}]
            return self

        if "from information_schema.columns" in lower:
            table_match = re.search(r"table_name = '([^']+)'", lower)
            table_name = params[0] if params else (table_match.group(1) if table_match else "")
            column_filter_match = re.search(r"column_name = '([^']+)'", lower)
            column_filter = column_filter_match.group(1) if column_filter_match else None
            columns = self.state["tables"].get(str(table_name), {}).get("columns", {})
            if "select 1 as present" in lower or "limit 1" in lower:
                if column_filter and column_filter in columns:
                    self.rows = [{"present": 1}]
                else:
                    self.rows = []
                return self
            self.rows = []
            for column_name, type_name in columns.items():
                if column_filter and column_name != column_filter:
                    continue
                row = {"column_name": column_name}
                row.update(_meta(type_name))
                self.rows.append(row)
            return self

        if "from pg_constraint" in lower:
            name = params[0]
            self.rows = [{"present": 1}] if name in self.state["constraints"] else []
            return self

        if lower.startswith("alter table signals rename column id to signal_id"):
            table = self.state["tables"]["signals"]
            table["columns"]["signal_id"] = table["columns"].pop("id")
            for row in table["rows"]:
                row["signal_id"] = row.pop("id")
            return self

        if lower.startswith("alter table signal_outcomes rename column signal_id to legacy_signal_id"):
            table = self.state["tables"]["signal_outcomes"]
            table["columns"]["legacy_signal_id"] = table["columns"].pop("signal_id")
            for row in table["rows"]:
                row["legacy_signal_id"] = row.pop("signal_id")
            return self

        if lower.startswith("alter table signals alter column strategy_id drop not null"):
            column = self.state["tables"]["signals"]["columns"].get("strategy_id")
            if isinstance(column, dict):
                column["is_nullable"] = "YES"
            return self

        if lower.startswith("alter table signals alter column action drop not null"):
            column = self.state["tables"]["signals"]["columns"].get("action")
            if isinstance(column, dict):
                column["is_nullable"] = "YES"
            return self

        if lower.startswith("alter table signals rename column confidence to legacy_confidence_numeric"):
            table = self.state["tables"]["signals"]
            table["columns"]["legacy_confidence_numeric"] = table["columns"].pop("confidence")
            for row in table["rows"]:
                row["legacy_confidence_numeric"] = row.pop("confidence")
            return self

        if lower.startswith("alter table") and " add column if not exists " in lower:
            match = re.search(r"alter table ([a-z_]+) add column if not exists ([a-z0-9_]+) ([a-z0-9_ ]+)", lower)
            assert match is not None
            table_name, column_name, type_decl = match.groups()
            table = self.state["tables"].setdefault(table_name, {"columns": {}, "rows": []})
            if column_name not in table["columns"]:
                if "uuid" in type_decl:
                    column_type = "uuid"
                elif "bigint" in type_decl or "double precision" in type_decl:
                    column_type = "bigint"
                elif "integer" in type_decl:
                    column_type = "integer"
                else:
                    column_type = "text"
                table["columns"][column_name] = column_type
                for row in table["rows"]:
                    row[column_name] = None
            return self

        if lower.startswith("alter table") and " add constraint " in lower:
            match = re.search(r"add constraint ([a-z_]+)", lower)
            assert match is not None
            self.state["constraints"].add(match.group(1))
            return self

        if lower.startswith("create index if not exists") or lower.startswith("create unique index if not exists"):
            match = re.search(r"if not exists ([a-z_]+)", lower)
            assert match is not None
            self.state["indexes"].add(match.group(1))
            return self

        if lower.startswith("insert into schema_migrations"):
            version_match = re.search(r"values \((\d+)\)", lower)
            assert version_match is not None
            self.state["migrations"].add(int(version_match.group(1)))
            return self

        if lower.startswith("update signal_outcomes so set signal_id = s.signal_id"):
            outcomes_rows = self.state["tables"].get("signal_outcomes", {}).get("rows", [])
            signals_rows = self.state["tables"].get("signals", {}).get("rows", [])
            for outcome_row in outcomes_rows:
                if outcome_row.get("signal_id") is not None:
                    continue
                legacy = outcome_row.get("legacy_signal_id")
                if legacy is None:
                    continue
                for signal_row in signals_rows:
                    if str(signal_row.get("signal_id")) == str(legacy):
                        outcome_row["signal_id"] = signal_row.get("signal_id")
                        break
            return self

        if lower.startswith("update signals set direction = coalesce(direction, signal::text)"):
            signal_rows = self.state["tables"].get("signals", {}).get("rows", [])
            for row in signal_rows:
                if row.get("direction") is None and row.get("signal") is not None:
                    row["direction"] = row.get("signal")
            return self

        if lower.startswith("update signals set confidence = coalesce(confidence, legacy_confidence_numeric::text)"):
            signal_rows = self.state["tables"].get("signals", {}).get("rows", [])
            for row in signal_rows:
                if row.get("confidence") is None and row.get("legacy_confidence_numeric") is not None:
                    row["confidence"] = str(row.get("legacy_confidence_numeric"))
            return self

        if lower.startswith("delete from signal_outcomes"):
            run_id = params[0]
            signal_rows = self.state["tables"].get("signals", {}).get("rows", [])
            signal_ids = {row["signal_id"] for row in signal_rows if row.get("run_id") == run_id}
            table = self.state["tables"].get("signal_outcomes", {})
            table["rows"] = [row for row in table.get("rows", []) if row.get("signal_id") not in signal_ids]
            return self

        if lower.startswith("delete from signals where run_id = %s"):
            run_id = params[0]
            table = self.state["tables"].get("signals", {})
            table["rows"] = [row for row in table.get("rows", []) if row.get("run_id") != run_id]
            return self

        if lower.startswith("delete from analysis_runs where run_id = %s"):
            run_id = params[0]
            table = self.state["tables"].get("analysis_runs", {})
            table["rows"] = [row for row in table.get("rows", []) if row.get("run_id") != run_id]
            return self

        raise AssertionError(f"Unhandled SQL in test fake: {sql}")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _SchemaConn:
    def __init__(self, state: dict):
        self.state = state

    def cursor(self):
        return _SchemaCursor(self.state)

    def commit(self):
        self.state["commit_count"] += 1


def _legacy_uuid_state() -> dict:
    signal_uuid = str(uuid4())
    return {
        "tables": {
            "signals": {
                "columns": {
                    "signal_id": "uuid",
                    "symbol": "text",
                    "direction": "text",
                    "status": "text",
                    "created_at": "text",
                },
                "rows": [{"signal_id": signal_uuid, "symbol": "AMD", "direction": "LONG", "status": "OPEN", "created_at": "2026-08-11T00:00:00Z"}],
            },
            "signal_outcomes": {
                "columns": {
                    "outcome_id": "bigint",
                    "signal_id": "bigint",
                    "evaluation_time": "text",
                    "outcome": "text",
                },
                "rows": [{"outcome_id": 1, "signal_id": 123, "evaluation_time": "2026-08-11T00:00:00Z", "outcome": "OPEN"}],
            },
        },
        "constraints": set(),
        "indexes": set(),
        "migrations": set(),
        "commit_count": 0,
    }


def test_ensure_schema_preserves_uuid_signal_id_and_adds_run_id_idempotently():
    state = _legacy_uuid_state()
    conn = _SchemaConn(state)

    ensure_schema(conn)
    ensure_schema(conn)

    signals = state["tables"]["signals"]
    assert signals["columns"]["signal_id"] == "uuid"
    assert "run_id" in signals["columns"]
    assert signals["rows"][0]["signal_id"]
    assert signals["rows"][0]["run_id"] is None
    assert "uq_signals_run_symbol_direction" in state["indexes"]
    assert "fk_signals_run_id" in state["constraints"]
    assert state["migrations"] == {1, 2, 3, 4, 5}


def test_ensure_schema_migrates_signal_outcomes_signal_id_to_match_uuid_signals():
    state = _legacy_uuid_state()
    conn = _SchemaConn(state)

    ensure_schema(conn)

    outcomes = state["tables"]["signal_outcomes"]
    assert outcomes["columns"]["signal_id"] == "uuid"
    assert outcomes["columns"]["legacy_signal_id"] == "bigint"
    assert outcomes["rows"][0]["legacy_signal_id"] == 123
    assert outcomes["rows"][0]["signal_id"] is None
    assert "fk_signal_outcomes_signal_id" in state["constraints"]
    assert "uq_signal_outcomes_signal_eval_time" in state["indexes"]


def test_ensure_schema_backfills_direction_from_legacy_signal_column():
    state = _legacy_uuid_state()
    state["tables"]["signals"]["columns"]["signal"] = "text"
    state["tables"]["signals"]["rows"][0]["signal"] = "LONG"
    conn = _SchemaConn(state)

    ensure_schema(conn)

    signals = state["tables"]["signals"]
    assert "direction" in signals["columns"]
    assert signals["rows"][0]["direction"] == "LONG"


def test_ensure_schema_preserves_legacy_numeric_confidence_and_exposes_text_confidence():
    state = _legacy_uuid_state()
    state["tables"]["signals"]["columns"]["confidence"] = "bigint"
    state["tables"]["signals"]["rows"][0]["confidence"] = 85
    conn = _SchemaConn(state)

    ensure_schema(conn)

    signals = state["tables"]["signals"]
    assert signals["columns"]["legacy_confidence_numeric"] == "bigint"
    assert signals["columns"]["confidence"] == "text"
    assert signals["rows"][0]["legacy_confidence_numeric"] == 85
    assert signals["rows"][0]["confidence"] == "85"


def test_ensure_schema_relaxes_legacy_strategy_id_not_null_requirement():
    state = _legacy_uuid_state()
    state["tables"]["signals"]["columns"]["strategy_id"] = {"type": "uuid", "is_nullable": "NO", "column_default": None}
    state["tables"]["signals"]["rows"][0]["strategy_id"] = str(uuid4())
    conn = _SchemaConn(state)

    ensure_schema(conn)

    strategy_column = state["tables"]["signals"]["columns"]["strategy_id"]
    assert isinstance(strategy_column, dict)
    assert strategy_column["is_nullable"] == "YES"


def test_ensure_schema_relaxes_legacy_action_not_null_requirement():
    state = _legacy_uuid_state()
    state["tables"]["signals"]["columns"]["action"] = {"type": "text", "is_nullable": "NO", "column_default": None}
    state["tables"]["signals"]["rows"][0]["action"] = "BUY"
    conn = _SchemaConn(state)

    ensure_schema(conn)
    ensure_schema(conn)

    action_column = state["tables"]["signals"]["columns"]["action"]
    assert isinstance(action_column, dict)
    assert action_column["is_nullable"] == "YES"
    assert state["tables"]["signals"]["rows"][0]["action"] == "BUY"


def test_uuid_signal_id_is_preserved_by_outcome_engine():
    signal_id = uuid4()
    updates = evaluate_signal_outcomes(
        [
            {
                "id": signal_id,
                "symbol": "AAA",
                "signal": "LONG",
                "entry_trigger_price": 100.0,
                "target_1": 101.0,
                "target_2": 102.0,
                "stop_loss": 98.0,
                "invalidation_price": 98.0,
                "triggered": False,
                "triggered_at": None,
                "expiry_at": None,
                "mfe_pct": None,
                "mae_pct": None,
            }
        ],
        latest_prices={"AAA": 102.5},
        as_of_utc=datetime.now(UTC),
    )
    assert updates[0].signal_id == signal_id


def test_neon_smoke_cleanup_deletes_only_rows_for_target_run(monkeypatch):
    state = {
        "tables": {
            "signals": {
                "columns": {"signal_id": "uuid", "run_id": "text"},
                "rows": [
                    {"signal_id": "uuid-a", "run_id": "target-run"},
                    {"signal_id": "uuid-b", "run_id": "other-run"},
                ],
            },
            "signal_outcomes": {
                "columns": {"signal_id": "uuid"},
                "rows": [
                    {"signal_id": "uuid-a"},
                    {"signal_id": "uuid-b"},
                ],
            },
            "analysis_runs": {"columns": {"run_id": "text"}, "rows": [{"run_id": "target-run"}, {"run_id": "other-run"}]},
        },
        "constraints": set(),
        "indexes": set(),
        "migrations": set(),
        "commit_count": 0,
    }

    def _fake_postgres_connection(_database_url: str):
        class _Ctx:
            def __enter__(self_nonlocal):
                return _SchemaConn(state)

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()

    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke.postgres_connection", _fake_postgres_connection)

    neon_smoke._cleanup("postgresql://example", "target-run")

    assert state["tables"]["signals"]["rows"] == [{"signal_id": "uuid-b", "run_id": "other-run"}]
    assert state["tables"]["signal_outcomes"]["rows"] == [{"signal_id": "uuid-b"}]
    assert state["tables"]["analysis_runs"]["rows"] == [{"run_id": "other-run"}]


def test_neon_smoke_cleanup_skips_when_legacy_signals_lack_run_id(monkeypatch):
    state = {
        "tables": {
            "signals": {"columns": {"signal_id": "uuid"}, "rows": [{"signal_id": "uuid-a"}]},
            "signal_outcomes": {"columns": {"signal_id": "uuid"}, "rows": [{"signal_id": "uuid-a"}]},
            "analysis_runs": {"columns": {"run_id": "text"}, "rows": [{"run_id": "target-run"}]},
        },
        "constraints": set(),
        "indexes": set(),
        "migrations": set(),
        "commit_count": 0,
    }

    def _fake_postgres_connection(_database_url: str):
        class _Ctx:
            def __enter__(self_nonlocal):
                return _SchemaConn(state)

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()

    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke.postgres_connection", _fake_postgres_connection)

    neon_smoke._cleanup("postgresql://example", "target-run")

    assert state["tables"]["signals"]["rows"] == [{"signal_id": "uuid-a"}]
    assert state["tables"]["signal_outcomes"]["rows"] == [{"signal_id": "uuid-a"}]
    assert state["tables"]["analysis_runs"]["rows"] == []


def test_neon_smoke_main_preserves_original_failure_when_cleanup_also_fails(monkeypatch, capsys):
    secret = "postgresql://user:super-secret@host/db"
    monkeypatch.setenv("DATABASE_URL", secret)
    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke._verify_tls_connection", lambda conn: None)
    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke.ensure_schema", lambda conn: (_ for _ in ()).throw(RuntimeError("schema broke")))
    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke._cleanup", lambda database_url, run_id: (_ for _ in ()).throw(RuntimeError("cleanup broke")))

    def _fake_postgres_connection(_database_url: str):
        class _Ctx:
            def __enter__(self_nonlocal):
                return _SchemaConn({"tables": {}, "constraints": set(), "indexes": set(), "migrations": set(), "commit_count": 0})

            def __exit__(self_nonlocal, exc_type, exc, tb):
                return False

        return _Ctx()

    monkeypatch.setattr("src.daily_stock_analyse.database.neon_smoke.postgres_connection", _fake_postgres_connection)

    with pytest.raises(RuntimeError, match="schema broke"):
        neon_smoke.main()
    captured = capsys.readouterr()
    assert "super-secret" not in captured.out
    assert "super-secret" not in captured.err


def test_neon_smoke_tls_verification_accepts_runtime_ssl_state():
    class _Info:
        def get_parameters(self):
            return {"sslmode": "require", "channel_binding": "require"}

    class _PgConn:
        ssl_in_use = True

    class _Conn:
        info = _Info()
        pgconn = _PgConn()

    neon_smoke._verify_tls_connection(_Conn())


def test_neon_smoke_tls_verification_rejects_missing_runtime_ssl_even_if_params_require_it():
    class _Info:
        def get_parameters(self):
            return {"sslmode": "require", "channel_binding": "require"}

    class _PgConn:
        ssl_in_use = False

    class _Conn:
        info = _Info()
        pgconn = _PgConn()

    with pytest.raises(RuntimeError, match="ssl check failed"):
        neon_smoke._verify_tls_connection(_Conn())


def test_neon_smoke_tls_verification_falls_back_to_ssl_attributes_when_ssl_in_use_is_unavailable():
    class _Info:
        def get_parameters(self):
            return {"sslmode": "require", "channel_binding": "require"}

    class _PgConn:
        ssl_in_use = None

        @staticmethod
        def ssl_attribute(name: str):
            return {"protocol": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384"}.get(name)

    class _Conn:
        info = _Info()
        pgconn = _PgConn()

    neon_smoke._verify_tls_connection(_Conn())