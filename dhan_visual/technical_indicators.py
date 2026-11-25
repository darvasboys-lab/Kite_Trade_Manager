"""Read-only Yahoo technical levels for stock F&O underlyings.

EMA 10/20/50/200 use >=span actual daily closing bars and adjust=False.
Standard monthly floor-pivot R1 = 2*(H+L+C)/3 - L, using the PREVIOUS
COMPLETED calendar month's unadjusted high, low and final trading-day close.
No synthetic OHLC, no forward-fill, no intraday history and no broker orders.
"""
from __future__ import annotations

from .api_monitor import record

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import math

import pandas as pd

from .screener import as_yahoo_symbol

EMA_PERIODS = (200, 50, 20, 10)


@dataclass(frozen=True)
class TechnicalLevels:
    ema200: Decimal | None
    ema50: Decimal | None
    ema20: Decimal | None
    ema10: Decimal | None
    monthly_r1: Decimal | None
    last_daily_close: Decimal | None
    asof: str
    pivot_month: str

    def all_values(self) -> tuple[Decimal | None, ...]:
        return self.ema200, self.ema50, self.ema20, self.ema10, self.monthly_r1


def _number(raw) -> Decimal | None:
    try:
        x = Decimal(str(raw))
        if x.is_finite() and x > 0:
            return x
    except (ValueError, TypeError, ArithmeticError):
        pass
    return None


def _previous_month(day: date) -> tuple[int, int]:
    return (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)


def ticker_ohlc(frame: pd.DataFrame, symbol: str, multi_count: int) -> pd.DataFrame:
    """Support both yfinance MultiIndex layouts and single ticker frames."""
    ticker = as_yahoo_symbol(symbol)
    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        if ticker in frame.columns.get_level_values(0):
            return frame[ticker].copy()
        if ticker in frame.columns.get_level_values(1):
            return frame.xs(ticker, axis=1, level=1).copy()
        return pd.DataFrame()
    if multi_count == 1:
        return frame.copy()
    return pd.DataFrame()


def calculate_levels(frame: pd.DataFrame, *, current_month: date) -> TechnicalLevels:
    """Calculate indicators from complete Yahoo daily bars, not guessed values."""
    if frame is None or frame.empty or "Close" not in frame.columns:
        raise ValueError("Yahoo daily Close history unavailable")
    bars = frame.copy()
    try:
        dates = pd.to_datetime(bars.index)
        if dates.tz is not None:
            # NSE daily bars should be interpreted in the exchange timezone.
            dates = dates.tz_convert("Asia/Kolkata").tz_localize(None)
        bars.index = dates
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Yahoo OHLC dates are invalid") from exc
    close = pd.to_numeric(bars["Close"], errors="coerce")
    close = close.where((close > 0) & close.map(math.isfinite)).dropna()
    close = close[close.index.date <= current_month]
    if close.empty:
        raise ValueError("Yahoo daily Close has no usable completed bars")
    latest_date = close.index[-1].date()
    if latest_date > current_month:
        raise ValueError("Yahoo daily Close has a future date")

    em_values = {}
    for n in EMA_PERIODS:
        em_values[n] = (
            _number(float(close.ewm(span=n, adjust=False, min_periods=n).mean().iloc[-1]))
            if len(close) >= n else None
        )

    previous_y, previous_m = _previous_month(current_month)
    r1 = None
    if {"High", "Low", "Close"}.issubset(bars.columns):
        prior = bars.loc[
            (bars.index.year == previous_y) & (bars.index.month == previous_m)
        ].copy()
        if not prior.empty:
            for col in ("High", "Low", "Close"):
                prior[col] = pd.to_numeric(prior[col], errors="coerce")
            prior = prior.dropna(subset=["High", "Low", "Close"])
            prior = prior.loc[
                (prior["High"] > 0) & (prior["Low"] > 0) & (prior["Close"] > 0)
                & (prior["High"] >= prior["Low"])
            ].sort_index()
            if not prior.empty:
                month_high = _number(prior["High"].max())
                month_low = _number(prior["Low"].min())
                month_close = _number(prior["Close"].iloc[-1])
                if month_high and month_low and month_close:
                    p = (month_high + month_low + month_close) / 3
                    r1 = 2 * p - month_low
                    if r1 <= 0:
                        r1 = None
    return TechnicalLevels(
        ema200=em_values[200],
        ema50=em_values[50],
        ema20=em_values[20],
        ema10=em_values[10],
        monthly_r1=r1,
        last_daily_close=_number(close.iloc[-1]),
        asof=latest_date.isoformat(),
        pivot_month=f"{current_month.year:04d}-{current_month.month:02d}",
    )


def download_history_batch(symbols: list[str], *, current_month: date) -> tuple[dict[str, TechnicalLevels], list[str]]:
    """One bounded Yahoo 2-year daily group download; never use current quote API."""
    if not 1 <= len(symbols) <= 10:
        raise ValueError("Batch must contain 1–10 stock F&O symbols")
    import yfinance as yf

    record("Yahoo")
    frame = yf.download(
        tickers=" ".join(as_yahoo_symbol(x) for x in symbols),
        interval="1d", period="2y", group_by="ticker",
        auto_adjust=False, progress=False, threads=False, timeout=20,
    )
    result, errors = {}, []
    for symbol in symbols:
        try:
            bars = ticker_ohlc(frame, symbol, len(symbols))
            result[symbol] = calculate_levels(bars, current_month=current_month)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{symbol}: {str(exc)[:90]}")
    return result, errors


def format_level(level: Decimal | None, cmp: float | None) -> str:
    """Compact one-line cell, tick only when the displayed CMP is > the level."""
    if level is None:
        return "—"
    if cmp is None:
        return f"— ₹{level:,.2f}"
    try:
        price = Decimal(str(cmp))
        if not price.is_finite() or price <= 0:
            return f"— ₹{level:,.2f}"
        # A green check is an affirmative signal ONLY. An at/below-CMP
        # level stays neutral (no red cross or misleading negative badge).
        return f"✅ ₹{level:,.2f}" if price > level else f"₹{level:,.2f}"
    except (ValueError, TypeError, ArithmeticError):
        return f"— ₹{level:,.2f}"
