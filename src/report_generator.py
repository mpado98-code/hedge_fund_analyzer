"""
Genera report di sintesi LLM-driven per ogni ticker enriched.
Output: dict serializzabile salvato in data/reports/{ticker}.json
"""
import json
import google.generativeai as genai
from utils import env, get_logger, save_json, REPORTS_DIR, now_iso

log = get_logger("report")


REPORT_PROMPT_TEMPLATE = """Sei un analista buy-side di un hedge fund. Genera un report investment-grade per {ticker} ({name}), conciso ma rigoroso.

DATI DISPONIBILI:
{data_json}

OUTPUT JSON con queste chiavi ESATTE:
{{
  "thesis_short": "2-3 frasi sulla tesi 1-mese: cosa muove il prezzo nelle prossime 4-6 settimane",
  "thesis_long":  "3-4 frasi sulla tesi 3-10y: vantaggio competitivo, durabilità, traiettoria growth",
  "bull_case":    "ipotesi rialziste principali (2-3 bullet)",
  "bear_case":    "ipotesi ribassiste e rischi materiali (2-3 bullet)",
  "key_metrics":  ["ROIC X%", "rev CAGR 5y Y%", "FCF margin Z%"],
  "variant_perception": "una frase: cosa ne pensi diversamente dal consenso",
  "watch_for":    ["catalizzatori e trigger specifici nelle prossime 4-12 settimane"],
  "horizon_recommendation": {{
     "short_1m": "BUY/HOLD/AVOID + rationale 1 frase",
     "long_3_10y": "BUY/HOLD/AVOID + rationale 1 frase"
  }},
  "setup_technical": {{
     "entry": "range prezzo o livello",
     "stop":  "livello stop loss",
     "target":"livello target",
     "rr":    "risk/reward es. 1:2"
  }}
}}

Regole:
- Sii specifico, no boilerplate.
- Usa SOLO numeri presenti nei dati.
- Se un campo non è inferibile, scrivi "n/d".
- Risposta DEVE essere SOLO il JSON valido, nessun testo prima/dopo.
"""


def _llm(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    key = env("GEMINI_API_KEY", required=True)
    genai.configure(api_key=key)
    model = genai.GenerativeModel(model_name)
    resp = model.generate_content(prompt, generation_config={"temperature": 0.3, "response_mime_type": "application/json"})
    return resp.text


def make_report(ticker: str, name: str, payload: dict) -> dict:
    """payload deve contenere: score_short, score_long, fundamentals subset, sentiment, factors, regime."""
    # Compact payload to fit context window
    compact = {
        "score_short": payload.get("score_short", {}),
        "score_long": payload.get("score_long", {}),
        "fund_snapshot": {
            "ratios": (payload.get("fundamentals", {}).get("ratios") or {}),
            "quality": (payload.get("fundamentals", {}).get("quality") or {}),
            "growth": (payload.get("fundamentals", {}).get("growth") or {}),
            "dcf": (payload.get("fundamentals", {}).get("dcf") or {}),
            "earnings_next": (payload.get("fundamentals", {}).get("earnings_next") or {}),
            "insider": (payload.get("fundamentals", {}).get("insider") or {}),
            "profile": {
                "price": (payload.get("fundamentals", {}).get("profile") or {}).get("price"),
                "mktCap": (payload.get("fundamentals", {}).get("profile") or {}).get("mktCap"),
                "sector": (payload.get("fundamentals", {}).get("profile") or {}).get("sector"),
                "industry": (payload.get("fundamentals", {}).get("profile") or {}).get("industry"),
            }
        },
        "sentiment": payload.get("sentiment", {}),
        "factors": payload.get("factors", {}),
        "regime": payload.get("regime", {}),
    }
    prompt = REPORT_PROMPT_TEMPLATE.format(
        ticker=ticker,
        name=name,
        data_json=json.dumps(compact, default=str)[:14000],
    )
    try:
        text = _llm(prompt)
        parsed = json.loads(text)
    except Exception as e:
        log.error(f"LLM report {ticker} failed: {e}")
        parsed = {"error": str(e)}

    report = {
        "ticker": ticker,
        "name": name,
        "generated_at": now_iso(),
        "score_short": (payload.get("score_short") or {}).get("score_short"),
        "score_long":  (payload.get("score_long") or {}).get("score_long"),
        "short_breakdown": (payload.get("score_short") or {}).get("pillars"),
        "long_breakdown":  (payload.get("score_long") or {}).get("pillars"),
        "red_flags": (payload.get("score_long") or {}).get("red_flags", []),
        "sentiment": payload.get("sentiment", {}),
        "factors":   payload.get("factors", {}),
        "regime":    payload.get("regime", {}),
        "fundamentals_snapshot": compact["fund_snapshot"],
        "narrative": parsed,
    }
    save_json(REPORTS_DIR / f"{ticker}.json", report)
    return report
