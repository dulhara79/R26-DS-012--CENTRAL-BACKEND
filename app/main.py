from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .db import init_db
from .routers import auth, clinician, ops, patient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("central_backend")

app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION,
              description="Single trust boundary for the R26-DS-012 multimodal system.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router)
app.include_router(patient.router)
app.include_router(clinician.router)
app.include_router(ops.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("service wiring: %s", config.mock_summary())
    mocked = [k for k, s in config.SERVICES.items() if s.mock]
    if mocked:
        log.warning("MOCK MODE for: %s  (set the *_BASE_URL in .env to go live)",
                    ", ".join(mocked))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": config.APP_NAME, "version": config.APP_VERSION,
            "services": config.mock_summary()}


@app.get("/")
def root() -> dict:
    return {"service": config.APP_NAME, "docs": "/docs", "health": "/health"}
