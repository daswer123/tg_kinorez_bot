"""Redis connection manager."""

import logging
import redis.asyncio as redis
from typing import Optional

from app.core.config import settings

# Module logger
logger = logging.getLogger(__name__)

# Global connection pool
_redis: Optional[redis.Redis] = None

async def get_redis_connection() -> redis.Redis:
    """
    Get or create a connection to Redis.
    
    Returns:
        redis.Redis: Connection to Redis
    """
    global _redis
    
    if _redis is None:
        try:
            logger.info("Creating Redis connection...")
            _redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password.get_secret_value() if settings.redis_password else None,
                decode_responses=True,  # Return strings instead of bytes
            )
            
            # Test connection
            await _redis.ping()
            
            logger.info("Redis connection created successfully")
        except Exception as e:
            logger.critical(f"Failed to create Redis connection: {e}")
            raise
    
    return _redis

async def close_redis_connection() -> None:
    """Close the Redis connection."""
    global _redis
    
    if _redis is not None:
        logger.info("Closing Redis connection...")
        await _redis.close()
        _redis = None
        logger.info("Redis connection closed") 