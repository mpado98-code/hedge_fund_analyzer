# Hedge Fund Agent

Bot Telegram che valuta ~1900 aziende US+EU come un buy-side hedge fund e:

- ogni domenica sera manda **2 ranking top 10** (short-term 1 mese, long-term 3-10 anni)
- risponde on-demand a `/a TICKER` con **report completo + 2 score 1-100 + prezzo live**
- traccia performance reali, calibra gli score con backtest 5y

## Quick start

1. Leggi `GUIDA_SETUP.md` — passo per passo.
2. Carica `src/`, `requirements.txt`, e i 5 file in `workflows/` (rinominali in `.github/workflows/`) in un nuovo repo GitHub.
3. Aggiungi i 7 secrets (Telegram, FMP, FRED, NewsAPI, Gemini, [Bigdata opzionale]).
4. Lancia **Calibration** una volta a mano.
5. Lancia **Weekly Run** una volta a mano.
6. Lancia **Bot Listener** (si auto-restarta).

## Architettura in due righe

`universe → regime → screening → enrich (FMP) → factors → sentiment → score_short + score_long → LLM report → cache + Telegram`

## Cosa lo distingue dal precedente stock picker

| | Stock Picker S&P500 | Hedge Fund Agent |
|---|---|---|
| Universo | 500 | ~1900 |
| Score | 1 (conviction generale) | 2 separati (short + long) |
| Mercati | US | US + IT + FR + DE |
| Output | 1 azione/giorno | Top 10 short + Top 10 long settimanali + on-demand |
| Fattori | tecnico + reg | tecnico + fond + factor (value/mom/qual/lowvol) + sentiment |
| LLM | Gemini sceglie | Gemini scrive thesis per ogni ticker |

## File principali

```
src/
  weekly_run.py        ← entry point pipeline settimanale
  bot.py               ← Telegram listener
  tracker.py           ← performance + posizioni virtuali
  calibration_runner.py← backtest 5y
  daily_refresh.py     ← prezzi giornalieri + alert
  universe.py          ← S&P/R1000/NDX/CAC/FTSEMIB/DAX
  regime.py            ← classifier US + EU
  macro.py             ← FRED + ECB
  fundamentals.py      ← FMP API
  sentiment.py         ← Bigdata.com + NewsAPI+LLM
  factor_analysis.py   ← value/momentum/quality/lowvol Z-scores
  score_short.py       ← scoring 1m (5 pillar)
  score_long.py        ← scoring 3-10y (6 pillar)
  report_generator.py  ← thesis LLM
  utils.py             ← logging, env, JSON I/O
```

## Disclaimer

Decision-support tool, non advisory. Nessun trade automatico. La fiscalità italiana non è considerata nello scoring.
