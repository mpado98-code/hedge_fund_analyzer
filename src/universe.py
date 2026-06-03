"""
Build dell'universo investibile: S&P 500, Russell 1000, Nasdaq 100, FTSE MIB, CAC 40, DAX.

Output: dict { ticker_yfinance: {name, exchange, region, sector, in_indices: [...] } }
Salva in data/universe.json
"""
import pandas as pd
import requests
from io import StringIO
from utils import save_json, DATA_DIR, get_logger, retry

log = get_logger("universe")

WIKI = {
    "sp500":   "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "ndx100":  "https://en.wikipedia.org/wiki/Nasdaq-100",
    "ftsemib": "https://en.wikipedia.org/wiki/FTSE_MIB",
    "cac40":   "https://en.wikipedia.org/wiki/CAC_40",
    "dax":     "https://en.wikipedia.org/wiki/DAX",
}

# Russell 1000: iShares IWB holdings CSV (free, no login)
IWB_CSV = "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"

HEADERS = {"User-Agent": "Mozilla/5.0 (HedgeFundAgent/1.0)"}


@retry(times=3, delay=3.0)
def _fetch_wiki_tables(url: str) -> list[pd.DataFrame]:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return pd.read_html(StringIO(r.text))


def get_sp500() -> list[dict]:
    tables = _fetch_wiki_tables(WIKI["sp500"])
    df = tables[0]
    out = []
    for _, row in df.iterrows():
        tk = str(row.get("Symbol", "")).replace(".", "-")  # BRK.B -> BRK-B for yfinance
        if not tk:
            continue
        out.append({
            "ticker": tk,
            "name": row.get("Security", ""),
            "sector": row.get("GICS Sector", ""),
            "region": "US",
            "exchange": "NYSE/NASDAQ",
            "index": "SP500",
        })
    log.info(f"SP500: {len(out)}")
    return out


def get_ndx100() -> list[dict]:
    tables = _fetch_wiki_tables(WIKI["ndx100"])
    # tabella con i componenti ha tipicamente colonne 'Symbol' o 'Ticker'
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("symbol" in c or "ticker" in c for c in cols) and len(t) >= 80:
            df = t
            break
    else:
        log.warning("NDX100 table not found")
        return []
    sym_col = [c for c in df.columns if "symbol" in str(c).lower() or "ticker" in str(c).lower()][0]
    name_col = next((c for c in df.columns if "company" in str(c).lower() or "name" in str(c).lower()), sym_col)
    sec_col = next((c for c in df.columns if "sector" in str(c).lower() or "industry" in str(c).lower()), None)
    out = []
    for _, row in df.iterrows():
        tk = str(row[sym_col]).replace(".", "-")
        out.append({
            "ticker": tk,
            "name": str(row[name_col]) if name_col else "",
            "sector": str(row[sec_col]) if sec_col else "",
            "region": "US",
            "exchange": "NASDAQ",
            "index": "NDX100",
        })
    log.info(f"NDX100: {len(out)}")
    return out


@retry(times=3, delay=5.0)
def get_russell1000() -> list[dict]:
    r = requests.get(IWB_CSV, headers=HEADERS, timeout=60)
    r.raise_for_status()
    text = r.text
    # iShares CSV ha header con metadati nelle prime 9 righe
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("Ticker,Name") or line.startswith('"Ticker"'):
            start = i
            break
    csv_body = "\n".join(lines[start:])
    df = pd.read_csv(StringIO(csv_body))
    df = df[df["Ticker"].notna()]
    df = df[df["Asset Class"].fillna("Equity").str.contains("Equity", case=False)]
    out = []
    for _, row in df.iterrows():
        tk = str(row["Ticker"]).strip().replace(".", "-")
        if not tk or tk == "-" or len(tk) > 6:
            continue
        out.append({
            "ticker": tk,
            "name": row.get("Name", ""),
            "sector": row.get("Sector", ""),
            "region": "US",
            "exchange": row.get("Exchange", ""),
            "index": "R1000",
        })
    log.info(f"Russell1000: {len(out)}")
    return out


def get_european(index_name: str, wiki_key: str, suffix: str) -> list[dict]:
    """Estrai i ticker dei principali indici EU dalla wiki."""
    tables = _fetch_wiki_tables(WIKI[wiki_key])
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if (any("ticker" in c or "symbol" in c or "isin" in c for c in cols) and len(t) >= 30):
            df = t
            break
    if df is None:
        log.warning(f"{index_name} table not found")
        return []
    sym_col = None
    for c in df.columns:
        cl = str(c).lower()
        if "ticker" in cl or "symbol" in cl or "code" in cl:
            sym_col = c
            break
    if sym_col is None:
        # fallback: prendi colonna ISIN o nome
        return []
    name_col = next((c for c in df.columns if "company" in str(c).lower() or "name" in str(c).lower() or "issuer" in str(c).lower()), sym_col)
    sec_col = next((c for c in df.columns if "sector" in str(c).lower() or "industry" in str(c).lower() or "ics" in str(c).lower()), None)
    out = []
    for _, row in df.iterrows():
        raw = str(row[sym_col]).strip()
        # pulizia: a volte sono "RACE.MI", a volte "RACE", a volte "Ferrari.MI"
        if "." in raw and len(raw.split(".")[0]) <= 6:
            tk = raw  # già col suffix
        else:
            tk = raw.split()[0].upper() + suffix
        out.append({
            "ticker": tk,
            "name": str(row[name_col]) if name_col else "",
            "sector": str(row[sec_col]) if sec_col else "",
            "region": suffix.lstrip("."),  # MI, PA, DE
            "exchange": index_name,
            "index": index_name,
        })
    log.info(f"{index_name}: {len(out)}")
    return out


def build_universe() -> dict:
    """Merge tutti gli indici, dedup, salva."""
    sources = []
    for fn in [
        get_sp500,
        get_ndx100,
        get_russell1000,
        lambda: get_european("FTSEMIB", "ftsemib", ".MI"),
        lambda: get_european("CAC40", "cac40", ".PA"),
        lambda: get_european("DAX", "dax", ".DE"),
    ]:
        try:
            sources.extend(fn())
        except Exception as e:
            log.error(f"Fonte fallita: {e}")

    universe = {}
    for row in sources:
        tk = row["ticker"]
        if not tk:
            continue
        if tk not in universe:
            universe[tk] = {
                "ticker": tk,
                "name": row["name"],
                "sector": row["sector"],
                "region": row["region"],
                "exchange": row.get("exchange", ""),
                "indices": [row["index"]],
            }
        else:
            if row["index"] not in universe[tk]["indices"]:
                universe[tk]["indices"].append(row["index"])

    save_json(DATA_DIR / "universe.json", universe)
    log.info(f"Universo totale unico: {len(universe)}")
    return universe


if __name__ == "__main__":
    build_universe()
