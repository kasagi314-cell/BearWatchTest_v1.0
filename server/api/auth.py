from __future__ import annotations

import os


class TokenAuth:
    def __init__(self, token_map: dict[str, str]):
        self._map = dict(token_map)

    def resolve(self, token: str) -> str | None:
        return self._map.get(token)

    @classmethod
    def from_env(cls) -> TokenAuth:
        raw = os.environ.get("DEVICE_TOKENS", "")
        token_map: dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                tok, dev = pair.split(":", 1)
                token_map[tok.strip()] = dev.strip()
        return cls(token_map)


from fastapi import HTTPException, Request


def resolve_device_or_raise(request: Request, authorization: str | None) -> str:
    """Bearer トークンから device_id を解決する。失敗時は 401 を返す。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    device_id = request.app.state.auth.resolve(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Unknown token")
    return device_id
