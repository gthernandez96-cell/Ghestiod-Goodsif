import redis.asyncio as aioredis
from app.core.config import settings
from typing import Optional
import json
import logging

logger = logging.getLogger(__name__)

# Redis client (async)
redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return redis_client


async def cache_set(key: str, value: any, ttl: int = 300) -> bool:
    """Guardar en caché con TTL en segundos."""
    try:
        r = await get_redis()
        await r.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception as e:
        logger.warning(f"Cache set error [{key}]: {e}")
        return False


async def cache_get(key: str) -> Optional[any]:
    """Obtener desde caché."""
    try:
        r = await get_redis()
        data = await r.get(key)
        return json.loads(data) if data else None
    except Exception as e:
        logger.warning(f"Cache get error [{key}]: {e}")
        return None


async def cache_delete(key: str) -> bool:
    """Eliminar clave de caché."""
    try:
        r = await get_redis()
        await r.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Cache delete error [{key}]: {e}")
        return False


async def cache_delete_pattern(pattern: str) -> int:
    """Eliminar todas las claves que coincidan con un patrón."""
    try:
        r = await get_redis()
        keys = await r.keys(pattern)
        if keys:
            return await r.delete(*keys)
        return 0
    except Exception as e:
        logger.warning(f"Cache delete pattern error [{pattern}]: {e}")
        return 0


async def publish_event(channel: str, data: dict) -> int:
    """Publicar evento para WebSockets / Pub-Sub."""
    try:
        r = await get_redis()
        return await r.publish(channel, json.dumps(data, default=str))
    except Exception as e:
        logger.warning(f"Publish error [{channel}]: {e}")
        return 0
