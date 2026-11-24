"""Automatic read-only F&O quote loading for all stocks, without Streamlit calls.

Dhan: one broker bulk LTP request for a typical 219-stock universe.
Yahoo: independent incremental, rate-limited-by-download batches with progress.

No order, account, holdings, trade, token refresh, database or VM operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from queue import SimpleQueue
from typing import Mapping

from .dhan_market import DhanDataError, DhanUnderlying, fetch_dhan_ltp
from .fast_universe import match_cash_ids, resolve_equity_ids
from .screener import Quote, fetch_yahoo_quotes
from .load_priority import strongest_first


@dataclass(frozen=True)
class DhanQuoteResult:
    quotes: Mapping[str, Quote]
    ids: Mapping[str, int]
    errors: tuple[str, ...]
    requested: int
    requested_at: str


@dataclass(frozen=True)
class YahooChunk:
    quotes: Mapping[str, Quote]
    errors: tuple[str, ...]
    attempted: tuple[str, ...]
    completed: int
    total: int


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def deduplicate_underlyings(underlyings: list[DhanUnderlying]) -> list[DhanUnderlying]:
    """One cash equity quote per stock, regardless of F&O expiry months."""
    by_symbol = {}
    for x in underlyings:
        symbol = x.entry.symbol
        old = by_symbol.get(symbol)
        if old is not None and old.equity_security_id != x.equity_security_id:
            raise DhanDataError(f"Conflicting cash IDs for stock {symbol}")
        by_symbol[symbol] = x
    return [by_symbol[x] for x in sorted(by_symbol)]


def load_dhan_all(underlyings: list[DhanUnderlying], client_id: str, token: str) -> DhanQuoteResult:
    """Fetch entire F&O universe with 1+ Dhan quotes, no arbitrary 50-stock cap.

    If a batch returns some stock quotes but omits other stocks, retry missing
    once (read-only; existing rate limiter still applies). No infinite retries.
    """
    rows = deduplicate_underlyings(underlyings)
    by_symbol = {r.entry.symbol: r.equity_security_id for r in rows}
    errors = []
    if any(value <= 0 for value in by_symbol.values()):
        try:
            lookup = resolve_equity_ids(set(by_symbol))
            rows, matched = match_cash_ids(rows, lookup)
            if matched == 0:
                raise DhanDataError("No F&O stock symbols matched Dhan NSE_EQ IDs")
            by_symbol = {r.entry.symbol: r.equity_security_id for r in rows}
        except DhanDataError as exc:
            errors.append(f"Dhan NSE_EQ ID mapping: {exc}")
            return DhanQuoteResult({}, {}, tuple(errors), len(rows), now_utc())

    eligible = [r for r in rows if r.equity_security_id > 0]
    if len(eligible) != len(rows):
        errors.append(
            f"Cash-security IDs unavailable for {len(rows)-len(eligible)} stock(s). "
            "They remain unpriced."
        )
    if not eligible:
        return DhanQuoteResult({}, by_symbol, tuple(errors), len(rows), now_utc())

    try:
        quotes, problems = fetch_dhan_ltp(eligible, client_id, token)
    except DhanDataError as exc:
        quotes, problems = {}, [f"Dhan LTP: {exc}"]
    errors.extend(problems)
    missing = [r for r in eligible if r.entry.symbol not in quotes]
    # Retry only if the previous request returned some data and no fatal auth
    # or rate limit error; avoid endless polling of a failed broker connection.
    fatal = any(any(flag in e for flag in ("401", "403", "429", "token", "permission"))
                for e in problems)
    if quotes and missing and not fatal:
        try:
            extra, extra_errors = fetch_dhan_ltp(missing, client_id, token)
            quotes.update(extra)
            errors.extend(extra_errors)
        except DhanDataError as exc:
            errors.append(f"Missing-stock LTP retry: {exc}")
    return DhanQuoteResult(quotes, by_symbol, tuple(errors), len(rows), now_utc())


def priority_order(
    underlyings: list[DhanUnderlying],
    prior_quotes: Mapping[str, Quote] | None = None,
    expiry: str | None = None,
    threshold: Decimal = Decimal("700000"),
    technical_levels=None,
) -> list[str]:
    """Prioritize only *previously quoted* under-threshold stocks.

    Unknown prices cannot be classified as under ₹7 lakh. All unknowns still
    receive an automatic quote request.
    """
    rows = deduplicate_underlyings(underlyings)
    existing = prior_quotes or {}
    focused = []
    other = []
    for row in rows:
        sym = row.entry.symbol
        lot = row.entry.lots.get(expiry) if expiry else None
        old = existing.get(sym)
        if lot and old and old.price * lot < threshold:
            focused.append((old.price * lot, sym))
        else:
            other.append(sym)
    ordered = [sym for _, sym in sorted(focused)] + sorted(other)
    # With reliable cached EMA/R1 levels, prioritize trend strength above
    # contract-value preference. Preserve historic ordering without a cache.
    if technical_levels:
        return strongest_first(ordered, technical_levels, prior_quotes)
    return ordered


def load_yahoo_all(
    underlyings: list[DhanUnderlying],
    progress: SimpleQueue,
    prior_quotes: Mapping[str, Quote] | None = None,
    expiry: str | None = None,
    batch_size: int = 25,
    technical_levels=None,
) -> tuple[int, int]:
    """Stream all Yahoo stock quotes as independently completed batches.

    Never mix Yahoo and Dhan prices. Caller reads SimpleQueue on Streamlit's
    main thread; worker does NOT access Streamlit state or components.
    """
    if not 1 <= batch_size <= 50:
        raise ValueError("Yahoo chunk size must be 1–50 stocks")
    ordered = priority_order(underlyings, prior_quotes, expiry,
                             technical_levels=technical_levels)
    total = len(ordered)
    completed = 0
    for start in range(0, total, batch_size):
        symbols = ordered[start:start + batch_size]
        try:
            quotes, errors = fetch_yahoo_quotes(symbols, batch_size=batch_size)
        except Exception as exc:
            quotes, errors = {}, [f"Yahoo chunk {start // batch_size + 1}: {type(exc).__name__}"]
        completed += len(symbols)
        progress.put(YahooChunk(quotes, tuple(errors), tuple(symbols), completed, total))
    return completed, total
