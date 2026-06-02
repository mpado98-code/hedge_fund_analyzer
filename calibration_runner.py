"""
Calibrazione: backtest 5 anni separato per score_short (forward 30g) e score_long (forward 3y).

Strategia:
- Carica universo (sottosegmento random di ~250 ticker per limitare il run a <1h)
- Per ogni mese degli ultimi 5 anni:
  - calcola score_short per i ticker su prezzi disponibili a quella data
  - calcola forward return 30g, hit-rate target +5%, stop -4%
- Per ogni anno degli ultimi 5y:
  - calcola score_long approssimato (solo da prezzi e ratios storici aggregati)
  - calcola forward return 3y annualizzato, CAGR > 8% hit-rate

Output: data/calibration_short.json + data/calibration_long.json
"""
import random
import statistics
import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from utils import get_logger, save_json, load_json, DATA_DIR
import universe as universe_mod

log = get_logger("calibration")

YEARS_BACK = 5
SAMPLE_TICKERS = 250
WEEKLY_SAMPLE = 6  # ticker per settimana

SHORT_FWD_DAYS = 30
SHORT_TARGET = 0.05
SHORT_STOP = -0.04
LONG_FWD_DAYS = 252 * 3
LONG_TARGET_CAGR = 0.08


def calibrate_short():
    """Backtest score_short su 5y. Versione semplificata: usa solo segnali tecnici + factor proxy."""
    # Build universe
    uni = universe_mod.build_universe() if not (DATA_DIR / "universe.json").exists() else load_json(DATA_DIR / "universe.json")
    tickers = list(uni.keys())
    random.seed(42)
    sample = random.sample(tickers, min(SAMPLE_TICKERS, len(tickers)))

    # Pull 6y di storia per avere margine
    log.info(f"Downloading 6y history for {len(sample)} tickers (può richiedere 5-10 min)...")
    end = datetime.utcnow()
    start = end - timedelta(days=365 * 6)
    df = yf.download(sample, start=start, end=end, progress=False, auto_adjust=True, group_by="ticker", threads=True)

    # SPY for regime classifier
    spy = yf.Ticker("SPY").history(start=start, end=end, auto_adjust=True)

    results = []  # list of {date, ticker, score, regime, fwd_ret, hit_target, hit_stop}

    # Genera date di osservazione (ogni settimana)
    dates = pd.date_range(start + pd.Timedelta(days=200), end - pd.Timedelta(days=40), freq="W-FRI")
    for d in dates:
        try:
            # Regime semplificato: SPY trend + vol
            spy_sub = spy.loc[:d]
            if len(spy_sub) < 200:
                continue
            ma200 = spy_sub["Close"].rolling(200).mean().iloc[-1]
            ma50 = spy_sub["Close"].rolling(50).mean().iloc[-1]
            last = spy_sub["Close"].iloc[-1]
            trend = "bull" if last > ma50 > ma200 else "bear" if last < ma50 < ma200 else "range"
            vol = spy_sub["Close"].pct_change().tail(30).std() * np.sqrt(252)
            voltag = "quiet" if vol < 0.14 else "volatile" if vol > 0.22 else "normal"
            regime = f"{trend}-{voltag}"
        except Exception:
            continue

        # Random subset per data per accelerare
        picks = random.sample(sample, min(WEEKLY_SAMPLE, len(sample)))
        for tk in picks:
            try:
                sub = df[tk] if len(sample) > 1 else df
                sub_at = sub.loc[:d].dropna()
                fwd = sub.loc[d:d + pd.Timedelta(days=SHORT_FWD_DAYS + 5)]["Close"]
                if len(sub_at) < 200 or len(fwd) < 15:
                    continue
                # Score tecnico semplificato (mom+rsi+vs_ma200)
                close = sub_at["Close"]
                vs200 = close.iloc[-1] / close.rolling(200).mean().iloc[-1] - 1
                mom_3m = close.iloc[-1] / close.iloc[-63] - 1
                mom_1m = close.iloc[-1] / close.iloc[-21] - 1
                delta = close.diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = -delta.clip(upper=0).rolling(14).mean()
                rs = gain / loss.replace(0, np.nan)
                rsi = 100 - 100 / (1 + rs.iloc[-1])
                if np.isnan(rsi):
                    rsi = 50

                raw = (
                    50
                    + np.clip(vs200 * 100, -15, 15)
                    + np.clip(mom_3m * 100, -10, 10) * 0.6
                    + np.clip(mom_1m * 100, -10, 10) * 0.4
                    + (6 if 55 <= rsi <= 70 else -4 if rsi > 80 else 0)
                )
                score = max(0, min(100, raw))

                # Forward returns
                entry = float(fwd.iloc[0])
                fwd_period = fwd.iloc[:SHORT_FWD_DAYS + 1]
                end_p = float(fwd_period.iloc[-1])
                fwd_ret = (end_p / entry - 1)
                hit_t = bool((fwd_period.max() / entry - 1) >= SHORT_TARGET)
                hit_s = bool((fwd_period.min() / entry - 1) <= SHORT_STOP)

                results.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "ticker": tk,
                    "score": float(score),
                    "regime": regime,
                    "fwd_ret_30d": fwd_ret,
                    "hit_target_5pct": hit_t,
                    "hit_stop_4pct": hit_s,
                })
            except Exception:
                continue

    log.info(f"Short backtest samples: {len(results)}")

    # Bucket
    buckets = {}
    for r in results:
        b = int(r["score"] // 10) * 10
        for key in (f"score_{b}_{b+10}_all", f"score_{b}_{b+10}_{r['regime']}"):
            buckets.setdefault(key, []).append(r)
    out = {}
    for k, rows in buckets.items():
        if len(rows) < 15:
            continue
        rets = [x["fwd_ret_30d"] for x in rows]
        out[k] = {
            "n_samples": len(rows),
            "p_pos_30d": round(sum(1 for r in rets if r > 0) / len(rets), 3),
            "p_target_5pct": round(sum(1 for r in rows if r["hit_target_5pct"]) / len(rows), 3),
            "p_stop_4pct": round(sum(1 for r in rows if r["hit_stop_4pct"]) / len(rows), 3),
            "avg_ret_30d_pct": round(statistics.mean(rets) * 100, 2),
            "median_ret_30d_pct": round(statistics.median(rets) * 100, 2),
        }
    save_json(DATA_DIR / "calibration_short.json", out)
    log.info(f"calibration_short: {len(out)} buckets")
    return out


def calibrate_long():
    """Backtest long: usa segnali tecnici + value proxy (mean reversion 3y returns)."""
    uni = load_json(DATA_DIR / "universe.json", default={})
    tickers = list(uni.keys())
    if not tickers:
        uni = universe_mod.build_universe()
        tickers = list(uni.keys())
    random.seed(7)
    sample = random.sample(tickers, min(200, len(tickers)))

    end = datetime.utcnow()
    start = end - timedelta(days=365 * 8)  # 8y for 5y obs + 3y fwd
    log.info(f"Downloading 8y for {len(sample)} tickers...")
    df = yf.download(sample, start=start, end=end, progress=False, auto_adjust=True, group_by="ticker", threads=True)

    results = []
    # Observation dates: ogni 6 mesi, last obs deve avere 3y fwd
    dates = pd.date_range(start + pd.Timedelta(days=400), end - pd.Timedelta(days=365 * 3 + 30), freq="6MS")
    for d in dates:
        for tk in sample:
            try:
                sub = df[tk] if len(sample) > 1 else df
                sub_at = sub.loc[:d].dropna()
                fwd = sub.loc[d:d + pd.Timedelta(days=LONG_FWD_DAYS + 30)]["Close"]
                if len(sub_at) < 300 or len(fwd) < 200:
                    continue
                close = sub_at["Close"]
                # Pseudo long score: mom 12m smooth - drawdown - vol
                mom_12m = close.iloc[-1] / close.iloc[-252] - 1
                mom_24m = close.iloc[-1] / close.iloc[-min(504, len(close) - 1)] - 1
                vol = close.pct_change().tail(126).std() * np.sqrt(252)
                # drawdown
                roll_max = close.rolling(252).max()
                dd = (close.iloc[-1] / roll_max.iloc[-1] - 1)
                raw = (
                    50 +
                    np.clip(mom_12m * 50, -15, 15) +
                    np.clip(mom_24m * 30, -10, 10) -
                    np.clip(vol * 30, 0, 12) +
                    np.clip(-dd * 30, -10, 5)  # leggera mean rev
                )
                score = max(0, min(100, raw))

                entry = float(fwd.iloc[0])
                end_p = float(fwd.iloc[min(LONG_FWD_DAYS, len(fwd) - 1)])
                ann = (end_p / entry) ** (1 / 3) - 1
                results.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "ticker": tk,
                    "score": float(score),
                    "fwd_cagr_3y": ann,
                    "fwd_ret_3y": end_p / entry - 1,
                    "hit_cagr_8pct": bool(ann >= LONG_TARGET_CAGR),
                })
            except Exception:
                continue

    log.info(f"Long backtest samples: {len(results)}")
    buckets = {}
    for r in results:
        b = int(r["score"] // 10) * 10
        buckets.setdefault(f"score_{b}_{b+10}", []).append(r)
    out = {}
    for k, rows in buckets.items():
        if len(rows) < 15:
            continue
        cagrs = [x["fwd_cagr_3y"] for x in rows]
        out[k] = {
            "n_samples": len(rows),
            "avg_cagr_3y_pct": round(statistics.mean(cagrs) * 100, 2),
            "median_cagr_3y_pct": round(statistics.median(cagrs) * 100, 2),
            "p_cagr_above_8pct": round(sum(1 for r in rows if r["hit_cagr_8pct"]) / len(rows), 3),
            "avg_ret_3y_pct": round(statistics.mean([r["fwd_ret_3y"] for r in rows]) * 100, 2),
        }
    save_json(DATA_DIR / "calibration_long.json", out)
    log.info(f"calibration_long: {len(out)} buckets")
    return out


def main():
    calibrate_short()
    calibrate_long()


if __name__ == "__main__":
    main()
