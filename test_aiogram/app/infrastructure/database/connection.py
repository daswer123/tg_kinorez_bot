"""PostgreSQL database connection manager."""

import asyncpg
import logging
from typing import Optional

from app.core.config import settings

# Module logger
logger = logging.getLogger(__name__)

# Global connection pool
_pool: Optional[asyncpg.Pool] = None

async def get_database_pool() -> asyncpg.Pool:
    """
    Get or create a connection pool to the PostgreSQL database.
    
    Returns:
        asyncpg.Pool: Connection pool to the PostgreSQL database
    """
    global _pool
    
    if _pool is None:
        try:
            logger.info("Creating PostgreSQL connection pool...")
            _pool = await asyncpg.create_pool(
                dsn=settings.postgres_dsn,
                min_size=2,
                max_size=10,
            )
            
            # Initialize database schema if needed
            await _initialize_schema()
            
            logger.info("PostgreSQL connection pool created successfully")
        except Exception as e:
            logger.critical(f"Failed to create PostgreSQL connection pool: {e}")
            raise
    
    return _pool

async def close_database_pool() -> None:
    """Close the PostgreSQL connection pool."""
    global _pool
    
    if _pool is not None:
        logger.info("Closing PostgreSQL connection pool...")
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")

async def _initialize_schema() -> None:
    """Initialize the database schema if needed."""
    pool = await get_database_pool()
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_authorized BOOLEAN DEFAULT FALSE,
                    is_waiting_for_password BOOLEAN DEFAULT FALSE,
                    password_attempts INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    last_attempt_at TIMESTAMP WITH TIME ZONE
                )
            ''')
            
            # Create requests table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    request_text TEXT,
                    video_url TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    status TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            logger.info("Database schema initialized") 