from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Request

from server.api.models import HeartbeatRequest, HeartbeatResponse
from server.api.auth import TokenAuth, resolve_device_or_raise
from server.api.database import init_db, ensure_device, record_heartbeat, get_active_config
from server.api.commands import get_pending_commands, mark_delivered
from server.api.events import router as events_router
from server.api.config import router as config_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./bearwatch.db")
    db_path = db_url.replace("sqlite:///", "")
    init_db(db_path)

    app.state.db_path = db_path
    app.state.auth = TokenAuth.from_env()
    app.state.media_root = os.environ.get("MEDIA_ROOT", "./media")

    if os.environ.get("SKIP_MODEL_LOAD") != "1":
        from server.api.inference import load_model
        load_model()

    yield


app = FastAPI(title="BearWatch", lifespan=lifespan)
app.include_router(events_router, prefix="/api")
app.include_router(config_router, prefix="/api")


@app.post("/api/v1/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(
    request: Request,
    body: HeartbeatRequest,
    authorization: str | None = Header(default=None),
):
    device_id = resolve_device_or_raise(request, authorization)
    db_path = request.app.state.db_path

    if body.device_info:
        ensure_device(db_path, device_id, body.device_info)
    else:
        ensure_device(db_path, device_id)

    record_heartbeat(
        db_path, device_id,
        body.battery_pct, body.battery_temp_c, body.uptime_s,
        body.config_etag, body.clock_offset_ms,
        body.metrics,
    )

    server_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    config = None
    config_etag = None
    active = get_active_config(db_path, device_id)
    if active is not None:
        cfg, etag = active
        if etag != body.config_etag:
            config = cfg
            config_etag = etag

    pending = get_pending_commands(db_path, device_id)
    if pending:
        mark_delivered(db_path, [c["id"] for c in pending])
    commands = [
        {k: v for k, v in c.items() if k != "id"}
        for c in pending
    ]

    return HeartbeatResponse(
        status="ok",
        server_time=server_time,
        config=config,
        config_etag=config_etag,
        commands=commands,
    )
