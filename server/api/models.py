from __future__ import annotations

from pydantic import BaseModel


class HeartbeatRequest(BaseModel):
    battery_pct: int
    battery_temp_c: float
    uptime_s: int
    config_etag: str
    clock_offset_ms: int
    device_info: dict | None = None
    metrics: dict | None = None


class HeartbeatResponse(BaseModel):
    status: str
    server_time: str | None = None
    config: dict | None = None
    config_etag: str | None = None
    commands: list[dict] = []


class EventData(BaseModel):
    event_id: str
    device_id: str | None = None
    detected_at: str
    clock_offset_ms: int | None = None
    camera: str | None = None
    roi: dict | None = None
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    estimated_distance_m: float | None = None
    estimated_size_m: float | None = None
    track: dict | None = None
    env: dict | None = None
    scores: dict | None = None


class EventResponse(BaseModel):
    event_id: str
    status: str
    s4_result: dict | None = None
