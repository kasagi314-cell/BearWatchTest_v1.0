from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from server.api.models import HeartbeatRequest, HeartbeatResponse
from server.api.auth import TokenAuth
from server.api.database import init_db, ensure_device, record_heartbeat, get_active_config

_db_path: str = ""
_auth: TokenAuth = TokenAuth({})


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_path, _auth
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./bearwatch.db")
    _db_path = db_url.replace("sqlite:///", "")
    init_db(_db_path)
    _auth = TokenAuth.from_env()
    yield


app = FastAPI(title="BearWatch", lifespan=lifespan)


def _resolve_device(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    device_id = _auth.resolve(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Unknown token")
    return device_id


@app.post("/api/v1/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(
    body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
):
    device_id = _resolve_device(authorization)

    if body.device_info:
        ensure_device(_db_path, device_id, body.device_info)
    else:
        ensure_device(_db_path, device_id)

    record_heartbeat(
        _db_path, device_id,
        body.battery_pct, body.battery_temp_c, body.uptime_s,
        body.config_etag, body.clock_offset_ms,
    )

    active = get_active_config(_db_path, device_id)
    if active is not None:
        config, etag = active
        if etag != body.config_etag:
            return HeartbeatResponse(status="ok", config=config, config_etag=etag)

    return HeartbeatResponse(status="ok")
