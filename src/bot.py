"""
Telegram bot listener.
Risponde a comandi:
  /start | /help
  /a TICKER         -> report dettagliato (cache + prezzo live yfinance)
  TICKER (plain)    -> stesso
  /rank             -> ultimo top 10 short + long
  /rank short       -> solo short
  /rank long        -> solo long
  /watch TICKER     -> aggiungi a personal watchlist
  /unwatch TICKER
  /perf             -> stato tracker (delega a tracker.py latest report)
  /macro            -> ultimo snapshot macro
  /regime           -> regime corrente
"""
import json
import yfinance as yf
from pathlib import Path
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from utils import env, get_logger, load_json, save_json, DATA_DIR, REPORTS_DIR

log = get_logger("bot")

WATCH_FILE = DATA_DIR / "personal_watchlist.json"


def live_price(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        h = t.history(period="5d", interval="1d", auto_adjust=True)
        if h.empty:
            return None
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) > 1 else last
        return {"price": last, "prev": prev, "chg_pct": (last / prev - 1) * 100 if prev else 0}
    except Exception as e:
        log.warning(f"live price {ticker}: {e}")
        return None


def format_report(report: dict, live: dict | None) -> str:
    n = report.get("narrative", {}) or {}
    short_p = report.get("short_breakdown") or {}
    long_p = report.get("long_breakdown") or {}
    fund = report.get("fundamentals_snapshot", {}) or {}
    profile = fund.get("profile", {}) or {}
    ratios = fund.get("ratios", {}) or {}
    quality = fund.get("quality", {}) or {}
    growth = fund.get("growth", {}) or {}
    earnings_next = fund.get("earnings_next") or {}
    sent = report.get("sentiment", {}) or {}

    sc_s = report.get("score_short") or 0
    sc_l = report.get("score_long") or 0
    stars_s = "⭐" * int(round(sc_s / 20))
    stars_l = "⭐" * int(round(sc_l / 20))

    price_line = ""
    if live:
        cached_price = profile.get("price") or 0
        if cached_price:
            delta_cache = (live["price"] / cached_price - 1) * 100
            price_line = f"💲 Prezzo live: ${live['price']:.2f}  (vs cache ${cached_price:.2f}: {delta_cache:+.1f}%)  | giorno: {live['chg_pct']:+.2f}%"
        else:
            price_line = f"💲 Prezzo live: ${live['price']:.2f}  | giorno: {live['chg_pct']:+.2f}%"

    flags = report.get("red_flags") or []
    flags_line = f"⚠️ *FLAG*: {', '.join(flags)}" if flags else ""

    lines = [
        f"📊 *{report.get('name','?')} ({report['ticker']})*",
        price_line,
        f"Cap: {profile.get('mktCap',0)/1e9:.1f}B | P/E: {ratios.get('peRatioTTM','n/d')} | Settore: {profile.get('sector','?')}",
        "",
        f"🎯 *SCORE SHORT (1m): {sc_s:.0f}/100*  {stars_s}",
    ]
    for k, v in short_p.items():
        lines.append(f"   ├ {k}: {v['score']:.0f}/100 (w {int(v['weight']*100)}%)")
    lines.append("")
    lines.append(f"🏔️ *SCORE LONG (3-10y): {sc_l:.0f}/100*  {stars_l}")
    for k, v in long_p.items():
        lines.append(f"   ├ {k}: {v['score']:.0f}/100 (w {int(v['weight']*100)}%)")
    lines.append("")
    if flags_line:
        lines.append(flags_line)
        lines.append("")
    lines.append(f"*Tesi 1-mese*: {n.get('thesis_short','n/d')}")
    lines.append(f"*Tesi 3-10y*: {n.get('thesis_long','n/d')}")
    lines.append("")
    rec = n.get("horizon_recommendation", {}) or {}
    lines.append(f"📌 *Reco short*: {rec.get('short_1m','n/d')}")
    lines.append(f"📌 *Reco long*:  {rec.get('long_3_10y','n/d')}")
    lines.append("")
    setup = n.get("setup_technical", {}) or {}
    if setup:
        lines.append(f"📈 *Setup*: entry {setup.get('entry','?')} | stop {setup.get('stop','?')} | target {setup.get('target','?')} | R:R {setup.get('rr','?')}")
        lines.append("")
    bull = n.get("bull_case", "")
    bear = n.get("bear_case", "")
    if bull: lines.append(f"🟢 *Bull*: {bull}")
    if bear: lines.append(f"🔴 *Bear*: {bear}")
    vp = n.get("variant_perception", "")
    if vp: lines.append(f"🧠 *Variant view*: {vp}")
    watch = n.get("watch_for", [])
    if watch:
        lines.append("👀 *Watch for*:")
        for w in watch[:5]:
            lines.append(f"   • {w}")
    sent_score = sent.get("score")
    if sent_score is not None:
        lines.append("")
        lines.append(f"📰 Sentiment 7g: {sent_score:+.2f} ({sent.get('n_articles',0)} articoli, fonte: {sent.get('source','?')})")
    lines.append(f"\n_Cache aggiornata: {report.get('generated_at','')[:16]}_")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hedge Fund Agent attivo.\n\n"
        "Comandi:\n"
        "• `/a TICKER` o solo `TICKER` — report completo\n"
        "• `/rank` — ultima top 10\n"
        "• `/rank short` / `/rank long`\n"
        "• `/watch TICKER` / `/unwatch TICKER`\n"
        "• `/macro` — snapshot macro\n"
        "• `/regime` — regime mercato\n"
        "• `/perf` — performance posizioni\n"
        "• `/help`",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update, context):
    await start(update, context)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Estrae ticker da /a TICKER o testo plain."""
    if update.message.text.startswith("/a"):
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text("Uso: `/a NVDA`", parse_mode="Markdown")
            return
        ticker = parts[1].upper()
    else:
        ticker = update.message.text.strip().upper()

    # Normalize possible EU suffixes
    if ticker in ("HELP", "START", "RANK", "MACRO", "REGIME", "PERF"):
        return

    rpt_path = REPORTS_DIR / f"{ticker}.json"
    # Cerca varianti EU
    if not rpt_path.exists():
        for suffix in (".MI", ".PA", ".DE"):
            alt = REPORTS_DIR / f"{ticker.split('.')[0]}{suffix}.json"
            if alt.exists():
                rpt_path = alt
                ticker = ticker.split('.')[0] + suffix
                break

    if not rpt_path.exists():
        await update.message.reply_text(
            f"❌ {ticker} non in cache. La cache contiene solo i ~400 ticker della watchlist settimanale.\n"
            f"Il prossimo weekly run (domenica) potrebbe includerlo. Se è un nome major non in lista, segnalalo."
        )
        return

    report = json.loads(rpt_path.read_text(encoding="utf-8"))
    live = live_price(ticker)
    text = format_report(report, live)
    # Telegram limit 4096 chars
    for i in range(0, len(text), 3900):
        await update.message.reply_text(text[i:i + 3900], parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rank = load_json(DATA_DIR / "ranking_latest.json")
    if not rank:
        await update.message.reply_text("Nessun ranking disponibile. Lancia weekly_run.")
        return
    args = context.args
    which = args[0].lower() if args else "both"
    out = []
    if which in ("short", "both"):
        out.append("🎯 *TOP 10 SHORT-TERM*")
        for i, x in enumerate(rank["top_short"], 1):
            out.append(f"{i}. {x['ticker']} — {x['score']:.0f}/100 — {x['headline']}")
        out.append("")
    if which in ("long", "both"):
        out.append("🏔️ *TOP 10 LONG-TERM*")
        for i, x in enumerate(rank["top_long"], 1):
            out.append(f"{i}. {x['ticker']} — {x['score']:.0f}/100 — {x['headline']}")
    await update.message.reply_text("\n".join(out), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /watch TICKER")
        return
    tk = args[0].upper()
    wl = load_json(WATCH_FILE, default=[])
    if tk not in wl:
        wl.append(tk)
        save_json(WATCH_FILE, wl)
    await update.message.reply_text(f"👁 In watch: {tk} (totale: {len(wl)})")


async def cmd_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        return
    tk = args[0].upper()
    wl = load_json(WATCH_FILE, default=[])
    wl = [x for x in wl if x != tk]
    save_json(WATCH_FILE, wl)
    await update.message.reply_text(f"Rimosso: {tk}")


async def cmd_macro(update, context):
    m = load_json(DATA_DIR / "macro.json")
    if not m:
        await update.message.reply_text("Macro snapshot non disponibile.")
        return
    lines = ["🌍 *MACRO SNAPSHOT*"]
    for k, v in m.items():
        if isinstance(v, dict) and "value" in v:
            lines.append(f"• {k}: {v['value']}  _(asof {v.get('asof','?')})_")
        elif k != "asof":
            lines.append(f"• {k}: {v}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_regime(update, context):
    r = load_json(DATA_DIR / "regime.json")
    if not r:
        await update.message.reply_text("Regime non disponibile.")
        return
    lines = ["📐 *REGIME MERCATO*"]
    for region, v in r.items():
        lines.append(f"\n*{region}*: `{v.get('regime','?')}`")
        for k in ("trend", "vol_level", "vix", "spy_vs_ma200_pct", "breadth", "risk"):
            if k in v:
                lines.append(f"  ├ {k}: {v[k]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_perf(update, context):
    p = load_json(DATA_DIR / "performance_latest.json")
    if not p:
        await update.message.reply_text("Nessun report performance disponibile. Aspetta il tracker venerdì.")
        return
    await update.message.reply_text(p.get("text", "n/d"), parse_mode="Markdown")


def main():
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("a", cmd_analyze))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("macro", cmd_macro))
    app.add_handler(CommandHandler("regime", cmd_regime))
    app.add_handler(CommandHandler("perf", cmd_perf))
    # plain ticker
    app.add_handler(MessageHandler(filters.Regex(r"^[A-Za-z0-9\.\-]{1,8}$") & ~filters.COMMAND, cmd_analyze))
    log.info("Bot polling started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
