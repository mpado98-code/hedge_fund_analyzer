"""
Score SHORT-TERM (orizzonte ~1 mese). 0-100.

Pillar:
- Technical/Momentum 35%
- Flow & Sentiment   25%
- Earnings Catalyst  20%
- Regime Fit         10%
- Relative Strength  10%
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime, date
from utils import clip, get_logger

log = get_logger("score_short")


def technical_subscore(prices: pd.DataFrame) -> tuple[float, dict]:
    if prices is None or len(prices) < 200:
        return 50.0, {"reason": "no data"}
    close = prices["Close"]
    vol = prices["Volume"] if "Volume" in prices else None
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    last = close.iloc[-1]

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs.iloc[-1])
    rsi = float(rsi if not np.isnan(rsi) else 50)

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9).mean()
    macd_diff = float(macd.iloc[-1] - sig.iloc[-1])
    macd_z = np.tanh(macd_diff / (close.iloc[-1] * 0.01))  # normalizzato

    # Volume confirmation
    vol_z = 0.0
    if vol is not None and len(vol) > 20:
        vavg = vol.tail(20).mean()
        vol_z = (vol.iloc[-1] - vavg) / (vol.tail(20).std() + 1e-9)
        vol_z = float(np.clip(vol_z, -2, 2))

    score = 50.0
    # trend
    if last > ma50 > ma200:
        score += 12
    elif last < ma50 < ma200:
        score -= 12
    # vs ma200
    vs_ma200 = (last / ma200 - 1) * 100
    score += np.clip(vs_ma200, -15, 15)
    # rsi: optimal zone 50-70 for momentum buy
    if 55 <= rsi <= 70:
        score += 6
    elif rsi > 80:
        score -= 6  # overbought
    elif rsi < 30:
        score += 3  # oversold bounce
    # macd
    score += macd_z * 6
    # volume confirm
    score += vol_z * 2
    return clip(score), {
        "ma20_vs_ma50": round((ma20 / ma50 - 1) * 100, 2),
        "vs_ma200_pct": round(vs_ma200, 2),
        "rsi14": round(rsi, 1),
        "macd_diff": round(macd_diff, 3),
        "vol_z": round(vol_z, 2),
    }


def sentiment_subscore(sent: dict) -> tuple[float, dict]:
    if not sent:
        return 50.0, {}
    s = sent.get("score", 0)  # -1..+1
    buzz = sent.get("buzz", 0)  # 0..1
    score = 50 + s * 40 + (buzz - 0.5) * 10
    return clip(score), {"net_sent": s, "buzz": buzz, "n": sent.get("n_articles", 0)}


def catalyst_subscore(fund: dict) -> tuple[float, dict]:
    """Earnings catalyst: vicino a earnings = upside potential + risk."""
    e = fund.get("earnings_next") or {}
    if not e:
        return 50.0, {"days_to_earnings": None}
    try:
        dt = datetime.fromisoformat(e["date"]).date()
        days = (dt - date.today()).days
    except Exception:
        return 50.0, {}
    eps_est = e.get("epsEstimated") or 0
    # 0-5 giorni: alta volatilità, score moderato
    # 6-14 giorni: zona "pre-earnings drift" - storicamente positivo
    # 15-30 giorni: neutro
    # >30 giorni: catalyst lontano
    if 6 <= days <= 14:
        score = 75
    elif 0 <= days <= 5:
        score = 65
    elif 15 <= days <= 30:
        score = 55
    else:
        score = 45
    return clip(score), {"days_to_earnings": days, "eps_est": eps_est}


def regime_fit_subscore(factors: dict, regime: dict) -> tuple[float, dict]:
    """In bull-quiet/normal premia momentum+quality, in range mean-reversion,
    in bear premia lowvol+quality."""
    if not factors or not regime:
        return 50.0, {}
    rg = regime.get("regime", "")
    z_mom = factors.get("z_momentum", 0)
    z_q = factors.get("z_quality", 0)
    z_lv = factors.get("z_lowvol", 0)
    z_val = factors.get("z_value", 0)
    if rg.startswith("bull"):
        signal = 0.6 * z_mom + 0.3 * z_q + 0.1 * z_lv
    elif rg.startswith("range"):
        signal = 0.4 * z_val + 0.3 * z_q + 0.3 * z_lv
    else:  # bear
        signal = 0.5 * z_lv + 0.4 * z_q - 0.1 * z_mom
    score = 50 + np.tanh(signal) * 25
    return clip(score), {"regime": rg, "composite_z": round(float(signal), 3)}


def relative_strength_subscore(prices_t: pd.DataFrame, prices_bench: pd.DataFrame) -> tuple[float, dict]:
    if prices_t is None or prices_bench is None or len(prices_t) < 90 or len(prices_bench) < 90:
        return 50.0, {}
    rt = prices_t["Close"].iloc[-1] / prices_t["Close"].iloc[-90] - 1
    rb = prices_bench["Close"].iloc[-1] / prices_bench["Close"].iloc[-90] - 1
    diff = (rt - rb) * 100  # punti %
    score = 50 + np.clip(diff, -25, 25)
    return clip(score), {"perf_90d_pct": round(rt * 100, 2), "bench_90d_pct": round(rb * 100, 2), "alpha_pct": round(diff, 2)}


def compute(
    ticker: str,
    prices_ticker: pd.DataFrame,
    prices_bench: pd.DataFrame,
    sent: dict,
    fund: dict,
    factors: dict,
    regime: dict,
) -> dict:
    s_tech, b_tech = technical_subscore(prices_ticker)
    s_sent, b_sent = sentiment_subscore(sent)
    s_cat, b_cat = catalyst_subscore(fund)
    s_reg, b_reg = regime_fit_subscore(factors, regime)
    s_rs, b_rs = relative_strength_subscore(prices_ticker, prices_bench)

    total = (
        0.35 * s_tech +
        0.25 * s_sent +
        0.20 * s_cat +
        0.10 * s_reg +
        0.10 * s_rs
    )
    return {
        "ticker": ticker,
        "score_short": round(total, 1),
        "pillars": {
            "technical": {"score": round(s_tech, 1), "weight": 0.35, "breakdown": b_tech},
            "sentiment": {"score": round(s_sent, 1), "weight": 0.25, "breakdown": b_sent},
            "catalyst":  {"score": round(s_cat, 1),  "weight": 0.20, "breakdown": b_cat},
            "regime":    {"score": round(s_reg, 1),  "weight": 0.10, "breakdown": b_reg},
            "rel_strength": {"score": round(s_rs, 1), "weight": 0.10, "breakdown": b_rs},
        },
    }
