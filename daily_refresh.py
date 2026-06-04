"""
Refresh quotidiano leggero: aggiorna prezzi degli ultimi report cache,
controlla notizie urgenti, aggiorna flag su movimenti grandi (gap > 5%).
Non rifa fondamentali.
"""
from __future__ import annotations
import yfinance as yf
import requests
from utils import env, get_logger, load_json, save_json, DATA_DIR, now_iso

log = get_logger("daily")


def main():
    wl = load_json(DATA_DIR / "watchlist.json")
    if not wl:
        log.warning("Nessuna watchlist - run weekly first")
        return
    tickers = list(wl["tickers"].keys())
    log.info(f"Daily refresh: {len(tickers)} tickers")

    try:
        df = yf.download(tickers, period="5d", interval="1d", progress=False,
                         auto_adjust=True, group_by="ticker", threads=True)
    except Exception as e:
        log.error(f"yfinance dl: {e}")
        return

    alerts = []
    prices_today = {}
    for t in tickers:
        try:
            sub = df[t] if len(tickers) > 1 else df
            if sub.empty or len(sub) < 2:
                continue
            last = sub["Close"].iloc[-1]
            prev = sub["Close"].iloc[-2]
            chg = (last / prev - 1) * 100
            prices_today[t] = {
                "price": float(last),
                "prev": float(prev),
                "chg_pct": round(chg, 2),
                "asof": str(sub.index[-1].date()),
            }
            if abs(chg) >= 5:
                emo = "🟢" if chg > 0 else "🔴"
                alerts.append(f"{emo} {t}: {chg:+.1f}%  ({prev:.2f} → {last:.2f})")
        except Exception:
            continue

    save_json(DATA_DIR / "prices_today.json", {"updated": now_iso(), "prices": prices_today})

    if alerts:
        msg = "⚡ Movimenti notevoli watchlist:\n" + "\n".join(alerts[:30])
        token = env("TELEGRAM_BOT_TOKEN")
        chat = env("TELEGRAM_CHAT_ID")
        if token and chat:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": msg},
                    timeout=10,
                )
            except Exception as e:
                log.error(f"telegram: {e}")
    log.info(f"Refresh done. Alerts: {len(alerts)}")


if __name__ == "__main__":
    main()
