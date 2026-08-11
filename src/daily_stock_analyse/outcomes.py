from __future__ import annotations

from datetime import UTC, datetime

from .signal_history import OutcomeUpdate


def evaluate_signal_outcomes(
    open_rows: list[dict],
    latest_prices: dict[str, float | None],
    as_of_utc: datetime,
) -> list[OutcomeUpdate]:
    updates: list[OutcomeUpdate] = []
    for row in open_rows:
        signal_id = row["id"]
        symbol = str(row["symbol"])
        signal = str(row["signal"])
        entry_trigger = _as_float(row["entry_trigger_price"])
        target_1 = _as_float(row["target_1"])
        target_2 = _as_float(row["target_2"])
        stop = _as_float(row["stop_loss"])
        invalidation = _as_float(row["invalidation_price"])
        triggered = bool(int(row["triggered"] or 0))
        triggered_at = _as_str(row["triggered_at"])
        expiry_at = _parse_iso(_as_str(row["expiry_at"]))

        current = latest_prices.get(symbol)
        if current is None:
            continue

        if not triggered and entry_trigger is not None:
            if signal == "LONG" and current >= entry_trigger:
                triggered = True
                triggered_at = as_of_utc.isoformat()
            elif signal == "SHORT" and current <= entry_trigger:
                triggered = True
                triggered_at = as_of_utc.isoformat()

        status = "OPEN"
        note = "Signal remains open"
        if not triggered:
            if expiry_at is not None and as_of_utc > expiry_at:
                status = "NO_TRIGGER"
                note = "Signal expired before trigger"
        else:
            if signal == "LONG":
                if stop is not None and current <= stop:
                    status = "STOP"
                    note = "Stop reached"
                elif invalidation is not None and current <= invalidation:
                    status = "INVALIDATED"
                    note = "Invalidation reached"
                elif target_2 is not None and current >= target_2:
                    status = "TARGET_2"
                    note = "Target 2 reached"
                elif target_1 is not None and current >= target_1:
                    status = "TARGET_1"
                    note = "Target 1 reached"
            elif signal == "SHORT":
                if stop is not None and current >= stop:
                    status = "STOP"
                    note = "Stop reached"
                elif invalidation is not None and current >= invalidation:
                    status = "INVALIDATED"
                    note = "Invalidation reached"
                elif target_2 is not None and current <= target_2:
                    status = "TARGET_2"
                    note = "Target 2 reached"
                elif target_1 is not None and current <= target_1:
                    status = "TARGET_1"
                    note = "Target 1 reached"

            if status == "OPEN" and expiry_at is not None and as_of_utc > expiry_at:
                status = "EXPIRED"
                note = "Signal triggered but did not resolve before expiry"

        metrics = _compute_metrics(
            signal=signal,
            current=current,
            entry_trigger=entry_trigger,
            prior_mfe=_as_float(row["mfe_pct"]),
            prior_mae=_as_float(row["mae_pct"]),
            triggered=triggered,
            triggered_at=triggered_at,
            as_of_utc=as_of_utc,
        )

        updates.append(
            OutcomeUpdate(
                signal_id=signal_id,
                status=status,
                triggered=triggered,
                triggered_at=triggered_at,
                exit_price=current if status in {"TARGET_1", "TARGET_2", "STOP", "INVALIDATED", "EXPIRED"} else None,
                return_pct=metrics["return_pct"],
                mfe_pct=metrics["mfe_pct"],
                mae_pct=metrics["mae_pct"],
                holding_minutes=metrics["holding_minutes"],
                outcome_note=note,
            )
        )

    return updates


def _compute_metrics(
    *,
    signal: str,
    current: float,
    entry_trigger: float | None,
    prior_mfe: float | None,
    prior_mae: float | None,
    triggered: bool,
    triggered_at: str | None,
    as_of_utc: datetime,
) -> dict[str, float | int | None]:
    if not triggered or entry_trigger is None or entry_trigger <= 0:
        return {"return_pct": None, "mfe_pct": prior_mfe, "mae_pct": prior_mae, "holding_minutes": None}

    if signal == "LONG":
        pnl_pct = ((current - entry_trigger) / entry_trigger) * 100.0
    else:
        pnl_pct = ((entry_trigger - current) / entry_trigger) * 100.0

    mfe = max(prior_mfe if prior_mfe is not None else pnl_pct, pnl_pct)
    mae = min(prior_mae if prior_mae is not None else pnl_pct, pnl_pct)

    minutes = None
    if triggered_at:
        triggered_dt = _parse_iso(triggered_at)
        if triggered_dt is not None:
            minutes = max(0, int((as_of_utc - triggered_dt).total_seconds() // 60))

    return {
        "return_pct": round(pnl_pct, 4),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "holding_minutes": minutes,
    }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _as_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_str(value) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None
