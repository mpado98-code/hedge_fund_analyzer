"""
Utilities condivise: logging, retry, persistence, env management.
"""
import os
import json
import logging
import time
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
    return logger


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(f"Required env var {key} not set")
    return val


def retry(times: int = 3, delay: float = 2.0, exc=(Exception,)):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for i in range(times):
                try:
                    return fn(*args, **kwargs)
                except exc as e:
                    last = e
                    time.sleep(delay * (i + 1))
            raise last
        return wrapper
    return deco


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_div(a, b, default=0.0):
    try:
        if b == 0 or b is None or a is None:
            return default
        return a / b
    except Exception:
        return default


def clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))
