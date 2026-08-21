"""設定取得エンドポイント"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from server.api.auth import resolve_device_or_raise
from server.api.database import get_active_config

router = APIRouter()


@router.get("/v1/config")
def get_config(
    request: Request,
    authorization: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    active = get_active_config(db, device_id)
    if active is None:
        raise HTTPException(status_code=404, detail="No config available")

    config, etag = active
    if if_none_match and if_none_match == etag:
        return Response(status_code=304)

    return JSONResponse(content=config, headers={"ETag": etag})
