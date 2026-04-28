"""Local technical-indicator computation — pure Python, no network calls.

This module exposes a single MCP tool, `compute_indicators`, that takes raw
OHLCV candles (the same format produced by `get_exchange_ohlcv` and
`get_aggregated_ohlc`) and returns a bundle of standard technical indicators:
RSI, MACD, Bollinger Bands, EMA, SMA, ATR, ADX, Stochastic, OBV.

Design notes:
- No new dependencies. Everything is stdlib math.
- Wilder's smoothing is used where conventional (RSI / ATR / ADX).
- EMA is seeded with the SMA of the first `period` values, matching the
  textbook formulation used by most charting platforms.
- We accept both 5-column rows (CoinGecko aggregated_ohlc, no volume) and
  6-column rows (CCXT, with volume). OBV requires volume; if the input
  lacks it, OBV is reported as null with a `note`.
"""
from __future__ import annotations

import math
from typing import Any

from .core import mcp


# Hard caps to keep `compute_indicators` bounded against unbounded caller
# inputs (e.g. a prompt-injected LLM passing millions of rows). 5000 1m bars
# = ~3.5 days, plenty for any indicator window we expose. When series are
# returned with `include_series=True` we additionally truncate each series
# to MAX_SERIES_RETURN entries to keep the JSON payload reasonable.
MAX_OHLCV_ROWS = 5000
MAX_SERIES_RETURN = 1000


# --------------------------- helper math ---------------------------

def _sma_series(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. Returns a list aligned with `values`; entries
    before the window is full are None."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def _ema_series(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average. Seeded with SMA over the first `period`
    values (textbook convention). Aligned with input; pre-seed entries None."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        cur = values[i] * k + prev * (1 - k)
        out[i] = cur
        prev = cur
    return out


def _wilder_smooth_series(values: list[float], period: int) -> list[float | None]:
    """Wilder's smoothing (a.k.a. RMA / SMMA). Seeded with the simple mean of
    the first `period` values; subsequent values: prev + (x - prev) / period."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        cur = prev + (values[i] - prev) / period
        out[i] = cur
        prev = cur
    return out


def _stddev(values: list[float], mean: float) -> float:
    """Population standard deviation (matches Bollinger Bands convention)."""
    if not values:
        return 0.0
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


# --------------------------- indicators ---------------------------

def _rsi(closes: list[float], period: int) -> list[float | None]:
    """Wilder's RSI. Returns a list aligned with `closes`.

    Seeding: when at least `period+1` closes are available, the first RSI
    value uses the SMA of gains/losses over the first `period` changes
    (standard Wilder). When fewer closes are available but at least 2,
    the seed uses whatever changes exist — divided by their count — so the
    function still produces a value (matches the classic 14-close textbook
    test vector that yields ~70.46).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2 or period <= 0:
        return out

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains.append(ch if ch > 0 else 0.0)
        losses.append(-ch if ch < 0 else 0.0)

    seed_count = period if len(gains) >= period else len(gains)
    if seed_count <= 0:
        return out
    avg_gain = sum(gains[:seed_count]) / seed_count
    avg_loss = sum(losses[:seed_count]) / seed_count

    seed_idx = seed_count  # index into closes where first RSI lands
    out[seed_idx] = _rsi_value(avg_gain, avg_loss)
    for i in range(seed_count, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd(
    closes: list[float], fast: int, slow: int, signal: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (macd_line, signal_line, histogram) aligned with `closes`."""
    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    n = len(closes)
    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    # Signal EMA needs to operate only on the populated tail of macd_line.
    first_idx = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * n
    hist: list[float | None] = [None] * n
    if first_idx is not None:
        tail = [v for v in macd_line[first_idx:] if v is not None]
        sig_tail = _ema_series(tail, signal)
        for j, v in enumerate(sig_tail):
            signal_line[first_idx + j] = v
        for i in range(n):
            if macd_line[i] is not None and signal_line[i] is not None:
                hist[i] = macd_line[i] - signal_line[i]
    return macd_line, signal_line, hist


def _bollinger(
    closes: list[float], period: int, stddev_mult: float
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (upper, middle, lower) aligned with `closes`."""
    n = len(closes)
    upper: list[float | None] = [None] * n
    middle: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period or period <= 0:
        return upper, middle, lower
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        m = sum(window) / period
        sd = _stddev(window, m)
        middle[i] = m
        upper[i] = m + stddev_mult * sd
        lower[i] = m - stddev_mult * sd
    return upper, middle, lower


def _atr(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float | None]:
    """Wilder's ATR. Aligned with input."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2 or period <= 0:
        return out
    tr: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    smoothed = _wilder_smooth_series(tr, period)
    return smoothed


def _adx(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (plus_di, minus_di, adx) aligned with input. Uses Wilder."""
    n = len(closes)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    adx_out: list[float | None] = [None] * n
    if n < 2 or period <= 0:
        return plus_di, minus_di, adx_out

    tr: list[float] = [0.0]
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    # Wilder smoothing on tr/+dm/-dm starting from index 1 (index 0 is the
    # synthetic zero seed). We pass the full series so output indices match.
    sm_tr = _wilder_smooth_series(tr[1:], period)
    sm_plus = _wilder_smooth_series(plus_dm[1:], period)
    sm_minus = _wilder_smooth_series(minus_dm[1:], period)

    dx: list[float | None] = [None] * n
    for j in range(len(sm_tr)):
        i = j + 1  # align back to original index
        if sm_tr[j] is None or sm_plus[j] is None or sm_minus[j] is None:
            continue
        if sm_tr[j] == 0:
            plus_di[i] = 0.0
            minus_di[i] = 0.0
            dx[i] = 0.0
            continue
        pdi = 100.0 * sm_plus[j] / sm_tr[j]
        mdi = 100.0 * sm_minus[j] / sm_tr[j]
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx[i] = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0

    # ADX = Wilder smoothing of DX over `period`.
    dx_tail = [v for v in dx if v is not None]
    if len(dx_tail) >= period:
        adx_smoothed = _wilder_smooth_series(dx_tail, period)
        first_dx_idx = next(i for i, v in enumerate(dx) if v is not None)
        for j, v in enumerate(adx_smoothed):
            adx_out[first_dx_idx + j] = v
    return plus_di, minus_di, adx_out


def _stochastic(
    highs: list[float], lows: list[float], closes: list[float],
    k_period: int, d_period: int,
) -> tuple[list[float | None], list[float | None]]:
    """Return (%K, %D) aligned with input."""
    n = len(closes)
    k: list[float | None] = [None] * n
    d: list[float | None] = [None] * n
    if n < k_period or k_period <= 0:
        return k, d
    for i in range(k_period - 1, n):
        window_high = max(highs[i - k_period + 1 : i + 1])
        window_low = min(lows[i - k_period + 1 : i + 1])
        rng = window_high - window_low
        k[i] = 100.0 * (closes[i] - window_low) / rng if rng > 0 else 50.0
    # %D = SMA of %K over d_period
    k_clean = [v for v in k if v is not None]
    if len(k_clean) >= d_period:
        d_smoothed = _sma_series(k_clean, d_period)
        first_k = next(i for i, v in enumerate(k) if v is not None)
        for j, v in enumerate(d_smoothed):
            d[first_k + j] = v
    return k, d


def _obv(closes: list[float], volumes: list[float]) -> list[float | None]:
    """On-balance volume. Cumulative; first bar = 0."""
    n = len(closes)
    if n == 0:
        return []
    out: list[float | None] = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return out


# --------------------------- helpers for output ---------------------------

def _round(v: float | None, digits: int = 6) -> float | None:
    if v is None:
        return None
    if not math.isfinite(v):
        return None
    return round(v, digits)


def _last(series: list[float | None]) -> float | None:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _maybe_series(
    series: list[float | None],
    include: bool,
    digits: int = 6,
    tail: int | None = None,
) -> list[float | None] | None:
    if not include:
        return None
    if tail is not None and tail > 0 and len(series) > tail:
        series = series[-tail:]
    return [_round(v, digits) for v in series]


# --------------------------- the MCP tool ---------------------------

@mcp.tool()
async def compute_indicators(
    ohlcv: list[list[float]],
    indicators: list[str] = ["rsi", "macd", "bollinger", "ema", "sma", "atr"],
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_stddev: float = 2.0,
    ema_periods: list[int] = [12, 26, 50, 200],
    sma_periods: list[int] = [20, 50, 200],
    atr_period: int = 14,
    stoch_k_period: int = 14,
    stoch_d_period: int = 3,
    adx_period: int = 14,
    include_series: bool = False,
) -> dict:
    """Compute technical indicators on OHLCV candles you have already fetched.

    USE THIS WHEN: you have OHLCV data (from `get_exchange_ohlcv` for one
    specific exchange, or `get_aggregated_ohlc` for a CoinGecko cross-venue
    aggregate) and you want RSI / MACD / Bollinger / EMA / SMA / ATR / ADX /
    Stochastic / OBV without writing the math yourself.

    THIS TOOL DOES NOT FETCH DATA. The caller must provide candles. If you
    need candles first, call `get_exchange_ohlcv` (CCXT, per-venue, supports
    1m candles) or `get_aggregated_ohlc` (CoinGecko, market-aggregate, daily/
    hourly).

    THIS TOOL RETURNS OBSERVATIONS, NOT TRADING ADVICE. The `signal_summary`
    field describes what the indicators currently show (e.g. "RSI 72 —
    overbought"). It never recommends buying or selling.

    Input format:
        ohlcv: list of rows. Either
            6 columns: [timestamp_ms, open, high, low, close, volume]   (CCXT)
            5 columns: [timestamp_ms, open, high, low, close]           (CoinGecko aggregated_ohlc — no volume)
        Rows must be ordered oldest -> newest. With 5-column input, OBV is
        unavailable (returned as null with a `note`).

    Args:
        ohlcv: Candles, oldest first. 5 or 6 columns per row.
        indicators: Which indicators to compute. Any subset of
            ["rsi", "macd", "bollinger", "ema", "sma", "atr", "adx",
             "stochastic", "obv"]. Default omits adx/stochastic/obv to keep
            output compact; pass them explicitly to opt in.
        rsi_period: Lookback for Wilder's RSI. Default 14.
        macd_fast / macd_slow / macd_signal: MACD EMA periods. Defaults 12/26/9.
        bb_period / bb_stddev: Bollinger Bands lookback and stddev multiplier.
            Defaults 20 and 2.0.
        ema_periods: List of EMA lookbacks to compute. Default [12,26,50,200].
        sma_periods: List of SMA lookbacks to compute. Default [20,50,200].
        atr_period: Wilder ATR lookback. Default 14.
        stoch_k_period / stoch_d_period: Stochastic %K and %D periods.
            Defaults 14 and 3.
        adx_period: Wilder ADX lookback. Default 14.
        include_series: If True, return the full per-bar series for every
            indicator (suitable for charting). If False (default), return
            only the latest value per indicator — much smaller payload.
            When True and the input has more than `MAX_SERIES_RETURN` (1000)
            rows, each returned series is truncated to the last
            `MAX_SERIES_RETURN` entries and `truncated_series_to` is set on
            the response so the caller can tell.

    Bounds:
        The input is rejected with an error if it has more than
        `MAX_OHLCV_ROWS` (5000) rows — pass the most recent N bars or split
        into chunks. This guards against pathological / prompt-injected
        inputs whose ADX/Stochastic passes and JSON serialization would
        otherwise dominate runtime and memory.

    Returns:
        Dict with one key per requested indicator plus:
          - `meta`: { bar_count, has_volume, last_timestamp_ms, last_close }
          - `signal_summary`: human-readable interpretation per indicator
            (observations only — overbought / oversold / trend direction etc.)
        Per-indicator shape:
          - rsi:        { latest, period, series? }
          - macd:       { latest: { macd, signal, histogram }, params, series? }
          - bollinger:  { latest: { upper, middle, lower }, period, stddev,
                          percent_b, bandwidth, series? }
          - ema:        { latest: { "12": ..., "26": ... }, periods, series? }
          - sma:        { latest: { "20": ..., "50": ... }, periods, series? }
          - atr:        { latest, period, series? }
          - adx:        { latest: { adx, plus_di, minus_di }, period, series? }
          - stochastic: { latest: { k, d }, k_period, d_period, series? }
          - obv:        { latest, series? }   # null + note if no volume column
    """
    if not isinstance(ohlcv, list):
        return {"error": "ohlcv must be a list of [ts,o,h,l,c[,v]] rows"}
    n_rows = len(ohlcv)
    if n_rows == 0:
        return {"error": "ohlcv is empty"}
    if n_rows > MAX_OHLCV_ROWS:
        return {
            "error": (
                f"ohlcv exceeds MAX_OHLCV_ROWS={MAX_OHLCV_ROWS}; received {n_rows} rows. "
                "Truncate the input (most recent N bars) or split into chunks."
            ),
            "max_rows": MAX_OHLCV_ROWS,
        }

    # ----- parse rows -----
    width = len(ohlcv[0])
    if width not in (5, 6):
        return {
            "error": f"each row must have 5 or 6 columns, got {width}",
            "hint": "expected [ts, open, high, low, close] or [ts, open, high, low, close, volume]",
        }
    has_volume = width == 6
    try:
        timestamps = [int(row[0]) for row in ohlcv]
        opens = [float(row[1]) for row in ohlcv]
        highs = [float(row[2]) for row in ohlcv]
        lows = [float(row[3]) for row in ohlcv]
        closes = [float(row[4]) for row in ohlcv]
        volumes = [float(row[5]) for row in ohlcv] if has_volume else []
    except (ValueError, TypeError, IndexError) as e:
        return {"error": f"failed to parse ohlcv rows: {e}"}

    n = len(closes)
    requested = {s.lower().strip() for s in indicators}
    # When include_series is True, cap each returned series to the last
    # MAX_SERIES_RETURN entries to keep the JSON response reasonable.
    series_tail: int | None = (
        MAX_SERIES_RETURN if include_series and n > MAX_SERIES_RETURN else None
    )
    out: dict[str, Any] = {
        "meta": {
            "bar_count": n,
            "has_volume": has_volume,
            "last_timestamp_ms": timestamps[-1] if timestamps else None,
            "last_close": _round(closes[-1]) if closes else None,
        },
    }
    if series_tail is not None:
        out["truncated_series_to"] = series_tail
    summary: dict[str, str] = {}

    # ----- RSI -----
    if "rsi" in requested:
        rsi_series = _rsi(closes, rsi_period)
        latest = _last(rsi_series)
        out["rsi"] = {
            "latest": _round(latest, 4),
            "period": rsi_period,
            "series": _maybe_series(rsi_series, include_series, 4, series_tail),
        }
        if latest is None:
            summary["rsi"] = "insufficient data"
        elif latest >= 70:
            summary["rsi"] = f"{latest:.2f} — overbought (>70)"
        elif latest <= 30:
            summary["rsi"] = f"{latest:.2f} — oversold (<30)"
        else:
            summary["rsi"] = f"{latest:.2f} — neutral (30-70)"

    # ----- MACD -----
    if "macd" in requested:
        macd_line, signal_line, hist = _macd(closes, macd_fast, macd_slow, macd_signal)
        l_macd, l_sig, l_hist = _last(macd_line), _last(signal_line), _last(hist)
        out["macd"] = {
            "latest": {
                "macd": _round(l_macd, 6),
                "signal": _round(l_sig, 6),
                "histogram": _round(l_hist, 6),
            },
            "params": {"fast": macd_fast, "slow": macd_slow, "signal": macd_signal},
            "series": (
                {
                    "macd": _maybe_series(macd_line, True, 6, series_tail),
                    "signal": _maybe_series(signal_line, True, 6, series_tail),
                    "histogram": _maybe_series(hist, True, 6, series_tail),
                }
                if include_series
                else None
            ),
        }
        if l_macd is None or l_sig is None or l_hist is None:
            summary["macd"] = "insufficient data"
        else:
            # Use a tiny tolerance so float noise around zero doesn't flip sides.
            tol = 1e-9 * max(abs(l_macd), abs(l_sig), 1.0)
            if l_macd > l_sig + tol:
                direction, side = "bullish", "above"
            elif l_macd < l_sig - tol:
                direction, side = "bearish", "below"
            else:
                direction, side = "neutral", "at"
            summary["macd"] = (
                f"{direction} — line {side} signal, histogram {l_hist:+.4f}"
            )

    # ----- Bollinger -----
    if "bollinger" in requested:
        upper, middle, lower = _bollinger(closes, bb_period, bb_stddev)
        l_up, l_mid, l_lo = _last(upper), _last(middle), _last(lower)
        last_close = closes[-1]
        percent_b = None
        bandwidth = None
        if l_up is not None and l_lo is not None and l_mid is not None:
            rng = l_up - l_lo
            if rng > 0:
                percent_b = (last_close - l_lo) / rng
            if l_mid != 0:
                bandwidth = (l_up - l_lo) / l_mid
        out["bollinger"] = {
            "latest": {
                "upper": _round(l_up, 6),
                "middle": _round(l_mid, 6),
                "lower": _round(l_lo, 6),
            },
            "period": bb_period,
            "stddev": bb_stddev,
            "percent_b": _round(percent_b, 4),
            "bandwidth": _round(bandwidth, 6),
            "series": (
                {
                    "upper": _maybe_series(upper, True, 6, series_tail),
                    "middle": _maybe_series(middle, True, 6, series_tail),
                    "lower": _maybe_series(lower, True, 6, series_tail),
                }
                if include_series
                else None
            ),
        }
        if l_up is None or l_lo is None:
            summary["bollinger"] = "insufficient data"
        elif last_close >= l_up:
            summary["bollinger"] = f"price {last_close:.6g} at/above upper band ({l_up:.6g})"
        elif last_close <= l_lo:
            summary["bollinger"] = f"price {last_close:.6g} at/below lower band ({l_lo:.6g})"
        else:
            summary["bollinger"] = (
                f"price {last_close:.6g} inside bands [{l_lo:.6g}, {l_up:.6g}]"
            )

    # ----- EMA -----
    ema_latest_map: dict[str, float | None] = {}
    if "ema" in requested:
        ema_series_map: dict[str, list[float | None] | None] = {}
        for p in ema_periods:
            s = _ema_series(closes, p)
            ema_latest_map[str(p)] = _round(_last(s), 6)
            ema_series_map[str(p)] = _maybe_series(s, include_series, 6, series_tail)
        out["ema"] = {
            "latest": ema_latest_map,
            "periods": list(ema_periods),
            "series": ema_series_map if include_series else None,
        }

    # ----- SMA -----
    if "sma" in requested:
        sma_latest: dict[str, float | None] = {}
        sma_series_map: dict[str, list[float | None] | None] = {}
        for p in sma_periods:
            s = _sma_series(closes, p)
            sma_latest[str(p)] = _round(_last(s), 6)
            sma_series_map[str(p)] = _maybe_series(s, include_series, 6, series_tail)
        out["sma"] = {
            "latest": sma_latest,
            "periods": list(sma_periods),
            "series": sma_series_map if include_series else None,
        }

    # ----- ATR -----
    if "atr" in requested:
        atr_series = _atr(highs, lows, closes, atr_period)
        latest_atr = _last(atr_series)
        out["atr"] = {
            "latest": _round(latest_atr, 6),
            "period": atr_period,
            "series": _maybe_series(atr_series, include_series, 6, series_tail),
        }
        if latest_atr is not None and closes[-1] != 0:
            pct = 100.0 * latest_atr / closes[-1]
            summary["atr"] = f"{latest_atr:.6g} ({pct:.2f}% of last close)"
        elif latest_atr is None:
            summary["atr"] = "insufficient data"

    # ----- ADX -----
    if "adx" in requested:
        plus_di, minus_di, adx_series = _adx(highs, lows, closes, adx_period)
        l_pdi, l_mdi, l_adx = _last(plus_di), _last(minus_di), _last(adx_series)
        out["adx"] = {
            "latest": {
                "adx": _round(l_adx, 4),
                "plus_di": _round(l_pdi, 4),
                "minus_di": _round(l_mdi, 4),
            },
            "period": adx_period,
            "series": (
                {
                    "adx": _maybe_series(adx_series, True, 4, series_tail),
                    "plus_di": _maybe_series(plus_di, True, 4, series_tail),
                    "minus_di": _maybe_series(minus_di, True, 4, series_tail),
                }
                if include_series
                else None
            ),
        }
        if l_adx is None:
            summary["adx"] = "insufficient data"
        else:
            strength = (
                "strong trend" if l_adx >= 25
                else "weak/no trend" if l_adx < 20
                else "developing trend"
            )
            dir_txt = ""
            if l_pdi is not None and l_mdi is not None:
                dir_txt = " (+DI > -DI, bullish)" if l_pdi > l_mdi else " (-DI > +DI, bearish)"
            summary["adx"] = f"{l_adx:.2f} — {strength}{dir_txt}"

    # ----- Stochastic -----
    if "stochastic" in requested:
        k_series, d_series = _stochastic(highs, lows, closes, stoch_k_period, stoch_d_period)
        l_k, l_d = _last(k_series), _last(d_series)
        out["stochastic"] = {
            "latest": {"k": _round(l_k, 4), "d": _round(l_d, 4)},
            "k_period": stoch_k_period,
            "d_period": stoch_d_period,
            "series": (
                {
                    "k": _maybe_series(k_series, True, 4, series_tail),
                    "d": _maybe_series(d_series, True, 4, series_tail),
                }
                if include_series
                else None
            ),
        }
        if l_k is None:
            summary["stochastic"] = "insufficient data"
        elif l_k >= 80:
            summary["stochastic"] = f"%K {l_k:.2f} — overbought (>=80)"
        elif l_k <= 20:
            summary["stochastic"] = f"%K {l_k:.2f} — oversold (<=20)"
        else:
            summary["stochastic"] = f"%K {l_k:.2f} — neutral"

    # ----- OBV -----
    if "obv" in requested:
        if not has_volume:
            out["obv"] = {
                "latest": None,
                "series": None,
                "note": (
                    "OBV requires a volume column; the input has 5 columns "
                    "(no volume — likely from get_aggregated_ohlc). Use "
                    "get_exchange_ohlcv for volume-bearing candles."
                ),
            }
            summary["obv"] = "unavailable (no volume column)"
        else:
            obv_series = _obv(closes, volumes)
            latest_obv = obv_series[-1] if obv_series else None
            out["obv"] = {
                "latest": _round(latest_obv, 4),
                "series": _maybe_series(obv_series, include_series, 4, series_tail),
            }

    # ----- Trend summary (uses EMAs if available) -----
    e50 = ema_latest_map.get("50") if ema_latest_map else None
    e200 = ema_latest_map.get("200") if ema_latest_map else None
    last_close = closes[-1]
    if e50 is not None and e200 is not None:
        if last_close > e50 > e200:
            summary["trend"] = "uptrend (price > EMA50 > EMA200)"
        elif last_close < e50 < e200:
            summary["trend"] = "downtrend (price < EMA50 < EMA200)"
        else:
            summary["trend"] = (
                f"mixed (close={last_close:.6g}, EMA50={e50:.6g}, EMA200={e200:.6g})"
            )

    out["signal_summary"] = summary
    out["disclaimer"] = (
        "These are observations on the supplied candles, not trading advice."
    )
    return out
