"""Fixed-window rate limiter backed by Redis."""
from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


class RateLimiter:
    def __init__(self, client: redis.Redis, prefix: str = "rl"):
        self.r = client
        self.prefix = prefix

    def hit(self, bucket: str, limit: int, window_s: int) -> bool:
        """Record one attempt. Returns True if still within the limit."""
        key = f"{self.prefix}:{bucket}"
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_s, nx=True)
        count, _ = pipe.execute()
        return int(count) <= limit

    def is_blocked(self, bucket: str, limit: int) -> bool:
        val = self.r.get(f"{self.prefix}:{bucket}")
        return val is not None and int(val) >= limit

    def reset(self, bucket: str) -> None:
        self.r.delete(f"{self.prefix}:{bucket}")
