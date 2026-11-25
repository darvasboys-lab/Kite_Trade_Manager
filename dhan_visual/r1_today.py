"""Read-only NSE stock F&O 'crossed MONTHLY R1 today' detection.

A crossing is verified only using actual, timezone-aware, regular-session
15-minute Yahoo OHLC bars from the CURRENT IST calendar trading date.
Any bar whose high pierces strictly above the monthly R1, after a verified
below/equal preceding close or bar open, qualifies. Overnight gap-ups qualify
only when a prior completed session close confirms the stock was at/below R1.

Monthly R1 is already calculated from the PREVIOUS completed month in the
existing daily Yahoo technical loader; this module never recalculates it from
incomplete current-month candles. Missing intraday bars/levels are UNKNOWN,
never interpreted as negative or a successful crossing.

The worker fetches all valid F&O symbols in bounded, paced Yahoo groups, with
no broker API calls, no streamlit context and no TradingView requests.
"""
from __future__ import annotations

from .api_monitor import record

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from queue import SimpleQueue
from threading import Event
from zoneinfo import ZoneInfo

import pandas as pd

from .screener import as_yahoo_symbol
from .technical_indicators import ticker_ohlc
from .fyers_readonly import FyersDataError, fetch_r1_intraday_bars

IST = ZoneInfo("Asia/Kolkata")
R1_BATCH_SIZE = 6
R1_BATCH_PAUSE_SEC = 8.0
R1_FAILED_BATCH_BACKOFF_SEC = 30.0
NSE_OPEN = time(9, 15)
NSE_LAST_BAR = time(15, 15)


@dataclass(frozen=True)
class R1TodayCross:
    symbol: str
    day: date
    r1: Decimal
    first_bar_time: str
    latest_close: Decimal
    latest_bar_time: str
    currently_above: bool
    pct_vs_r1: Decimal


@dataclass(frozen=True)
class R1ScanUpdate:
    records: dict[str, R1TodayCross]
    scanned: int
    total: int
    unknown: int = 0
    warnings: tuple[str, ...] = ()


def _positive(raw) -> Decimal | None:
    try:
        v = Decimal(str(raw))
        return v if v.is_finite() and v > 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def session_r1_cross(bars: pd.DataFrame, monthly_r1: Decimal,
                     day: date) -> tuple[R1TodayCross | None, Decimal | None]:
    """Return (cross, latest real 15m close), using only NSE regular-session bars.

    Caller replaces the cross's empty symbol with the verified NSE ticker.
    Today's first 15m candle can cross intrabar from an open below R1;
    a gap that *opens* above R1 needs a prior regular session's completed
    close at/below R1. Intraday re-crosses can also be detected after a
    candle close/open at/below R1, even if the stock was above yesterday.
    """
    level = _positive(monthly_r1)
    if level is None:
        raise ValueError("No valid current-month standard R1")
    if bars is None or bars.empty or not {"Open", "High", "Close"}.issubset(bars.columns):
        raise ValueError("Yahoo 15m OHLC is unavailable")
    try:
        idx = pd.to_datetime(bars.index)
    except (ValueError, TypeError) as exc:
        raise ValueError("Yahoo intraday candle time is malformed") from exc
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        # A UTC-naive timestamp must never be guessed to be an IST candle.
        raise ValueError("Yahoo intraday candles have no verified exchange timezone")
    frame = bars[["Open", "High", "Close"]].copy()
    frame.index = idx.tz_convert(IST)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    # Exact IST calendar trading day and regular NSE cash-market candle times.
    valid_window = [
        NSE_OPEN <= stamp.time().replace(tzinfo=None) <= NSE_LAST_BAR
        for stamp in frame.index
    ]
    frame = frame.loc[valid_window]
    for col in ("Open", "High", "Close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Close"])
    frame = frame.loc[
        (frame["Open"] > 0) & (frame["High"] > 0) & (frame["Close"] > 0)
        & (frame["High"] >= frame["Open"])
        & (frame["High"] >= frame["Close"])
    ]
    # Prevent accidental future/past-date signals, including after market close.
    today_rows = frame.loc[[stamp.date() == day for stamp in frame.index]]
    if today_rows.empty:
        raise ValueError("No verified 15m trading candles for today's NSE session")
    last = today_rows.iloc[-1]
    latest_close = _positive(last["Close"])
    if latest_close is None:
        raise ValueError("Latest NSE session close is unavailable")

    # Prior session close is used ONLY for an opening-gap crossover, and must
    # come from a completed 15:15 IST regular-session candle.
    earlier = frame.loc[[stamp.date() < day for stamp in frame.index]]
    previous_completed_close = None
    if not earlier.empty:
        completed = earlier.loc[
            [stamp.time().replace(tzinfo=None) == NSE_LAST_BAR
             for stamp in earlier.index]
        ]
        if not completed.empty:
            latest_completed = completed.iloc[-1]
            # Never treat 5d-old stale series as yesterday's verified signal.
            if (day - completed.index[-1].date()) <= timedelta(days=7):
                previous_completed_close = _positive(latest_completed["Close"])

    first_cross = None
    previous_candle_close = None
    for i, (stamp, candle) in enumerate(today_rows.iterrows()):
        opened = _positive(candle["Open"])
        high = _positive(candle["High"])
        close = _positive(candle["Close"])
        if opened is None or high is None or close is None:
            continue
        # A first-bar gap above R1 is only a true *new* crossing if the
        # previous completed session was at or below R1.
        if i == 0 and opened > level:
            crossed_from_below = (
                previous_completed_close is not None
                and previous_completed_close <= level
            )
        else:
            crossed_from_below = (
                opened <= level
                or (previous_candle_close is not None
                    and previous_candle_close <= level)
            )
        if first_cross is None and crossed_from_below and high > level:
            first_cross = stamp.strftime("%H:%M")
        previous_candle_close = close

    if first_cross is None:
        return None, latest_close
    crossing = R1TodayCross(
        symbol="", day=day, r1=level, first_bar_time=first_cross,
        latest_close=latest_close,
        latest_bar_time=today_rows.index[-1].strftime("%H:%M"),
        currently_above=latest_close > level,
        pct_vs_r1=(latest_close - level) / level * Decimal(100),
    )
    return crossing, latest_close


def yahoo_r1_batch(symbols: list[str], r1_by_symbol: dict[str, Decimal],
                   *, today: date) -> tuple[dict[str, R1TodayCross], int, tuple[str, ...]]:
    """At most six tickers per single read-only yfinance 15-minute history call."""
    if not 1 <= len(symbols) <= 6:
        raise ValueError("R1 batch must contain 1–6 underlying symbols")
    import yfinance as yf

    record("Yahoo")
    raw = yf.download(
        tickers=" ".join(as_yahoo_symbol(sym) for sym in symbols),
        interval="15m", period="5d", group_by="ticker",
        auto_adjust=False, threads=False, progress=False, prepost=False,
        timeout=20,
    )
    found: dict[str, R1TodayCross] = {}
    unavailable = 0
    warnings = []
    for symbol in symbols:
        try:
            rows = ticker_ohlc(raw, symbol, len(symbols))
            event, _ = session_r1_cross(rows, r1_by_symbol[symbol], today)
            if event is not None:
                found[symbol] = R1TodayCross(
                    symbol=symbol, day=event.day, r1=event.r1,
                    first_bar_time=event.first_bar_time,
                    latest_close=event.latest_close,
                    latest_bar_time=event.latest_bar_time,
                    currently_above=event.currently_above,
                    pct_vs_r1=event.pct_vs_r1,
                )
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            unavailable += 1
            warnings.append(f"{symbol}: {str(exc)[:75]}")
    return found, unavailable, tuple(warnings[:3])


def scan_r1_today(
    symbols: list[str], r1_by_symbol: dict[str, Decimal],
    queue: SimpleQueue, cancel: Event, *,
    today: date | None = None,
    pause: float = R1_BATCH_PAUSE_SEC,
    batch_size: int = R1_BATCH_SIZE,
    fyers_access=None,
) -> tuple[int, int]:
    """Cancellable paced scan; skips missing R1 instead of fabricating breaks."""
    if not 1 <= batch_size <= 6:
        raise ValueError("Invalid R1 Yahoo batch size")
    day = today or datetime.now(IST).date()
    unique = list(dict.fromkeys(symbols))
    total = len(unique)
    completed = 0
    qualified = []
    for sym in unique:
        level = _positive(r1_by_symbol.get(sym))
        if level is None:
            completed += 1
            queue.put(R1ScanUpdate({}, completed, total, 1, ()))
        else:
            qualified.append(sym)
    for start in range(0, len(qualified), batch_size):
        if cancel.is_set():
            break
        group = qualified[start:start + batch_size]
        if fyers_access is not None:
            found, unknown, warnings = {}, 0, []
            for symbol in group:
                if cancel.is_set():
                    break
                try:
                    bars = fetch_r1_intraday_bars(
                        fyers_access, symbol, day=day,
                    )
                    event, _ = session_r1_cross(
                        bars, r1_by_symbol[symbol], day,
                    )
                    if event is not None:
                        found[symbol] = R1TodayCross(
                            symbol, event.day, event.r1, event.first_bar_time,
                            event.latest_close, event.latest_bar_time,
                            event.currently_above, event.pct_vs_r1,
                        )
                except (FyersDataError, ValueError, KeyError) as exc:
                    unknown += 1
                    if len(warnings) < 3:
                        warnings.append(f"{symbol}: {str(exc)[:75]}")
                    if ("limit" in str(exc).lower()
                            or "expired" in str(exc).lower()
                            or "unauthorized" in str(exc).lower()):
                        cancel.set()
                        break
                except Exception:
                    unknown += 1
                    if len(warnings) < 3:
                        warnings.append(f"{symbol}: FYERS intraday unavailable")
            warnings = tuple(warnings)
        else:
            try:
                found, unknown, warnings = yahoo_r1_batch(
                    group, r1_by_symbol, today=day,
                )
            except Exception as exc:
                found, unknown = {}, len(group)
                warnings = (f"Yahoo 15m batch unavailable ({type(exc).__name__})",)
        completed += len(group)
        queue.put(R1ScanUpdate(found, completed, total, unknown, warnings))
        if start + batch_size < len(qualified):
            delay = (0 if fyers_access is not None else
                     R1_FAILED_BATCH_BACKOFF_SEC if unknown == len(group)
                     else pause)
            if cancel.wait(delay):
                break
    return completed, total
