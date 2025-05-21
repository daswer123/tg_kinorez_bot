import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.types import TelegramObject, ErrorEvent
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Import our settings instance
from app.core.config import settings
from app.handlers import main_handlers_router  # Import main router
from app.infrastructure.database import get_database_pool, close_database_pool
from app.infrastructure.redis import get_redis_connection, close_redis_connection
from app.infrastructure.database.middleware import ADMIN_USERS

# Module logger
logger = logging.getLogger(__name__)

# Get admin user IDs from environment
# Format: ADMIN_USERS=1234567,7654321
admin_ids_str = os.getenv("ADMIN_USERS", "")
if admin_ids_str.strip():
    try:
        # Parse comma-separated list of admin IDs
        admin_ids = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip().isdigit()]
        ADMIN_USERS.extend(admin_ids)
        logger.info(f"Configured admin users: {ADMIN_USERS}")
    except Exception as e:
        logger.error(f"Error parsing admin user IDs: {e}")

# Create session using settings from config
session = AiohttpSession(api=settings.telegram_api_server)

# Create bot instance
bot = Bot(
    token=settings.telegram_token.get_secret_value(),
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

# Monkey patch для добавления отсутствующего метода get_chat_history
async def get_chat_history(self, chat_id, limit=100, *args, **kwargs):
    """
    Эмуляция метода get_chat_history для совместимости.
    В aiogram нет прямого аналога, поэтому возвращаем пустой список или
    используем другой доступный метод для получения сообщений.
    """
    logger.warning(f"Using emulated get_chat_history method for chat {chat_id} (limit: {limit})")
    # Возвращаем пустой список или можно реализовать через другие методы API
    return []

# Применяем патч к классу Bot
Bot.get_chat_history = get_chat_history

# Create dispatcher
dp = Dispatcher()

# Error handler для отлова ошибок, связанных с get_chat_history
@dp.errors()
async def errors_handler(event: ErrorEvent):
    exception = event.exception
    if "get_chat_history" in str(exception):
        import traceback
        logger.error(f"get_chat_history error:\n{traceback.format_exc()}")
        # Можно попытаться вернуть какой-то результат, чтобы процесс не останавливался
        return True
    logger.error(f"{exception}", exc_info=True)
    return False

# Define startup handler
async def on_startup():
    """Actions to perform when the bot starts up: initialize database and Redis."""
    logger.info("Bot starting up...")
    
    # Initialize database connection
    await get_database_pool()
    logger.info("Database connection initialized.")
    
    # Initialize Redis connection
    await get_redis_connection()
    logger.info("Redis connection initialized.")

# Define shutdown handler
async def on_shutdown():
    """Actions to perform when the bot shuts down: close connections."""
    logger.warning("Bot shutting down...")
    
    # Close the database connection
    await close_database_pool()
    logger.warning("Database connection closed.")
    
    # Close the Redis connection
    await close_redis_connection()
    logger.warning("Redis connection closed.")
    
    # Close the bot session
    await bot.session.close()
    logger.warning("Bot session closed.")

# Register routers and lifecycle handlers
dp.include_router(main_handlers_router)  # Include all our handlers
dp.startup.register(on_startup)  # Register startup handler
dp.shutdown.register(on_shutdown) 