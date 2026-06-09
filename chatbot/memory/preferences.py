from __future__ import annotations

import json

import structlog

logger = structlog.get_logger(__name__)


class PreferencesManager:
    async def upsert(
        self,
        db_pool,
        redis,
        user_id: str,
        community_id: str,
        key: str,
        value: str,
        confidence: float = 1.0,
    ) -> None:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    '''
                    INSERT INTO user_preferences
                        (user_id, community_id, preference_key, preference_value, confidence, last_observed_at)
                    VALUES ($1, $2, $3, $4, $5, now())
                    ON CONFLICT (user_id, preference_key)
                    DO UPDATE SET
                        preference_value = EXCLUDED.preference_value,
                        confidence = EXCLUDED.confidence,
                        last_observed_at = now()
                    ''',
                    user_id,
                    community_id,
                    key,
                    value,
                    confidence,
                )
            # Invalidate Redis cache
            await redis.delete(f'prefs:{user_id}')
        except Exception as exc:
            logger.error('preferences_upsert_failed', user_id=user_id, key=key, error=str(exc))

    async def get_all(
        self,
        db_pool,
        redis,
        user_id: str,
    ) -> dict[str, str]:
        try:
            # Check Redis cache first
            cached = await redis.get(f'prefs:{user_id}')
            if cached:
                return json.loads(cached)

            # Load from Postgres
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    'SELECT preference_key, preference_value FROM user_preferences WHERE user_id = $1',
                    user_id,
                )
            result = {row['preference_key']: row['preference_value'] for row in rows}

            # Cache with 24h TTL
            from chatbot.config import settings
            await redis.set(f'prefs:{user_id}', json.dumps(result), ex=settings.PREFERENCES_CACHE_TTL)
            return result
        except Exception as exc:
            logger.error('preferences_get_failed', user_id=user_id, error=str(exc))
            return {}
