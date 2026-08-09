"""Сторож ключей поверх Redis: `SET key <токен> NX EX ttl`.

Освобождение — сравнить-и-удалить одним скриптом. Двумя командами («прочитать,
сравнить, удалить») нельзя: между чтением и удалением ключ успевает протухнуть
и быть перезанятым, а удалить мы успеем уже чужой захват. Lua у Redis
выполняется целиком, без чужих команд между шагами.

Пул приходит общий и процессный: `Redis.from_url` по месту вызова — это
отдельное соединение на каждый захват и `finally` с закрытием, повторённые
столько раз, сколько в проекте замков.
"""

from uuid import uuid4

from redis.asyncio import Redis

# KEYS[1] — ключ, ARGV[1] — наш токен. Удаляем, только если владеем.
_RELEASE_IF_MINE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisKeyGuard:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._release = redis.register_script(_RELEASE_IF_MINE)

    async def claim(self, key: str, ttl_seconds: int) -> str | None:
        token = uuid4().hex
        if await self._redis.set(key, token, nx=True, ex=ttl_seconds):
            return token
        return None

    async def release(self, key: str, token: str) -> None:
        await self._release(keys=[key], args=[token])
