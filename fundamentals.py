"""
Fondamentali via FMP API + DCF + peer comparables.
FMP key ha 250 call/g sul tier free, quindi usiamo cache aggressiva.

Per ogni ticker watchlist generiamo:
- ratios (P/E, P/S, P/B, EV/EBITDA, ROIC, ROE, debt/equity, FCF margin)
- growth (rev CAGR 5y, EPS CAGR 5y)
- DCF intrinsic value
- peers e mediana settore
- earnings calendar (prossima earning date)
- insider trading flow (last 90d)
"""
import requests
import time
from utils import env, get_logger, retry, safe_div

log = get_logger("fundamentals")
FMP = "https://financialmodelingprep.com/api/v3"
FMP4 = "https://financialmodelingprep.com/api/v4"


def _key():
    return env("FMP_API_KEY", required=True)


@retry(times=3, delay=1.0)
def _get(url: str, params: dict | None = None):
    p = params or {}
    p["apikey"] = _key()
    r = requests.get(url, params=p, timeout=25)
    if r.status_code == 429:
        time.sleep(15)
        r = requests.get(url, params=p, timeout=25)
    r.raise_for_status()
    return r.json()


def profile(ticker: str) -> dict:
    data = _get(f"{FMP}/profile/{ticker}")
    return data[0] if data else {}


def ratios_ttm(ticker: str) -> dict:
    data = _get(f"{FMP}/ratios-ttm/{ticker}")
    return data[0] if data else {}


def key_metrics_ttm(ticker: str) -> dict:
    data = _get(f"{FMP}/key-metrics-ttm/{ticker}")
    return data[0] if data else {}


def income_5y(ticker: str) -> list[dict]:
    return _get(f"{FMP}/income-statement/{ticker}", {"limit": 5}) or []


def cashflow_5y(ticker: str) -> list[dict]:
    return _get(f"{FMP}/cash-flow-statement/{ticker}", {"limit": 5}) or []


def dcf_intrinsic(ticker: str) -> dict:
    data = _get(f"{FMP}/discounted-cash-flow/{ticker}")
    return data[0] if data else {}


def earnings_calendar(ticker: str) -> dict | None:
    """Next earnings date + estimates."""
    data = _get(f"{FMP}/historical/earning_calendar/{ticker}", {"limit": 5})
    if not data:
        return None
    from datetime import date
    today = date.today().isoformat()
    upcoming = [d for d in data if d.get("date", "") >= today]
    return upcoming[-1] if upcoming else None


def insider_trades(ticker: str, days: int = 90) -> dict:
    """Net insider activity last 90d."""
    try:
        data = _get(f"{FMP4}/insider-trading", {"symbol": ticker, "limit": 100})
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        buys = sells = 0.0
        for t in data or []:
            if t.get("transactionDate", "") < cutoff:
                continue
            v = float(t.get("securitiesTransacted", 0) or 0) * float(t.get("price", 0) or 0)
            if (t.get("acquistionOrDisposition") or t.get("acquisitionOrDisposition")) == "A":
                buys += v
            else:
                sells += v
        net = buys - sells
        return {"buys_usd": buys, "sells_usd": sells, "net_usd": net, "ratio": safe_div(buys, sells + buys)}
    except Exception as e:
        log.warning(f"insider {ticker}: {e}")
        return {}


def peers(ticker: str) -> list[str]:
    data = _get(f"{FMP4}/stock_peers", {"symbol": ticker})
    if data and isinstance(data, list) and len(data) > 0:
        return (data[0].get("peersList") or [])[:6]
    return []


def peer_medians(peer_list: list[str]) -> dict:
    """Mediana di ratios chiave del peer group."""
    import statistics
    metrics = {"peRatioTTM": [], "priceToSalesRatioTTM": [], "returnOnEquityTTM": [], "enterpriseValueOverEBITDATTM": []}
    for p in peer_list[:6]:
        try:
            r = ratios_ttm(p)
            for k in metrics:
                v = r.get(k)
                if v is not None and v > 0:
                    metrics[k].append(v)
        except Exception:
            continue
    return {k: (statistics.median(v) if v else None) for k, v in metrics.items()}


def compute_growth(income: list[dict]) -> dict:
    """5y CAGR rev & EPS."""
    if len(income) < 3:
        return {}
    income_sorted = sorted(income, key=lambda x: x.get("date", ""))
    rev = [r.get("revenue") for r in income_sorted if r.get("revenue")]
    eps = [r.get("epsdiluted") or r.get("eps") for r in income_sorted if (r.get("epsdiluted") or r.get("eps"))]
    out = {}
    if len(rev) >= 3 and rev[0] > 0:
        n = len(rev) - 1
        out["rev_cagr_pct"] = round((((rev[-1] / rev[0]) ** (1 / n)) - 1) * 100, 2)
    if len(eps) >= 3 and eps[0] > 0:
        n = len(eps) - 1
        out["eps_cagr_pct"] = round((((eps[-1] / eps[0]) ** (1 / n)) - 1) * 100, 2)
    return out


def compute_quality(ratios: dict, key_metrics: dict, income: list[dict], cashflow: list[dict]) -> dict:
    """Quality composite: ROIC, ROE, margin trend, FCF conversion."""
    q = {
        "roic_pct": (key_metrics.get("roicTTM") or 0) * 100,
        "roe_pct": (ratios.get("returnOnEquityTTM") or 0) * 100,
        "gross_margin_pct": (ratios.get("grossProfitMarginTTM") or 0) * 100,
        "fcf_margin_pct": None,
        "debt_to_ebitda": ratios.get("debtEquityRatioTTM"),
    }
    # FCF margin
    if income and cashflow:
        rev = (income[0] or {}).get("revenue")
        fcf = (cashflow[0] or {}).get("freeCashFlow")
        if rev and fcf:
            q["fcf_margin_pct"] = round(fcf / rev * 100, 2)
    return q


def full_fundamentals(ticker: str) -> dict:
    """Bundle completo per scoring."""
    out = {"ticker": ticker}
    try:
        out["profile"] = profile(ticker)
        out["ratios"] = ratios_ttm(ticker)
        out["key_metrics"] = key_metrics_ttm(ticker)
        out["income"] = income_5y(ticker)
        out["cashflow"] = cashflow_5y(ticker)
        out["dcf"] = dcf_intrinsic(ticker)
        out["earnings_next"] = earnings_calendar(ticker)
        out["insider"] = insider_trades(ticker)
        out["growth"] = compute_growth(out["income"])
        out["quality"] = compute_quality(out["ratios"], out["key_metrics"], out["income"], out["cashflow"])
        try:
            out["peers"] = peers(ticker)
            out["peer_medians"] = peer_medians(out["peers"])
        except Exception:
            out["peers"] = []
            out["peer_medians"] = {}
    except Exception as e:
        log.error(f"full fundamentals {ticker}: {e}")
        out["error"] = str(e)
    return out
