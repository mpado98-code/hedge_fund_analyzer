"""
Orchestrator settimanale. Pipeline end-to-end:
1. Build universe
2. Regime + macro
3. Pre-screen short (top 300) + pre-screen long (top 300) usando solo prezzi+fattori veloci
4. Union -> watchlist (~400 unique)
5. Enrich (FMP fundamentals + DCF + peers) - rate-limited
6. Sentiment
7. Factors
8. Score short + long
9. LLM report
10. Cache (data/watchlist.json + data/reports/*.json)
11. Post top 10 short + top 10 long su Telegram

Run: python -m src.weekly_run
"""
import sys
import time
import json
import requests
import yfinance as yf
import pandas as pd
from pathlib import Path
from utils import env, get_logger, save_json, load_json, DATA_DIR, REPORTS_DIR, now_iso

import universe as universe_mod
import regime as regime_mod
import macro as macro_mod
import fundamentals as fund_mod
import sentiment as sent_mod
import factor_analysis as fa
import score_short
import score_long
import report_generator

log = get_logger("weekly")

# Tunable
PRE_SCREEN_SHORT = 250
PRE_SCREEN_LONG = 250
MAX_FMP_CALLS_PER_TICKER = 10  # profile, ratios, key_metrics, income, cashflow, dcf, earnings, insider, peers + 1 peer_medians (avg 6 inside)
DAILY_FMP_BUDGET = 240  # safety margin sotto 250


def _yf_session():
    """Sessione curl_cffi che impersona Chrome — bypassa il blocco Yahoo che fa fallire JSONDecodeError."""
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception:
        return None


def download_prices(tickers: list[str], period: str = "1y") -> dict:
    log.info(f"Downloading prices for {len(tickers)} tickers...")
    data = {}
    sess = _yf_session()
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        attempt = 0
        while attempt < 3:
            try:
                df = yf.download(
                    batch, period=period, progress=False, auto_adjust=True,
                    group_by="ticker", threads=True, session=sess,
                )
                for t in batch:
                    try:
                        sub = df[t] if len(batch) > 1 else df
                        if not sub.empty and "Close" in sub.columns:
                            data[t] = sub.dropna()
                    except Exception:
                        continue
                break
            except Exception as e:
                attempt += 1
                log.warning(f"bulk dl batch {i} attempt {attempt}: {e}")
                time.sleep(3 * attempt)
    log.info(f"  -> ok per {len(data)} su {len(tickers)}")
    return data


def pre_screen(universe: dict, prices: dict) -> tuple[list[str], list[str]]:
    """Light pre-screen senza FMP, solo prezzi+volume per restringere a top ~400."""
    rows = []
    for tk, info in universe.items():
        p = prices.get(tk)
        if p is None or len(p) < 200:
            continue
        close = p["Close"]
        try:
            mom6 = float(close.iloc[-1] / close.iloc[-126] - 1)
            mom1 = float(close.iloc[-1] / close.iloc[-21] - 1)
            vol = float(close.pct_change().tail(60).std())
            avg_dv = float((p["Close"] * p["Volume"]).tail(20).mean()) if "Volume" in p else 0
            rows.append({
                "ticker": tk,
                "mom6": mom6,
                "mom1": mom1,
                "vol": vol,
                "dollar_vol": avg_dv,
            })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return [], []

    # Liquidity filter (escludi penny / illiquid)
    df = df[df["dollar_vol"] > 5e6]

    # Short rank: momentum 6m + 1m, penalizza vol estrema
    df["short_rank"] = df["mom6"] * 0.6 + df["mom1"] * 0.4 - df["vol"] * 2
    short_picks = df.nlargest(PRE_SCREEN_SHORT, "short_rank")["ticker"].tolist()

    # Long rank: momentum smoothed (consistent trends) + low vol
    df["long_rank"] = df["mom6"] * 0.7 - df["vol"] * 4
    # filtra solo positive 6m
    df_long = df[df["mom6"] > -0.1]
    long_picks = df_long.nlargest(PRE_SCREEN_LONG, "long_rank")["ticker"].tolist()

    return short_picks, long_picks


def enrich_watchlist(tickers: list[str]) -> dict:
    """Chiama FMP per i fondamentali rispettando budget."""
    enriched = {}
    calls = 0
    for i, tk in enumerate(tickers):
        if calls + MAX_FMP_CALLS_PER_TICKER > DAILY_FMP_BUDGET:
            log.warning(f"FMP budget esaurito a {tk} (idx {i}). Salto i restanti.")
            break
        try:
            enriched[tk] = fund_mod.full_fundamentals(tk)
            calls += MAX_FMP_CALLS_PER_TICKER
        except Exception as e:
            log.warning(f"enrich {tk}: {e}")
        if i % 20 == 0:
            log.info(f"  enrich {i}/{len(tickers)}  (calls ~{calls})")
            time.sleep(1)
    return enriched


def telegram_send(text: str, parse_mode: str = "Markdown") -> None:
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    chat = env("TELEGRAM_CHAT_ID", required=True)
    # split lungo
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]
    for c in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": c, "parse_mode": parse_mode, "disable_web_page_preview": True},
                timeout=15,
            )
        except Exception as e:
            log.error(f"telegram send: {e}")


def format_ranking(picks: list[dict], horizon: str, regime: dict) -> str:
    title = "🎯 TOP 10 SHORT-TERM (1 mese)" if horizon == "short" else "🏔️ TOP 10 LONG-TERM (3-10 anni)"
    lines = [
        f"*{title}*",
        f"_{now_iso()[:10]} | Regime US: {regime.get('US', {}).get('regime', '?')} | EU: {regime.get('EU', {}).get('regime', '?')}_",
        "",
    ]
    for i, p in enumerate(picks, 1):
        flags = ""
        if p.get("red_flags"):
            flags = f"  ⚠️ {len(p['red_flags'])} flag"
        lines.append(f"{i}. *{p['ticker']}*  {p['score']:.0f}/100  — {p['headline']}{flags}")
    lines.append("")
    lines.append("_Manda /a TICKER per il report completo._")
    return "\n".join(lines)


def build_headline(report: dict, horizon: str) -> str:
    """Una frase di sintesi per il ranking."""
    n = report.get("narrative", {}) or {}
    if horizon == "short":
        return (n.get("thesis_short", "") or "").split(".")[0][:80] or "momentum + setup"
    return (n.get("thesis_long", "") or "").split(".")[0][:80] or "quality + valuation"


def main():
    log.info("=== WEEKLY RUN START ===")

    # 1. Universe
    universe = universe_mod.build_universe()
    tickers = list(universe.keys())

    # 2. Regime + Macro
    regime = regime_mod.classify_all()
    macro = macro_mod.snapshot()

    # 3. Bench prices for relative strength
    bench = {
        "US": yf.Ticker("SPY").history(period="1y", auto_adjust=True),
        "EU": yf.Ticker("EXSA.DE").history(period="1y", auto_adjust=True),
    }

    # 4. Universe prices
    prices = download_prices(tickers, period="1y")
    if len(prices) < 50:
        msg = f"⚠️ Weekly run abort: yfinance ha scaricato solo {len(prices)}/{len(tickers)} prezzi. Probabile rate-limit Yahoo. Riprova tra qualche ora."
        log.error(msg)
        telegram_send(msg, parse_mode=None)
        return

    # 5. Pre-screen
    short_picks, long_picks = pre_screen(universe, prices)
    watchlist = list(dict.fromkeys(short_picks + long_picks))  # ordered dedup
    log.info(f"Watchlist: {len(watchlist)} (short:{len(short_picks)} long:{len(long_picks)})")

    # 6. Enrich (FMP)
    enriched = enrich_watchlist(watchlist)

    # 7. Factors
    factors = fa.build_factors(enriched, {tk: prices[tk] for tk in enriched if tk in prices})

    # 8. Sentiment + 9. Scoring + 10. Report per ticker
    results_short, results_long = [], []
    for tk in enriched:
        info = universe.get(tk, {})
        name = info.get("name") or enriched[tk].get("profile", {}).get("companyName", tk)
        sent = sent_mod.sentiment_for(tk, name)
        bench_region = bench.get(info.get("region", "US"), bench["US"])
        prices_tk = prices.get(tk)

        # score
        rg_for_t = regime.get(info.get("region", "US"), regime.get("US"))
        s_short = score_short.compute(tk, prices_tk, bench_region, sent, enriched[tk], factors.get(tk, {}), rg_for_t)
        s_long  = score_long.compute(tk, enriched[tk], factors.get(tk, {}))

        # report
        payload = {
            "score_short": s_short,
            "score_long": s_long,
            "fundamentals": enriched[tk],
            "sentiment": sent,
            "factors": factors.get(tk, {}),
            "regime": rg_for_t,
        }
        report = report_generator.make_report(tk, name, payload)
        results_short.append({
            "ticker": tk, "score": s_short["score_short"], "report": report,
            "headline": build_headline(report, "short"),
            "red_flags": s_long.get("red_flags", []),
        })
        results_long.append({
            "ticker": tk, "score": s_long["score_long"], "report": report,
            "headline": build_headline(report, "long"),
            "red_flags": s_long.get("red_flags", []),
        })

    # 11. Watchlist file (lookup veloce per bot)
    watchlist_index = {
        r["ticker"]: {
            "score_short": r["score"],
            "name": r["report"]["name"],
            "asof": r["report"]["generated_at"],
        }
        for r in results_short
    }
    for r in results_long:
        watchlist_index.setdefault(r["ticker"], {})["score_long"] = r["score"]
    save_json(DATA_DIR / "watchlist.json", {"updated": now_iso(), "tickers": watchlist_index})

    # 12. Top 10 ranking
    top_short = sorted(results_short,
