"""
Sentiment analysis:
- Primario: Bigdata.com (se BIGDATA_API_KEY presente)
- Fallback: NewsAPI + Gemini per scoring titoli (-1 a +1)

Output per ticker:
{
  "score": -1..+1,                # net sentiment 7g
  "buzz": 0..1,                   # volume news normalizzato
  "n_articles": int,
  "headlines": [{"title", "date", "url", "score"}],
  "themes": ["AI", "earnings", ...]
}
"""
from __future__ import annotations
import requests
import time
from datetime import datetime, timedelta
import google.generativeai as genai
from utils import env, get_logger, retry

log = get_logger("sentiment")

NEWS_URL = "https://newsapi.org/v2/everything"


def _newsapi_recent(query: str, days: int = 7, limit: int = 20) -> list[dict]:
    key = env("NEWSAPI_KEY")
    if not key:
        return []
    frm = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = requests.get(NEWS_URL, params={
            "q": query,
            "from": frm,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": limit,
            "apiKey": key,
        }, timeout=20)
        if r.status_code == 429:
            return []
        r.raise_for_status()
        return r.json().get("articles", [])
    except Exception as e:
        log.warning(f"newsapi {query}: {e}")
        return []


@retry(times=2, delay=2.0)
def _score_headlines_llm(ticker: str, name: str, headlines: list[str]) -> list[float]:
    """Score per headline via Gemini (batch)."""
    key = env("GEMINI_API_KEY")
    if not key or not headlines:
        return [0.0] * len(headlines)
    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""Per {name} ({ticker}), valuta il sentiment di ogni titolo per il prezzo dell'azione.
Rispondi SOLO con una lista JSON di float tra -1 (molto bearish) e +1 (molto bullish).
Lista lunghezza esatta: {len(headlines)}. Esempio output: [0.2, -0.4, 0.1]

Titoli:
{chr(10).join(f"{i+1}. {h}" for i, h in enumerate(headlines))}
"""
    try:
        resp = model.generate_content(prompt, generation_config={"temperature": 0.2})
        text = resp.text.strip()
        # find list bracket
        s = text.find("[")
        e = text.rfind("]")
        if s == -1 or e == -1:
            return [0.0] * len(headlines)
        import json
        arr = json.loads(text[s:e + 1])
        if len(arr) != len(headlines):
            return [0.0] * len(headlines)
        return [float(x) for x in arr]
    except Exception as e:
        log.warning(f"llm score fail: {e}")
        return [0.0] * len(headlines)


def newsapi_sentiment(ticker: str, name: str) -> dict:
    arts = _newsapi_recent(f'"{name}" OR "{ticker}"', days=7, limit=15)
    if not arts:
        return {"score": 0.0, "buzz": 0.0, "n_articles": 0, "headlines": []}
    titles = [a.get("title", "")[:200] for a in arts]
    scores = _score_headlines_llm(ticker, name, titles)
    avg = sum(scores) / len(scores) if scores else 0.0
    out = {
        "score": round(avg, 3),
        "buzz": min(1.0, len(arts) / 15),
        "n_articles": len(arts),
        "headlines": [
            {"title": t, "date": a.get("publishedAt", ""), "url": a.get("url", ""), "score": s}
            for t, a, s in zip(titles, arts, scores)
        ][:10],
    }
    return out


def bigdata_sentiment(ticker: str, name: str) -> dict | None:
    """
    Placeholder per integrazione Bigdata.com.
    In produzione: chiama l'MCP/REST API di Bigdata.com con il ticker e
    parsa il sentiment tearsheet. Se non disponibile ritorna None.
    """
    if not env("BIGDATA_API_KEY"):
        return None
    # TODO: implementare REST call Bigdata.com quando avrai credenziali stabili
    # Esempio struttura attesa:
    # GET https://api.bigdata.com/v1/sentiment/{ticker}?window=7d
    # -> {score, buzz, themes, headlines}
    return None


def sentiment_for(ticker: str, name: str) -> dict:
    bd = bigdata_sentiment(ticker, name)
    if bd:
        bd["source"] = "bigdata"
        return bd
    out = newsapi_sentiment(ticker, name)
    out["source"] = "newsapi+llm"
    return out
