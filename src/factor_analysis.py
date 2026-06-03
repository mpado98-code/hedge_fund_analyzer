"""
Factor analysis cross-sectional sull'universo: value, momentum, quality, low-vol.
Calcola Z-score per ogni ticker rispetto al peer set (stesso settore se possibile).
Output: data/factors.json { ticker: { z_value, z_momentum, z_quality, z_lowvol } }
"""
import numpy as np
import pandas as pd
from utils import get_logger, save_json, load_json, DATA_DIR

log = get_logger("factors")


def winsorize(s: pd.Series, p: float = 0.02) -> pd.Series:
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lower=lo, upper=hi)


def zscore(s: pd.Series) -> pd.Series:
    s = winsorize(s.dropna())
    if s.std() == 0 or len(s) < 5:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / s.std()


def build_factors(enriched: dict, prices: dict) -> dict:
    """
    enriched: { ticker: full_fundamentals dict }
    prices:   { ticker: pd.DataFrame con history giornaliera close }
    """
    rows = []
    for tk, d in enriched.items():
        ratios = d.get("ratios", {}) or {}
        km = d.get("key_metrics", {}) or {}
        q = d.get("quality", {}) or {}
        g = d.get("growth", {}) or {}
        rows.append({
            "ticker": tk,
            "sector": (d.get("profile") or {}).get("sector", "Unknown"),
            "pe": ratios.get("peRatioTTM"),
            "ev_ebitda": ratios.get("enterpriseValueOverEBITDATTM"),
            "ps": ratios.get("priceToSalesRatioTTM"),
            "fcf_yield": (km.get("freeCashFlowYieldTTM") or 0) * 100 if km.get("freeCashFlowYieldTTM") else None,
            "roic": q.get("roic_pct"),
            "roe": q.get("roe_pct"),
            "gm": q.get("gross_margin_pct"),
            "rev_cagr": g.get("rev_cagr_pct"),
            "eps_cagr": g.get("eps_cagr_pct"),
        })
    if not rows:
        log.warning("build_factors: enriched vuoto, skip")
        save_json(DATA_DIR / "factors.json", {})
        return {}
    df = pd.DataFrame(rows).set_index("ticker")

    # Momentum (6m / 1m) and lowvol da prezzi
    mom_6m, mom_1m, vol_30d = {}, {}, {}
    for tk, p in prices.items():
        if p is None or len(p) < 130:
            continue
        close = p["Close"]
        try:
            mom_6m[tk] = close.iloc[-1] / close.iloc[-126] - 1
        except Exception:
            pass
        try:
            mom_1m[tk] = close.iloc[-1] / close.iloc[-21] - 1
        except Exception:
            pass
        try:
            ret = close.pct_change().dropna()
            vol_30d[tk] = ret.tail(30).std() * np.sqrt(252)
        except Exception:
            pass
    df["mom_6m"] = pd.Series(mom_6m)
    df["mom_1m"] = pd.Series(mom_1m)
    df["vol_30d"] = pd.Series(vol_30d)

    # Z-score per settore (group neutral)
    factor_z = {}
    for sector, sub in df.groupby("sector"):
        if len(sub) < 5:
            # fallback su tutto l'universo
            sub = df
        # Value: inverso di P/E + EV/EBITDA + P/S, più alto = più cheap
        sub_v = -(zscore(sub["pe"].astype(float)) +
                  zscore(sub["ev_ebitda"].astype(float)) +
                  zscore(sub["ps"].astype(float))) / 3
        # Momentum: 6m peso 0.7, 1m peso 0.3 (con segno positivo per up)
        sub_m = 0.7 * zscore(sub["mom_6m"].astype(float)) + 0.3 * zscore(sub["mom_1m"].astype(float))
        # Quality: roic + roe + gm
        sub_q = (zscore(sub["roic"].astype(float)) +
                 zscore(sub["roe"].astype(float)) +
                 zscore(sub["gm"].astype(float))) / 3
        # LowVol: inverso vol
        sub_l = -zscore(sub["vol_30d"].astype(float))
        for tk in sub.index:
            factor_z[tk] = {
                "z_value":    round(float(sub_v.get(tk, 0) or 0), 3),
                "z_momentum": round(float(sub_m.get(tk, 0) or 0), 3),
                "z_quality":  round(float(sub_q.get(tk, 0) or 0), 3),
                "z_lowvol":   round(float(sub_l.get(tk, 0) or 0), 3),
                "sector": sector,
