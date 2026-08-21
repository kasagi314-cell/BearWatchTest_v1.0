"""イベント受信エンドポイント"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, Request

from server.api.models import EventResponse
from server.api.auth import resolve_device_or_raise
from server.api.database import (
    insert_event, get_event, update_event_status,
    update_event_scores, update_event_media_path,
)
from server.api.commands import enqueue_command
from server.api.inference import run_s4, S4Result
from server.api.metrics import record_metric

router = APIRouter()


@router.post("/v1/events", response_model=EventResponse)
async def post_event(
    request: Request,
    event: str = Form(...),
    thumbnail: UploadFile | None = File(default=None),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)
    event_data = json.loads(event)
    event_data["device_id"] = device_id

    # べき等チェック
    existing = get_event(db, event_data["event_id"])
    if existing:
        return EventResponse(event_id=existing["event_id"], status=existing["status"])

    insert_event(db, event_data)
    record_metric(db, "events_received")

    # サムネイル保存
    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_data["event_id"])
    os.makedirs(event_dir, exist_ok=True)

    if thumbnail:
        thumb_path = os.path.join(event_dir, "thumbnail.jpg")
        content = await thumbnail.read()
        with open(thumb_path, "wb") as f:
            f.write(content)
        record_metric(db, "media_bytes_received", len(content))

    # S4 推論
    record_metric(db, "s4_input_count")
    s4_image = os.path.join(event_dir, "thumbnail.jpg") if thumbnail else None

    if s4_image and os.path.exists(s4_image):
        s4_result = run_s4(s4_image)
    else:
        s4_result = S4Result(is_animal=True, score=0.0, error="no thumbnail")

    # スコア更新
    scores = event_data.get("scores", {"s3": None, "s4": None, "s5": None})
    scores["s4"] = s4_result.score
    update_event_scores(db, event_data["event_id"], scores)
    record_metric(db, "s4_score_sum", s4_result.score)
    record_metric(db, "s4_score_count")

    # コマンド発行 + 状態遷移
    if s4_result.is_animal:
        enqueue_command(db, device_id, "request_video", {"event_id": event_data["event_id"]})
        update_event_status(db, event_data["event_id"], "MEDIA_REQUESTED")
        record_metric(db, "s4_animal_count")
        status = "MEDIA_REQUESTED"
    else:
        enqueue_command(db, device_id, "discard", {"event_id": event_data["event_id"]})
        update_event_status(db, event_data["event_id"], "SERVER_REJECTED")
        record_metric(db, "s4_non_animal_count")
        status = "SERVER_REJECTED"

    if s4_result.error:
        record_metric(db, "s4_error_count")

    return EventResponse(
        event_id=event_data["event_id"],
        status=status,
        s4_result={"is_animal": s4_result.is_animal, "score": s4_result.score},
    )


@router.post("/v1/events/{event_id}/still")
async def post_still(
    request: Request,
    event_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    evt = get_event(db, event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")
    if evt["device_id"] != device_id:
        raise HTTPException(status_code=403, detail="Event belongs to another device")

    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_id)
    os.makedirs(event_dir, exist_ok=True)

    still_path = os.path.join(event_dir, "still.jpg")
    content = await file.read()
    with open(still_path, "wb") as f:
        f.write(content)

    rel_path = f"{device_id}/{event_id}/still.jpg"
    update_event_media_path(db, event_id, "still", rel_path)
    record_metric(db, "media_bytes_received", len(content))

    return {"event_id": event_id, "still_path": rel_path}


@router.post("/v1/events/{event_id}/video")
async def post_video(
    request: Request,
    event_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    db = request.app.state.db_path
    device_id = resolve_device_or_raise(request, authorization)

    evt = get_event(db, event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")
    if evt["device_id"] != device_id:
        raise HTTPException(status_code=403, detail="Event belongs to another device")

    media_root = request.app.state.media_root
    event_dir = os.path.join(media_root, device_id, event_id)
    os.makedirs(event_dir, exist_ok=True)

    video_path = os.path.join(event_dir, "video.mp4")
    content = await file.read()
    with open(video_path, "wb") as f:
        f.write(content)

    rel_path = f"{device_id}/{event_id}/video.mp4"
    update_event_media_path(db, event_id, "video", rel_path)
    update_event_status(db, event_id, "MEDIA_UPLOADED")
    record_metric(db, "media_bytes_received", len(content))

    return {"event_id": event_id, "video_path": rel_path, "status": "MEDIA_UPLOADED"}
