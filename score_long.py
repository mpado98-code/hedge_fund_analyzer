"""
Score LONG-TERM (orizzonte 3-10 anni). 0-100.

Pillar:
- Quality          25%
- Valuation        25%
- Moat             20%
- Growth           15%
- Governance & ESG 10%
- Thematic Tailwind 5%
"""
from __future__ import annotations
import numpy as np
from utils import clip, safe_div, get_logger

log = get_logger("score_long")

# Themes lookup minimale - sector/keyword based
SECULAR_THEMES = {
    "AI": ["semiconductor", "software", "cloud", "data"],
    "Electrification": ["utilities", "auto", "industrial", "renewable"],
    "Healthcare aging": ["pharma", "biotech", "health", "device"],
    "Onshoring": ["industrial", "construction", "materials"],
    "Cybersecurity": ["software", "security"],
    "Luxury & emerging consumer": ["luxury", "apparel", "consumer"],
}


def quality_subscore(fund: dict) -> tuple[float, dict]:
    q = fund.get("quality", {}) or {}
    roic = q.get("roic_pct") or 0
    roe = q.get("roe_pct") or 0
    fcf_m = q.get("fcf_margin_pct") or 0
    gm = q.get("gross_margin_pct") or 0
    debt_eq = q.get("debt_to_ebitda")

    score = 50.0
    # ROIC: best > 15%
    if roic >= 25:   score += 18
    elif roic >= 15: score += 12
    elif roic >= 8:  score += 4
    elif roic < 0:   score -= 15
    # ROE: best > 20%
    if roe >= 25:   score += 10
    elif roe >= 15: score += 5
    elif roe < 0:   score -= 10
    # FCF margin
    if fcf_m >= 20:  score += 8
    elif fcf_m >= 10: score += 4
    elif fcf_m < 0:   score -= 8
    # Gross margin
    if gm >= 60:  score += 4
    elif gm < 20: score -= 4
    # Leverage
    if debt_eq is not None:
        if debt_eq > 3:  score -= 8
        elif debt_eq > 1.5: score -= 4
        elif debt_eq < 0.5: score += 3
    return clip(score), {"roic": roic, "roe": roe, "fcf_margin": fcf_m, "gm": gm, "debt_eq": debt_eq}


def valuation_subscore(fund: dict) -> tuple[float, dict]:
    dcf = fund.get("dcf", {}) or {}
    profile = fund.get("profile", {}) or {}
    ratios = fund.get("ratios", {}) or {}
    peer_med = fund.get("peer_medians", {}) or {}
    price = profile.get("price") or 0
    dcf_val = dcf.get("dcf") or 0
    pe = ratios.get("peRatioTTM")
    ps = ratios.get("priceToSalesRatioTTM")
    evebitda = ratios.get("enterpriseValueOverEBITDATTM")
    pe_peer = peer_med.get("peRatioTTM")
    ps_peer = peer_med.get("priceToSalesRatioTTM")
    eve_peer = peer_med.get("enterpriseValueOverEBITDATTM")

    score = 50.0
    upside_dcf = None
    if price > 0 and dcf_val > 0:
        upside_dcf = (dcf_val / price - 1) * 100
        if upside_dcf > 50:   score += 22
        elif upside_dcf > 25: score += 14
        elif upside_dcf > 5:  score += 6
        elif upside_dcf < -25: score -= 18
        elif upside_dcf < -5:  score -= 6
    # P/E vs peer
    if pe and pe_peer and pe_peer > 0:
        rel = pe / pe_peer
        if rel < 0.7:   score += 6
        elif rel < 0.9: score += 3
        elif rel > 1.5: score -= 5
        elif rel > 1.2: score -= 2
    # EV/EBITDA vs peer
    if evebitda and eve_peer and eve_peer > 0:
        rel = evebitda / eve_peer
        if rel < 0.8:   score += 4
        elif rel > 1.5: score -= 4
    return clip(score), {"dcf_upside_pct": upside_dcf, "pe": pe, "pe_peer": pe_peer, "ev_ebitda": evebitda}


def moat_subscore(fund: dict) -> tuple[float, dict]:
    """Proxy moat: gross margin stability + level + ROIC consistenza."""
    income = fund.get("income", []) or []
    q = fund.get("quality", {}) or {}
    gm_level = q.get("gross_margin_pct") or 0
    score = 50.0
    # gm level: >50% accenna a moat (brand/IP/network)
    if gm_level >= 60:   score += 14
    elif gm_level >= 45: score += 8
    elif gm_level >= 30: score += 3
    elif gm_level < 20:  score -= 6
    # gm stability 5y
    if len(income) >= 3:
        try:
            gms = [(r.get("grossProfit") or 0) / (r.get("revenue") or 1) * 100 for r in income if r.get("revenue")]
            if len(gms) >= 3:
                std = np.std(gms)
                if std < 2:   score += 8
                elif std < 5: score += 4
                elif std > 10: score -= 4
        except Exception:
            pass
    # R&D intensity (proxy innovation moat)
    try:
        if income:
            rd = (income[0].get("researchAndDevelopmentExpenses") or 0)
            rev = (income[0].get("revenue") or 1)
            rd_pct = rd / rev * 100
            if rd_pct > 15:   score += 5
            elif rd_pct > 8:  score += 3
    except Exception:
        pass
    return clip(score), {"gm_level": gm_level}


def growth_subscore(fund: dict) -> tuple[float, dict]:
    g = fund.get("growth", {}) or {}
    rev_cagr = g.get("rev_cagr_pct") or 0
    eps_cagr = g.get("eps_cagr_pct") or 0
    score = 50.0
    if rev_cagr >= 20:   score += 18
    elif rev_cagr >= 10: score += 10
    elif rev_cagr >= 5:  score += 4
    elif rev_cagr < 0:   score -= 12
    if eps_cagr >= 25:   score += 12
    elif eps_cagr >= 12: score += 6
    elif eps_cagr < 0:   score -= 8
    return clip(score), {"rev_cagr": rev_cagr, "eps_cagr": eps_cagr}


def governance_subscore(fund: dict) -> tuple[float, dict]:
    """Insider trading + capital allocation."""
    insider = fund.get("insider", {}) or {}
    ratios = fund.get("ratios", {}) or {}
    score = 50.0
    # Insider net buying = positivo
    net = insider.get("net_usd") or 0
    if net > 1e7:    score += 12
    elif net > 1e6:  score += 6
    elif net < -5e7: score -= 10
    elif net < -1e7: score -= 4
    # Buyback yield (proxy capital allocation)
    payout = ratios.get("payoutRatioTTM") or 0
    if 0.2 < payout < 0.6: score += 4
    elif payout > 1.0:     score -= 6  # paga più del FCF
    return clip(score), {"insider_net": net, "payout": payout}


def thematic_subscore(fund: dict) -> tuple[float, dict]:
    profile = fund.get("profile", {}) or {}
    sector = (profile.get("sector") or "").lower()
    industry = (profile.get("industry") or "").lower()
    desc = (profile.get("description") or "").lower()[:500]
    hits = []
    for theme, kw in SECULAR_THEMES.items():
        if any(k in sector or k in industry or k in desc for k in kw):
            hits.append(theme)
    score = 50 + min(len(hits), 3) * 10
    return clip(score), {"themes": hits}


def red_flags(fund: dict) -> list[str]:
    flags = []
    q = fund.get("quality", {}) or {}
    r = fund.get("ratios", {}) or {}
    if (q.get("debt_to_ebitda") or 0) > 5: flags.append("debt/equity > 5")
    if (q.get("roic_pct") or 0) < 0:        flags.append("ROIC negativo")
    if (r.get("currentRatioTTM") or 1) < 1: flags.append("current ratio < 1")
    insider = fund.get("insider", {}) or {}
    if (insider.get("net_usd") or 0) < -5e7: flags.append("insider selling pesante")
    return flags


def compute(ticker: str, fund: dict, factors: dict) -> dict:
    s_q, b_q = quality_subscore(fund)
    s_v, b_v = valuation_subscore(fund)
    s_m, b_m = moat_subscore(fund)
    s_g, b_g = growth_subscore(fund)
    s_gov, b_gov = governance_subscore(fund)
    s_t, b_t = thematic_subscore(fund)

    total = (
        0.25 * s_q +
        0.25 * s_v +
        0.20 * s_m +
        0.15 * s_g +
        0.10 * s_gov +
        0.05 * s_t
    )
    return {
        "ticker": ticker,
        "score_long": round(total, 1),
        "red_flags": red_flags(fund),
        "pillars": {
            "quality":   {"score": round(s_q, 1),   "weight": 0.25, "breakdown": b_q},
            "valuation": {"score": round(s_v, 1),   "weight": 0.25, "breakdown": b_v},
            "moat":      {"score": round(s_m, 1),   "weight": 0.20, "breakdown": b_m},
            "growth":    {"score": round(s_g, 1),   "weight": 0.15, "breakdown": b_g},
            "governance":{"score": round(s_gov, 1), "weight": 0.10, "breakdown": b_gov},
            "thematic":  {"score": round(s_t, 1),   "weight": 0.05, "breakdown": b_t},
        },
    }
