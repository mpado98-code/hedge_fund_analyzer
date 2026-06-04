"""
Performance tracker. Apre posizioni dai ranking settimanali (top 5 short + top 5 long).
Monitora stop/target. Calcola win rate, expectancy, breakdown per regime e bucket score.
Report Telegram il venerdì sera.

positions.json structure:
{
  "positions": [
    {
      "id": "...",
      "ticker": "NVDA",
      "horizon": "short" | "long",
      "entry_date": "2026-06-01",
      "entry_price": 135.20,
      "stop": 128.0,
      "target": 148.0,
      "score": 78,
      "regime": "bull-quiet",
      "status": "OPEN" | "CLOSED_TARGET" | "CLOSED_STOP" | "CLOSED_TIMEOUT",
      "exit_date": "...",
      "exit_price": ...,
      "pnl_pct": ...
    }
  ]
}
"""
from __future__ import annotations
import yfinance as yf
import requests
import statistics
from datetime import date, timedelta, datetime
from utils import env, get_logger, load_json, save_json, DATA_DIR, now_iso

log = get_logger("tracker")

POS_FILE = DATA_DIR / "positions.json"
PERF_FILE = DATA_DIR / "performance_latest.json"

# Orizzonti
SHORT_HOLD_DAYS = 30
LONG_HOLD_DAYS = 365 * 3  # 3 anni (per simplicity tracker monitora milestones)

# Default risk params (in % se non specificato dal report)
DEFAULT_SHORT_STOP_PCT = -7.0
DEFAULT_SHORT_TARGET_PCT = 12.0
DEFAULT_LONG_STOP_PCT = -25.0
DEFAULT_LONG_TARGET_PCT = 35.0


def open_from_ranking(rank: dict, n_short: int = 5, n_long: int = 5) -> None:
    """Apre posizioni virtuali dai top picks settimanali (se non già aperte)."""
    pos = load_json(POS_FILE, default={"positions": []})
    existing = {(p["ticker"], p["horizon"], p["entry_date"]) for p in pos["positions"]}

    today = date.today().isoformat()
    regime_us = (rank.get("regime", {}).get("US", {}) or {}).get("regime", "unknown")

    def add(ticker, score, horizon):
        key = (ticker, horizon, today)
        if key in existing:
            return
        try:
            tk = yf.Ticker(ticker)
            h = tk.history(period="5d", auto_adjust=True)
            if h.empty:
                return
            price = float(h["Close"].iloc[-1])
        except Exception as e:
            log.warning(f"open {ticker}: {e}")
            return
        if horizon == "short":
            stop = price * (1 + DEFAULT_SHORT_STOP_PCT / 100)
            target = price * (1 + DEFAULT_SHORT_TARGET_PCT / 100)
        else:
            stop = price * (1 + DEFAULT_LONG_STOP_PCT / 100)
            target = price * (1 + DEFAULT_LONG_TARGET_PCT / 100)
        pos["positions"].append({
            "id": f"{ticker}_{horizon}_{today}",
            "ticker": ticker, "horizon": horizon,
            "entry_date": today, "entry_price": price,
            "stop": round(stop, 2), "target": round(target, 2),
            "score": score, "regime": regime_us,
            "status": "OPEN",
        })
        log.info(f"open {ticker} {horizon} @ {price:.2f}  stop {stop:.2f}  target {target:.2f}")

    for p in rank.get("top_short", [])[:n_short]:
        add(p["ticker"], p["score"], "short")
    for p in rank.get("top_long", [])[:n_long]:
        add(p["ticker"], p["score"], "long")
    save_json(POS_FILE, pos)


def evaluate_positions() -> dict:
    pos = load_json(POS_FILE, default={"positions": []})
    today = date.today()

    updated = []
    for p in pos["positions"]:
        if p["status"] != "OPEN":
            updated.append(p)
            continue
        entry_date = date.fromisoformat(p["entry_date"])
        days_held = (today - entry_date).days
        max_days = SHORT_HOLD_DAYS if p["horizon"] == "short" else LONG_HOLD_DAYS

        try:
            tk = yf.Ticker(p["ticker"])
            h = tk.history(start=p["entry_date"], end=today.isoformat(), auto_adjust=True)
            if h.empty:
                updated.append(p)
                continue
            # check stop/target during holding period
            hit = None
            for _, row in h.iterrows():
                if row["Low"] <= p["stop"]:
                    hit = ("CLOSED_STOP", p["stop"], row.name.date())
                    break
                if row["High"] >= p["target"]:
                    hit = ("CLOSED_TARGET", p["target"], row.name.date())
                    break
            if hit:
                status, exit_price, exit_date = hit
                p.update({
                    "status": status, "exit_price": float(exit_price),
                    "exit_date": exit_date.isoformat(),
                    "pnl_pct": round((exit_price / p["entry_price"] - 1) * 100, 2),
                })
            elif days_held >= max_days:
                last = float(h["Close"].iloc[-1])
                p.update({
                    "status": "CLOSED_TIMEOUT", "exit_price": last,
                    "exit_date": today.isoformat(),
                    "pnl_pct": round((last / p["entry_price"] - 1) * 100, 2),
                })
        except Exception as e:
            log.warning(f"eval {p['ticker']}: {e}")
        updated.append(p)
    pos["positions"] = updated
    save_json(POS_FILE, pos)
    return pos


def aggregate(pos: dict) -> dict:
    closed = [p for p in pos["positions"] if p["status"] != "OPEN"]
    open_p = [p for p in pos["positions"] if p["status"] == "OPEN"]
    out = {
        "total": len(pos["positions"]),
        "open": len(open_p),
        "closed": len(closed),
        "by_horizon": {},
    }
    for h in ("short", "long"):
        sub = [p for p in closed if p["horizon"] == h]
        if not sub:
            continue
        wins = [p for p in sub if p["status"] == "CLOSED_TARGET"]
        losses = [p for p in sub if p["status"] == "CLOSED_STOP"]
        pnls = [p["pnl_pct"] for p in sub if "pnl_pct" in p]
        out["by_horizon"][h] = {
            "n": len(sub),
            "win_rate": round(len(wins) / len(sub) * 100, 1),
            "n_wins": len(wins),
            "n_losses": len(losses),
            "avg_pnl_pct": round(statistics.mean(pnls), 2) if pnls else 0,
            "median_pnl_pct": round(statistics.median(pnls), 2) if pnls else 0,
            "cum_pnl_pct": round(sum(pnls), 2),
            "max_dd_pct": round(min(pnls), 2) if pnls else 0,
        }
    # breakdown per regime
    out["by_regime"] = {}
    regimes = set(p["regime"] for p in closed)
    for rg in regimes:
        sub = [p for p in closed if p["regime"] == rg]
        if not sub:
            continue
        pnls = [p["pnl_pct"] for p in sub if "pnl_pct" in p]
        out["by_regime"][rg] = {
            "n": len(sub),
            "win_rate": round(sum(1 for p in sub if p["status"] == "CLOSED_TARGET") / len(sub) * 100, 1),
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
        }
    # breakdown per score bucket
    out["by_score_bucket"] = {}
    for low, high in [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]:
        sub = [p for p in closed if low <= p["score"] < high]
        if not sub:
            continue
        pnls = [p["pnl_pct"] for p in sub]
        out["by_score_bucket"][f"{low}-{high}"] = {
            "n": len(sub),
            "win_rate": round(sum(1 for p in sub if p["status"] == "CLOSED_TARGET") / len(sub) * 100, 1),
            "avg_pnl": round(statistics.mean(pnls), 2),
        }
    return out


def format_report(agg: dict, pos: dict) -> str:
    today_str = date.today().strftime("%d/%m/%Y")
    lines = [
        f"📊 *PERFORMANCE REPORT — {today_str}*",
        "",
        f"📈 Trade totali: {agg['total']} (aperti: {agg['open']}, chiusi: {agg['closed']})",
    ]
    for h, st in agg.get("by_horizon", {}).items():
        emoji = "⚡" if h == "short" else "🏔️"
        lines += [
            "",
            f"{emoji} *{h.upper()}* ({st['n']} chiusi)",
            f"   Win rate: {st['win_rate']}%  ({st['n_wins']}W / {st['n_losses']}L)",
            f"   Avg PnL: {st['avg_pnl_pct']:+.2f}%   Cum: {st['cum_pnl_pct']:+.2f}%",
            f"   Max single drawdown: {st['max_dd_pct']:.2f}%",
        ]
    open_pos = [p for p in pos["positions"] if p["status"] == "OPEN"]
    if open_pos:
        lines += ["", f"💼 *Aperte ({len(open_pos)})*"]
        for p in open_pos[:10]:
            try:
                tk = yf.Ticker(p["ticker"])
                last = float(tk.history(period="2d", auto_adjust=True)["Close"].iloc[-1])
                chg = (last / p["entry_price"] - 1) * 100
                emo = "🟢" if chg > 1 else "🔴" if chg < -1 else "🟡"
                days = (date.today() - date.fromisoformat(p["entry_date"])).days
                mx = SHORT_HOLD_DAYS if p["horizon"] == "short" else "long"
                lines.append(f"   {emo} {p['ticker']} ({p['horizon']}) {days}g | ${p['entry_price']:.2f} → ${last:.2f} ({chg:+.1f}%) | conv {p['score']}")
            except Exception:
                pass
    if agg.get("by_regime"):
        lines += ["", "🌍 *Breakdown regime*"]
        for rg, st in agg["by_regime"].items():
            lines.append(f"   {rg}: {st['n']} trade | Win {st['win_rate']}% | Avg {st['avg_pnl']:+.2f}%")
    if agg.get("by_score_bucket"):
        lines += ["", "⭐ *Breakdown conviction*"]
        for b, st in agg["by_score_bucket"].items():
            lines.append(f"   {b}: {st['n']} | Win {st['win_rate']}% | Avg {st['avg_pnl']:+.2f}%")
    return "\n".join(lines)


def _smart_chunks(text: str, size: int = 3800) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


def telegram_send(text: str):
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    for c in _smart_chunks(text):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": c, "parse_mode": "Markdown"},
                timeout=15,
            )
        except Exception as e:
            log.error(f"telegram: {e}")


def main():
    log.info("=== TRACKER START ===")
    rank = load_json(DATA_DIR / "ranking_latest.json")
    if rank:
        open_from_ranking(rank)
    pos = evaluate_positions()
    agg = aggregate(pos)
    text = format_report(agg, pos)
    save_json(PERF_FILE, {"asof": now_iso(), "agg": agg, "text": text})
    telegram_send(text)
    log.info("=== TRACKER DONE ===")


if __name__ == "__main__":
    main()
