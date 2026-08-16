from __future__ import annotations

from pydantic import BaseModel


class HeartbeatRequest(BaseModel):
    battery_pct: int
    battery_temp_c: float
    uptime_s: int
    config_etag: str
    clock_offset_ms: int
    device_info: dict | None = None


class HeartbeatResponse(BaseModel):
    status: str
    config: dict | None = None
    config_etag: str | None = None
