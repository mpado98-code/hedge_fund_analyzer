"""
Regime classifier: bull/bear/range × low/med/high vol, per US e EU separatamente.
"""
from __future__ import annotations
import yfinance as yf
import numpy as np
import pandas as pd
from utils import get_logger, save_json, DATA_DIR

log = get_logger("regime")

# Proxy per region
PROXIES = {
    "US": {"index": "SPY", "vol": "^VIX", "breadth": "RSP", "defensive": "XLP", "cyclical": "XLY"},
    "EU": {"index": "EXSA.DE", "vol": "^V2TX", "breadth": None, "defensive": "EXH7.DE", "cyclical": "EXH3.DE"},
}


def _session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception:
        return None


def _safe_history(ticker: str, period="2y"):
    try:
        sess = _session()
        df = yf.Ticker(ticker, session=sess).history(period=period, auto_adjust=True)
        return df if not df.empty else None
    except Exception as e:
        log.warning(f"history fail {ticker}: {e}")
        return None


def classify_region(region: str) -> dict:
    p = PROXIES[region]
    idx = _safe_history(p["index"])
    vix = _safe_history(p["vol"], period="6mo") if p["vol"] else None
    breadth_df = _safe_history(p["breadth"]) if p["breadth"] else None

    if idx is None or len(idx) < 250:
        return {"regime": "unknown", "reason": "no data"}

    close = idx["Close"]
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    last = close.iloc[-1]
    vs_ma200 = (last / ma200.iloc[-1] - 1) * 100

    # Trend
    if vs_ma200 > 3 and ma50.iloc[-1] > ma200.iloc[-1]:
        trend = "bull"
    elif vs_ma200 < -3 and ma50.iloc[-1] < ma200.iloc[-1]:
        trend = "bear"
    else:
        trend = "range"

    # Vol regime
    vol_level = "medium"
    vix_val = None
    if vix is not None and not vix.empty:
        vix_val = float(vix["Close"].iloc[-1])
        if vix_val < 14:
            vol_level = "low"
        elif vix_val > 22:
            vol_level = "high"
        else:
            vol_level = "medium"
    else:
        # fallback: realized vol 30g
        ret = close.pct_change().dropna()
        rv = ret.tail(30).std() * np.sqrt(252) * 100
        if rv < 12:
            vol_level = "low"
        elif rv > 22:
            vol_level = "high"

    # Breadth (US only)
    breadth_signal = None
    if breadth_df is not None and len(breadth_df) > 50:
        rsp_ret = breadth_df["Close"].iloc[-1] / breadth_df["Close"].iloc[-90] - 1
        spy_ret = close.iloc[-1] / close.iloc[-90] - 1
        breadth_signal = "broad" if rsp_ret > spy_ret else "narrow"

    # Risk on/off via defensive vs cyclical
    risk = None
    try:
        defens = _safe_history(p["defensive"])
        cycl = _safe_history(p["cyclical"])
        if defens is not None and cycl is not None:
            d_ret = defens["Close"].iloc[-1] / defens["Close"].iloc[-60] - 1
            c_ret = cycl["Close"].iloc[-1] / cycl["Close"].iloc[-60] - 1
            risk = "on" if c_ret > d_ret else "off"
    except Exception:
        risk = None

    regime = f"{trend}-{'quiet' if vol_level == 'low' else 'volatile' if vol_level == 'high' else 'normal'}"

    return {
        "region": region,
        "regime": regime,
        "trend": trend,
        "vol_level": vol_level,
        "vix": vix_val,
        "spy_vs_ma200_pct": round(vs_ma200, 2),
        "breadth": breadth_signal,
        "risk": risk,
    }


def classify_all() -> dict:
    out = {r: classify_region(r) for r in PROXIES}
    save_json(DATA_DIR / "regime.json", out)
    log.info(f"Regime: {out}")
    return out


if __name__ == "__main__":
    classify_all()
