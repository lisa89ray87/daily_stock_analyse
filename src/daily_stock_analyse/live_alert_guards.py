from __future__ import annotations

from dataclasses import replace
from datetime import timedelta


def _num(value):
    return float(value) if isinstance(value, (int, float)) else None


def _setup_signature(event: dict) -> tuple:
    """Stable identity for an entry setup; price movement alone does not change it."""
    return (
        event.get("event_type"),
        event.get("symbol"),
        event.get("signal"),
        event.get("trigger_type"),
        _num(event.get("entry")),
        _num(event.get("stop")),
        _num(event.get("target1")),
        _num(event.get("target2")),
    )


def install_live_alert_guards(engine) -> None:
    """Install safety guards around the existing live-alert engine.

    The guards are deliberately transport/eligibility wrappers so the existing
    extended-hours strategy remains intact:
    - DATA_LIMITED/UNAVAILABLE RVOL may not be called fully confirmed.
    - A numeric RVOL below the configured gate blocks the alert.
    - A numeric RVOL meeting the gate while data quality is limited is reported
      as VOLUME_LIMITED_CONFIRMED rather than VOLUME_CONFIRMED.
    - Identical entry setups are emitted only once until their setup identity
      changes; the normal cooldown remains in force for different setups.
    """
    if getattr(engine, "_live_alert_guards_installed", False):
        return

    original_eligibility = engine._evaluate_alert_eligibility
    original_determine_event = engine._determine_event

    def guarded_eligibility(analysis, symbol_state, cfg, now, opening_range_window):
        result, v4, candidate_event_type, trade_levels, volume_lifecycle = original_eligibility(
            analysis, symbol_state, cfg, now, opening_range_window
        )

        quality = result.rvol_quality
        rvol = result.rvol
        phase = v4.get("phase", "NORMAL")
        required = cfg.v4_opening_min_rvol if phase == "OPENING" else cfg.v4_normal_min_rvol

        if quality in {"DATA_LIMITED", "UNAVAILABLE"}:
            if rvol is None:
                limited = engine.VolumeLifecycle(
                    state=engine.VOLUME_STATE_WAIT_FOR_VOLUME,
                    rvol=None,
                    required_rvol=required,
                    detail="RVOL quality is not reliable; numeric RVOL is unavailable",
                )
                result = replace(
                    result,
                    eligible=False,
                    reason=engine.ALERT_REASON_RVOL_TOO_LOW,
                    detail="RVOL unavailable; volume confirmation is required",
                )
                return result, v4, candidate_event_type, trade_levels, limited

            if rvol < required:
                limited = engine.VolumeLifecycle(
                    state=engine.VOLUME_STATE_WAIT_FOR_VOLUME,
                    rvol=rvol,
                    required_rvol=required,
                    detail=f"RVOL {rvol:.2f} is below required {required:.2f}; data quality={quality}",
                )
                result = replace(
                    result,
                    eligible=False,
                    reason=engine.ALERT_REASON_RVOL_TOO_LOW,
                    detail=f"RVOL {rvol:.2f} < required {required:.2f} (quality={quality})",
                )
                return result, v4, candidate_event_type, trade_levels, limited

            # The number clears the gate, but the provider cannot support the
            # stronger claim that this is a fully reliable regular-session RVOL.
            limited = engine.VolumeLifecycle(
                state="VOLUME_LIMITED_CONFIRMED",
                rvol=rvol,
                required_rvol=required,
                detail=f"RVOL {rvol:.2f} meets {required:.2f}; provider quality={quality}",
            )
            if result.eligible:
                result = replace(
                    result,
                    detail=f"Execution and risk gates passed; RVOL gate met with {quality} data",
                )
            return result, v4, candidate_event_type, trade_levels, limited

        return result, v4, candidate_event_type, trade_levels, volume_lifecycle

    def guarded_determine_event(analysis, state, cfg, now, opening_range_window):
        event = original_determine_event(analysis, state, cfg, now, opening_range_window)
        if event is None:
            return None

        event_type = event.get("event_type")
        if event_type not in {"WAIT_TO_LONG", "WAIT_TO_SHORT"}:
            return event

        symbol_state = state.setdefault("symbols", {}).setdefault(analysis.symbol, {})
        signature = _setup_signature(event)
        previous = symbol_state.get("last_alert_signature")
        if previous == list(signature) or previous == signature:
            symbol_state["last_alert_decision"] = "DUPLICATE_SETUP_SUPPRESSED"
            symbol_state["last_alert_reason"] = "Identical active entry setup already alerted"
            print(
                f"{analysis.symbol}: ALERT_SUPPRESSED | DUPLICATE_SETUP | "
                "same direction/trigger/entry/stop/target as previous alert"
            )
            return None

        symbol_state["last_alert_signature"] = list(signature)
        return event

    engine._evaluate_alert_eligibility = guarded_eligibility
    engine._determine_event = guarded_determine_event
    engine._live_alert_guards_installed = True
