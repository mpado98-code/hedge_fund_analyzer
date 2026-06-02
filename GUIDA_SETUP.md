# Hedge Fund Agent — Guida Setup Completa

Agente che analizza ~1900 aziende US + EU (S&P 500, Russell 1000, Nasdaq 100, FTSE MIB, CAC 40, DAX) con metodologie da hedge fund (fondamentali, comparables, sentiment, factor analysis), e:

- **Domenica sera** manda su Telegram **due ranking top 10**: uno short-term (1 mese), uno long-term (3-10 anni).
- **On-demand** rispondi sul bot con un ticker (es. `/a NVDA`) e ricevi report completo + 2 score 1-100 (short + long) + prezzo live.
- **Tracker performance** ogni venerdì sera valuta i segnali aperti.
- **Calibrazione mensile** ancora gli score a probabilità storiche reali.

Costo target: **0–10 €/mese** (free tier APIs + GitHub Actions).

---

## Architettura in una pagina

```
                          ┌────────────────────────────────────┐
                          │      WEEKLY (dom 22:00 ITA)        │
                          │  1. universe.py    (build ticker)  │
                          │  2. regime.py      (US + EU)       │
                          │  3. macro.py       (FRED + ECB)    │
                          │  4. screen short   (top 300)       │
                          │  5. screen long    (top 300)       │
                          │  6. enrich top 400 unici:          │
                          │     - fundamentals (FMP)           │
                          │     - peers comparables            │
                          │     - DCF                          │
                          │     - sentiment (Bigdata + News)   │
                          │     - factor analysis (4 factor)   │
                          │  7. score_short.py  → 0-100        │
                          │  8. score_long.py   → 0-100        │
                          │  9. LLM report per ognuno          │
                          │ 10. cache JSON in repo             │
                          │ 11. top 10 short + top 10 long     │
                          │     → Telegram                     │
                          └────────────────────────────────────┘
                                          │
   ┌──────────────────────────────────────┼──────────────────────────────────┐
   ▼                                      ▼                                  ▼
┌─────────────┐                ┌───────────────────────┐         ┌──────────────────────┐
│ DAILY 07:00 │                │ TELEGRAM BOT (sempre) │         │ TRACKER (ven 23:00)  │
│ refresh     │                │ /a TICKER → cache +   │         │ apre/chiude posiz.   │
│ prezzi top  │                │ prezzo live           │         │ calcola win rate,    │
│ 400 +       │                │ /rank → ultimi top    │         │ expectancy, drawdown │
│ news flag   │                │ /watch TICKER         │         │ → report Telegram    │
└─────────────┘                └───────────────────────┘         └──────────────────────┘
                                                                            │
                                                              ┌─────────────▼──────────────┐
                                                              │ CALIBRATION (1° del mese)  │
                                                              │ backtest 5 anni            │
                                                              │ score_short × 30g forward  │
                                                              │ score_long  × 3y forward   │
                                                              │ → calibration_short.json   │
                                                              │ → calibration_long.json    │
                                                              └────────────────────────────┘
```

---

## I due modelli di scoring

### Score SHORT (orizzonte ≈ 1 mese)

Pesi base (variabili con regime):

| Pillar | Peso | Cosa misura |
|---|---|---|
| Technical/Momentum | 35% | Trend, RSI, MACD, MA20/50/200, breakout, volume |
| Flow & Sentiment | 25% | Sentiment news 7gg (Bigdata + NewsAPI), analyst revisions, options skew (proxy) |
| Earnings Catalyst | 20% | Giorni a earnings, beat rate storico, EPS revisions ultimi 30g |
| Regime Fit | 10% | Beta-fit al regime corrente (in bull-quiet premia momentum, in range premia mean-rev) |
| Relative Strength | 10% | vs ETF settore (90gg) e vs indice di riferimento |

Output: `score_short` (0-100) + breakdown per pillar + bias regime.

### Score LONG (orizzonte 3-10 anni)

| Pillar | Peso | Cosa misura |
|---|---|---|
| Quality | 25% | ROIC, ROE, gross margin trend 5y, debt/EBITDA, FCF conversion |
| Valuation | 25% | DCF reverse-engineered, EV/EBITDA vs storico 10y, FCF yield, P/E vs peer median |
| Moat | 20% | Margin stability, gross margin level, market share trend, R&D intensity, retention proxy |
| Growth | 15% | Revenue CAGR 5y, EPS CAGR 5y, secular trend exposure, TAM commentary |
| Governance & ESG | 10% | Insider buys/sells, capital allocation (buyback yield vs dilution), board independence, controversies |
| Thematic Tailwind | 5% | Esposizione a temi secolari (AI, electrification, demographics, onshoring) |

Output: `score_long` (0-100) + breakdown per pillar + flag rossi (red flags governance/accounting).

### Filosofia

- Score **NON sono raccomandazioni di trading**: sono probabilità calibrate empiricamente.
- 1 = "non comprare, alta probabilità storica di sottoperformance sul tuo orizzonte"
- 100 = "compra subito, top decile storico per il setup attuale"
- 50 = "neutro, nessun edge statistico"

---

## Universo (≈ 1900 ticker unici)

| Indice | Ticker | Note |
|---|---|---|
| S&P 500 | ~500 | Wikipedia scrape |
| Russell 1000 | ~1000 | iShares IWB holdings CSV |
| Nasdaq 100 | ~100 | Wikipedia scrape (overlap ~90% con S&P) |
| FTSE MIB | 40 | Wikipedia scrape, suffix `.MI` |
| CAC 40 | 40 | Wikipedia scrape, suffix `.PA` |
| DAX 40 | 40 | Wikipedia scrape, suffix `.DE` |
| **Totale unici** | **~1900** | dopo dedup |

I ticker EU vengono normalizzati per yfinance (`.MI`, `.PA`, `.DE`) e per FMP (alcuni usano formato diverso, es. `RACE.MI` → FMP usa `RACE.MI` o `RACE`).

---

## Stack tecnico

| Componente | Tecnologia | Free tier |
|---|---|---|
| Compute | GitHub Actions | 2000 min/mese gratis (basta) |
| Cache/Storage | File JSON committati nel repo + GitHub LFS opzionale | gratis |
| Prezzi | `yfinance` (Yahoo) | unofficial, no key |
| Fondamentali | FMP API | 250 call/g — bastano per top 400 watchlist su run weekly |
| Macro US | FRED API | illimitato |
| Macro EU | ECB Statistical Data Warehouse | illimitato, no key |
| News headline | NewsAPI | 100 call/g |
| Sentiment pro (opt) | Bigdata.com MCP | usa se hai subscription, altrimenti fallback |
| LLM synthesis | Gemini 2.5 Flash | 1500 req/g free |
| Bot Telegram | python-telegram-bot v21 | gratis |

Se Bigdata.com non è disponibile, il sentiment usa NewsAPI + analisi LLM dei titoli (qualità leggermente inferiore ma funzionale).

---

## STEP 1 — Crea bot Telegram (3 min)

Apri Telegram, cerca `@BotFather`, manda `/newbot`. Nome consigliato: `Hedgefund Agent Marco`. Username che finisca in `bot`. Copia il **token**.

Chat ID: manda un messaggio al bot (es. `start`), apri:
```
https://api.telegram.org/botTOKEN/getUpdates
```
Cerca `"chat":{"id":` → è il chat ID.

---

## STEP 2 — Registra account API gratuiti

| Service | URL | Tier free |
|---|---|---|
| **FMP** | https://site.financialmodelingprep.com/developer/docs | 250 call/g |
| **NewsAPI** | https://newsapi.org | 100 call/g |
| **FRED** | https://fredaccount.stlouisfed.org/apikeys | illimitato |
| **Gemini** | https://aistudio.google.com/apikey | 1500 req/g |

Bigdata.com: se hai già accesso, ottieni le credenziali OAuth dall'admin. Altrimenti **skip** — il bot funziona lo stesso.

---

## STEP 3 — Crea il repo GitHub

Nuovo repo `hedgefund-agent`. Public o private. Aggiungi README. Carica TUTTI i file della cartella `hedgefund-agent/` che ti ho preparato:

```
hedgefund-agent/
├── src/
│   ├── universe.py
│   ├── regime.py
│   ├── macro.py
│   ├── fundamentals.py
│   ├── sentiment.py
│   ├── factor_analysis.py
│   ├── score_short.py
│   ├── score_long.py
│   ├── report_generator.py
│   ├── bot.py
│   ├── tracker.py
│   ├── calibration_runner.py
│   ├── weekly_run.py
│   ├── daily_refresh.py
│   └── utils.py
├── workflows/        ← copia in .github/workflows/ del repo
│   ├── weekly.yml
│   ├── daily.yml
│   ├── tracker.yml
│   ├── calibration.yml
│   └── bot_listener.yml
├── data/             ← inizialmente vuota, riempita dai workflow
├── requirements.txt
└── GUIDA_SETUP.md
```

---

## STEP 4 — Secrets GitHub

In **Settings → Secrets and variables → Actions → New repository secret**:

| Nome | Valore |
|---|---|
| `TELEGRAM_BOT_TOKEN` | dal Step 1 |
| `TELEGRAM_CHAT_ID` | dal Step 1 |
| `GEMINI_API_KEY` | da AI Studio |
| `FMP_API_KEY` | da FMP |
| `NEWSAPI_KEY` | da NewsAPI |
| `FRED_API_KEY` | da FRED |
| `BIGDATA_API_KEY` | opzionale, lascia vuoto se non disponibile |

---

## STEP 5 — Prima esecuzione: calibrazione

La calibrazione **DEVE girare prima** dello weekly_run, altrimenti gli score non hanno ancora le probabilità empiriche.

**Actions → Calibration → Run workflow → main → Run**.

Tempo: 30-45 min (carica 5 anni di prezzi per 400 ticker random + calcola score storici).

Output committato nel repo:
- `data/calibration_short.json` — buckets per `score_short × regime` con P(target 5% in 30g), P(stop -4%), avg ret 30g.
- `data/calibration_long.json` — buckets per `score_long` con P(CAGR ≥ 8% over 3y), max drawdown medio, avg ret 3y.

---

## STEP 6 — Prima esecuzione: weekly_run

**Actions → Weekly Run → Run workflow**.

Tempo: 20-30 min. Cosa fa:

1. Build universe (~1900 ticker)
2. Regime classifier US + EU (output: `bull-quiet`, `bull-volatile`, `range-quiet`, `range-volatile`, `bear-quiet`, `bear-volatile` × 2 regioni)
3. Macro snapshot (Fed, ECB, DXY, EUR/USD, 10Y US, 10Y Bund, curve)
4. Screening short — top 300 per momentum/sentiment/regime fit
5. Screening long — top 300 per quality/value/moat
6. Unione (~400-450 ticker watchlist) → enrichment completo via FMP
7. Calcolo `score_short` + `score_long` per ognuno
8. Gemini genera report di sintesi per ognuno
9. Cache committata: `data/watchlist.json` (lookup veloce) + `data/reports/{ticker}.json` (report completi)
10. Top 10 short + top 10 long → 2 messaggi Telegram

Esempio messaggio:
```
🎯 TOP 10 SHORT-TERM (orizzonte 1 mese)
Run: 07/06/2026 — Regime US: bull-quiet | EU: range-quiet

1. NVDA  86/100 — momentum top decile, sentiment +0.42, earnings 4g
2. ASML.AS 81/100 — breakout, EU AI capex tailwind  ← (nota: in scope FR/IT/DE quindi no)
2. SAP.DE 81/100 — beat earnings 2g fa, RS 92
...

⚠️ Stop/target suggeriti: ATR 2x stop, ATR 4x target
📊 Probabilità storica bucket [80-90]: P(+5% in 30g) = 41%, P(-4%) = 22%
```

---

## STEP 7 — Bot listener on-demand

Il bot listener gira come long-running process. GitHub Actions free non supporta job > 6h continuativi, quindi usiamo **polling con job 5h45m che si auto-restarta**.

**Actions → Bot Listener → Run workflow**. Si auto-rilancia.

Comandi supportati:

| Comando | Cosa fa |
|---|---|
| `/start` | onboarding |
| `/a TICKER` o solo `TICKER` | report completo dal cache + prezzo live |
| `/rank` | ultimo top 10 short + long |
| `/rank short` | solo short |
| `/rank long` | solo long |
| `/watch TICKER` | aggiunge a watchlist personale, monitoring news |
| `/unwatch TICKER` | rimuove |
| `/perf` | stato performance tracker |
| `/help` | lista comandi |

**Risposta tipo per `/a NVDA`**:
```
📊 NVIDIA Corporation (NVDA)
Prezzo live: $137.42  (vs cache $135.20: +1.6%)
Cap: $3.35T | P/E fwd: 31x | Sector: Semiconductors

🎯 SCORE SHORT (1m): 86/100  ⭐⭐⭐⭐
   ├ Technical: 92/100 — breakout above $135, RSI 68, MA200 +18%
   ├ Sentiment: 81/100 — +0.42 net 7g, 12 analyst upgrades
   ├ Catalyst: 95/100 — earnings 4g
   ├ Regime fit: 78/100 — bull-quiet premia mom-tech
   └ Rel strength: 88/100 — outperform SOXX +14% 90g

🏔️ SCORE LONG (3-10y): 72/100  ⭐⭐⭐
   ├ Quality: 91/100 — ROIC 105%, FCF margin 49%
   ├ Valuation: 38/100 — DCF implica IRR 6%, EV/EBITDA 35x (premium)
   ├ Moat: 88/100 — CUDA moat + 80% gross margin
   ├ Growth: 92/100 — rev CAGR 5y 65%, TAM AI > $1T
   ├ Governance: 65/100 — insider selling alto, ma capital allocation solida
   └ Thematic: 95/100 — AI core beneficiary

⚠️ FLAG: valuation tirata, dipendenza hyperscalers (TOP4 = 56% rev)

📈 SETUP TECNICO (per short)
   Entry: $135-138 | Stop: $128 (-7%) | Target: $148 (+8%) | R:R 1.4

📊 PROBABILITÀ STORICHE (calibration 5y)
   Bucket score-short 80-90 in regime bull-quiet:
   P(+5% in 30g): 44% | P(-4%): 19% | Avg ret 30g: +2.8%
   Bucket score-long 70-80:
   P(CAGR > 8% over 3y): 58% | Avg ret 3y annualizzato: +11.2%

📝 TESI BREVE
Hedge fund take: long quality compounder con catalist earnings imminente;
valuation premium giustificato dal moat AI ma comprime rendimenti attesi
3-5y. Posizionamento ideale: trading position short-term con conviction
alta, allocazione long-term più contenuta (5-8% portafoglio max) data
concentrazione settoriale.

🔗 Dati: FMP, Bigdata.com, Yahoo Finance, FRED — aggiornati 07/06 22:00 ITA
```

---

## STEP 8 — Tracker performance

Stesso meccanismo del progetto precedente, ma traccia DUE pool di posizioni:

- **Short positions**: orizzonte 30g, target ATR×4, stop ATR×2
- **Long positions**: orizzonte 3y, target +35%, stop -25% (trailing)

Report settimanale ven 23:00 ITA su Telegram, identico in formato a quello che hai già.

---

## STEP 9 — Manutenzione

| Job | Frequenza | Cosa fare se fallisce |
|---|---|---|
| Calibration | 1° del mese 03:00 UTC | rilancia manualmente |
| Weekly run | Dom 22:00 ITA | check log per FMP rate limit |
| Daily refresh | Lun-ven 07:30 ITA | yfinance throttling, riavvia |
| Tracker | Ven 23:00 ITA | nessuno, solo report |
| Bot listener | continuo | auto-restart, ma se Telegram block check token |

### Quando rivedere i pesi

- Dopo 30 trade short: confronta win rate reale vs probabilità calibrate. Se reale > calibrato di >10pp → alza soglia di pubblicazione. Se reale < calibrato → c'è bug, controlla feature drift.
- Dopo 6 mesi: rifai calibrazione su window mobile 5y.
- Quando regime cambia drasticamente (es. da QT a QE, da bull a bear): ricalibra subito.

---

## Limiti e disclaimer

1. **Non è advisory**: è un decision-support tool. Non esegue trade. Le decisioni finali sono tue.
2. **Bias storico**: la calibrazione assume che il futuro assomigli al passato 5y. Non gestisce regime shift estremi (es. Covid, GFC) in tempo reale.
3. **Universo limitato a US+EU**: niente JP/CN/KR/IN nella v1. Estendibile aggiungendo source per Nikkei, KOSPI, ecc. (richiede dati FMP premium o source alternativa).
4. **Latenza dati FMP**: alcuni fondamentali sono aggiornati con 1-3 giorni di delay vs filing.
5. **Sentiment proxy**: senza Bigdata.com, sentiment è meno granulare (titoli vs full text).
6. **No fiscalità**: il bot non considera tasse italiane su capital gain. La logica di scoring è pre-tax.

---

## Roadmap v2 (opzionale, dopo che v1 è stabile)

- [ ] Aggiungere Giappone (Nikkei 225) via JPX feed
- [ ] Options data per skew/IV come segnale flow
- [ ] Backtest walk-forward annuale automatico
- [ ] Portfolio constructor: ottimizzatore Black-Litterman su top 10 settimanali
- [ ] Webhook Slack/Discord oltre a Telegram
- [ ] Cina/Hong Kong tramite HKEX listing per ADR
- [ ] Korea (KOSPI 200) via Naver Finance scraping

---

## Cosa rende questo agente "hedge fund grade" (per davvero)

| Aspetto | Hedge fund quant | Questo bot |
|---|---|---|
| Universo multi-mercato | ✓ globale | ✓ US + EU (sufficiente per retail IT) |
| Factor models | ✓ proprietari | ✓ 4 factor base (value, momentum, quality, low-vol) |
| Fundamental deep dive | ✓ analisti | ~  FMP data + DCF + peers + LLM synthesis |
| Sentiment alternative data | ✓ premium feeds | ~  Bigdata.com (se attivo) + NewsAPI |
| Regime classifier | ✓ HMM proprietari | ✓ regime classifier euristico calibrato |
| Backtest scientifico | ✓ Walk-forward, Sharpe | ✓ calibration buckets, expectancy reali |
| Risk management | ✓ VaR, correlazioni | ✓ correlazioni candidate, stop ATR |
| Costo | $$$$$ | ~ 0 € |
| Decisione finale | comitato investimenti | ✓ tu, informato |

Il "gap" che resta vs hedge fund vero: alternative data esotici (credit card data, satellite imagery, niche surveys), execution algos, tax optimization, leva strutturata. Per retail, è oltre il punto di diminishing returns.

---

Apri ora i file `src/` per il codice. Inizia con `weekly_run.py` per capire il flow end-to-end.
