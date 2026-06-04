"""
Snapshot macro per US e EU: tassi, FX, inflazione, curve.
US via FRED. EU via ECB SDW (no key needed).
"""
from __future__ import annotations
import requests
from datetime import datetime
from utils import env, get_logger, save_json, DATA_DIR, retry

log = get_logger("macro")

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
ECB_BASE = "https://data-api.ecb.europa.eu/service/data"

FRED_SERIES = {
    "us_10y": "DGS10",
    "us_2y": "DGS2",
    "us_fed_funds": "FEDFUNDS",
    "us_cpi_yoy": "CPIAUCSL",
    "dxy": "DTWEXBGS",
}

# ECB SDW series keys (key URLs)
ECB_SERIES = {
    "eu_10y_bund": ("FM/M.U2.EUR.4F.BB.U2_10Y.YLD", "10Y Bund yield"),
    "ecb_main_rate": ("FM/D.U2.EUR.4F.KR.MRR_FR.LEV", "ECB main refi"),
    "eurusd": ("EXR/D.USD.EUR.SP00.A", "EUR/USD spot"),
    "eu_hicp_yoy": ("ICP/M.U2.N.000000.4.ANR", "HICP YoY"),
}


@retry(times=3, delay=2.0)
def _fred(series_id: str) -> tuple[float | None, str | None]:
    key = env("FRED_API_KEY")
    if not key:
        return None, None
    r = requests.get(FRED_BASE, params={
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 5,
    }, timeout=20)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    for o in obs:
        v = o.get("value")
        if v not in (".", "", None):
            try:
                return float(v), o.get("date")
            except Exception:
                continue
    return None, None


@retry(times=3, delay=2.0)
def _ecb(key: str) -> tuple[float | None, str | None]:
    """Fetch ECB series latest observation. Endpoint returns JSON when accept set."""
    url = f"{ECB_BASE}/{key}?lastNObservations=5&format=jsondata"
    try:
        r = requests.get(url, timeout=20, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        # navigate ECB JSON
        series = data.get("dataSets", [{}])[0].get("series", {})
        for _, s in series.items():
            obs = s.get("observations", {})
            if obs:
                last_key = sorted(obs.keys(), key=int)[-1]
                val = obs[last_key][0]
                # data dimension
                time_dim = data["structure"]["dimensions"]["observation"][0]["values"]
                date = time_dim[int(last_key)]["id"]
                return float(val), date
        return None, None
    except Exception as e:
        log.warning(f"ECB fail {key}: {e}")
        return None, None


def cpi_yoy() -> float | None:
    """Calcola YoY% da CPIAUCSL (livello indice)."""
    key = env("FRED_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(FRED_BASE, params={
            "series_id": "CPIAUCSL",
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 14,
        }, timeout=20)
        obs = r.json().get("observations", [])
        vals = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "", None)]
        if len(vals) < 13:
            return None
        latest = vals[0][1]
        year_ago = vals[12][1]
        return round((latest / year_ago - 1) * 100, 2)
    except Exception as e:
        log.warning(f"CPI YoY fail: {e}")
        return None


def snapshot() -> dict:
    out = {"asof": datetime.utcnow().isoformat()}
    # US via FRED
    for label, sid in FRED_SERIES.items():
        if label == "us_cpi_yoy":
            continue  # calculated separately
        val, dt = _fred(sid)
        out[label] = {"value": val, "asof": dt}
    out["us_cpi_yoy"] = {"value": cpi_yoy(), "asof": "latest"}

    # Yield curve slope US
    y10 = (out["us_10y"] or {}).get("value")
    y2 = (out["us_2y"] or {}).get("value")
    if y10 is not None and y2 is not None:
        out["us_curve_slope_bps"] = round((y10 - y2) * 100, 1)

    # EU via ECB
    for label, (key, _) in ECB_SERIES.items():
        val, dt = _ecb(key)
        out[label] = {"value": val, "asof": dt}

    save_json(DATA_DIR / "macro.json", out)
    log.info(f"Macro snapshot saved")
    return out


if __name__ == "__main__":
    snapshot()
