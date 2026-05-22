"""Small in-process cache for public dashboard responses."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class CacheEntry:
    expires_at: float
    status_code: int
    headers: dict[str, str]
    body: bytes


class TTLResponseCache:
    def __init__(self, default_ttl: int = 45, max_entries: int = 256):
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._items: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> CacheEntry | None:
        now = time.time()
        with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._items.pop(key, None)
                return None
            return entry

    def set(self, key: str, status_code: int, headers: dict[str, str], body: bytes, ttl: int | None = None) -> None:
        expires_at = time.time() + (ttl or self.default_ttl)
        with self._lock:
            if len(self._items) >= self.max_entries:
                oldest_key = min(self._items, key=lambda item: self._items[item].expires_at)
                self._items.pop(oldest_key, None)
            self._items[key] = CacheEntry(expires_at=expires_at, status_code=status_code, headers=headers, body=body)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


response_cache = TTLResponseCache()


def clear_public_cache() -> None:
    response_cache.clear()
