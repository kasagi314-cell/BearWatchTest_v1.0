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
