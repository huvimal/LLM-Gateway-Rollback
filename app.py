from functools import lru_cache
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway import Gateway, simulate

app = FastAPI(title="LLM Gateway & Rollback", version="0.1.0")


@lru_cache
def database():
    return Gateway()


class RequestBody(BaseModel):
    app: Literal["support", "research"]
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    fail_canary: bool = False


@app.post("/simulate")
def run_request(body: RequestBody):
    result = simulate(database(), body.app, body.request_id, body.fail_canary)
    status = 429 if result["status"] == "rate_limit_exceeded" else 402 if result["status"] == "budget_exceeded" else 502 if result["status"] == "error" else 200
    return JSONResponse({"mode": "simulation", **result}, status_code=status)


@app.get("/metrics")
def metrics():
    return database().metrics()
