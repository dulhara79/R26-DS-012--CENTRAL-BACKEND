"""
Central configuration.

THIS IS THE ONLY FILE YOU EDIT WHEN THE HUGGING FACE SPACES GO LIVE.
Actually — you don't even edit this file. You edit `.env`.

Rule: if a service's BASE_URL is blank, that service runs in MOCK mode and the
backend still works end to end. Paste a real URL in and it switches to the real
service on next restart. Nothing else changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# .env loading (no python-dotenv dependency)
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(ROOT / ".env")


def _s(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _i(key: str, default: int) -> int:
    try:
        return int(_s(key) or default)
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(_s(key) or default)
    except ValueError:
        return default


def _b(key: str, default: bool) -> bool:
    v = _s(key).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Model services
# --------------------------------------------------------------------------
@dataclass
class ServiceConfig:
    key: str            # c1 | c2 | c4 | fusion | c3
    label: str
    base_url: str       # blank => mock mode
    predict_path: str
    health_path: str = "/health"

    @property
    def mock(self) -> bool:
        return not self.base_url

    @property
    def predict_url(self) -> str:
        return self.base_url.rstrip("/") + self.predict_path

    @property
    def health_url(self) -> str:
        return self.base_url.rstrip("/") + self.health_path


SERVICES: dict[str, ServiceConfig] = {
    "c1": ServiceConfig("c1", "C1 Physiological (LSTM-AE)",
                        _s("C1_BASE_URL"), _s("C1_PREDICT_PATH", "/predict")),
    "c2": ServiceConfig("c2", "C2 Behavioural (GATv2)",
                        _s("C2_BASE_URL"), _s("C2_PREDICT_PATH", "/predict")),
    "c4": ServiceConfig("c4", "C4 Clinical NLP (TC-WPN)",
                        _s("C4_BASE_URL"), _s("C4_PREDICT_PATH", "/predict")),
    "fusion": ServiceConfig("fusion", "Fusion Service",
                            _s("FUSION_BASE_URL"), _s("FUSION_PREDICT_PATH", "/fuse")),
    "c3": ServiceConfig("c3", "C3 Intervention",
                        _s("C3_BASE_URL"), _s("C3_PREDICT_PATH", "/intervene")),
}

# Modality key used in the wire contract, per service
MODALITY_OF = {
    "c1": "c1_physiological",
    "c2": "c2_behavioral",
    "c4": "c4_clinical_nlp",
    "c3": "c3_intervention",
}

# --------------------------------------------------------------------------
# Everything else
# --------------------------------------------------------------------------
DATABASE_URL = _s("DATABASE_URL", f"sqlite:///{ROOT / 'clinanx.db'}")
JWT_SECRET = _s("JWT_SECRET", "dev-only-change-me")
JWT_ALGO = "HS256"
JWT_TTL_MIN = _i("JWT_TTL_MIN", 720)
MRN_PEPPER = _s("MRN_PEPPER", "dev-only-pepper")
HF_TOKEN = _s("HF_TOKEN")  # server side only — never in an APK

HTTP_TIMEOUT_S = _f("HTTP_TIMEOUT_S", 90.0)      # HF Spaces cold-start slowly
HTTP_RETRIES = _i("HTTP_RETRIES", 2)

# Freshness: how old a reading may be and still count toward the composite
FRESHNESS_SECONDS = {
    "c1_physiological": _i("FRESH_C1_SECONDS", 15 * 60),
    "c2_behavioral": _i("FRESH_C2_SECONDS", 7 * 24 * 3600),
    "c4_clinical_nlp": _i("FRESH_C4_SECONDS", 90 * 24 * 3600),
    "c3_intervention": _i("FRESH_C3_SECONDS", 24 * 3600),
}

# Fusion weights. Frozen and versioned — stored with every result.
FUSION_WEIGHTS_VERSION = _s("FUSION_WEIGHTS_VERSION", "fusion-weights-v1.0.0")

# C3_MODE = "downstream"  -> fusion combines C1/C2/C4; C3 runs after fusion  (recommended)
# C3_MODE = "modality"    -> C3 is a fourth peer score inside fusion
C3_MODE = _s("C3_MODE", "downstream").lower()

if C3_MODE == "modality":
    FUSION_WEIGHTS = {
        "c1_physiological": _f("W_C1", 0.25),
        "c2_behavioral": _f("W_C2", 0.20),
        "c3_intervention": _f("W_C3", 0.15),
        "c4_clinical_nlp": _f("W_C4", 0.40),
    }
else:
    FUSION_WEIGHTS = {
        "c1_physiological": _f("W_C1", 0.31),
        "c2_behavioral": _f("W_C2", 0.24),
        "c4_clinical_nlp": _f("W_C4", 0.45),
    }

MIN_MODALITIES = _i("MIN_MODALITIES", 2)
RENORMALISE_ON_MISSING = _b("RENORMALISE_ON_MISSING", True)

# Bands, per proposal section 5.1
BANDS = [(0.25, "GREEN"), (0.50, "AMBER"), (0.75, "RED"), (1.01, "DARK_RED")]
PATIENT_BAND_LABEL = {
    "GREEN": "STEADY", "AMBER": "SLIGHTLY_ELEVATED",
    "RED": "ELEVATED", "DARK_RED": "HIGH",
}

# C2 needs 42 days x 4 windows before it can build a graph
C2_DAYS_REQUIRED = _i("C2_DAYS_REQUIRED", 42)
# What the C2 mock reports once it has enough history.
# "not_validated" (default) => stored and displayed, excluded from composite.
# "ok"                      => counted in the composite.
C2_MOCK_STATUS = _s("C2_MOCK_STATUS", "not_validated")

APP_NAME = "R26-DS-012 Central Backend"
APP_VERSION = "0.1.0"


def band_for(score: float) -> str:
    for ceiling, name in BANDS:
        if score < ceiling:
            return name
    return "DARK_RED"


def mock_summary() -> dict:
    return {k: ("MOCK" if s.mock else s.base_url) for k, s in SERVICES.items()}
