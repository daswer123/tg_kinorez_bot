import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode

from app.infrastructure.database.middleware import IsAuthorizedFilter
from app.infrastructure.redis.task_manager import TaskManager
# Create logger for this module
logger = logging.getLogger(__name__)

# Create router for start commands
router = Router(name="start-commands")

# Add authorization filter directly to this particular handler
# Note: This is a fallback - auth_router in handlers/__init__.py should catch authorized users first
@router.message(Command("start"), IsAuthorizedFilter())
async def cmd_start(message: Message):
    """Handler for /start command for authorized users."""
    user = message.from_user

    if await TaskManager.is_message_processed(message.message_id, message.chat.id):
        return
    
    # Send welcome message
    await message.reply(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот для извлечения фрагментов из YouTube видео.\n\n"
        "Просто отправь мне ссылку на YouTube видео и укажи таймкоды начала и конца фрагмента.\n"
        "Например: https://www.youtube.com/watch?v=dQw4w9WgXcQ 00:30 01:00",
        parse_mode=ParseMode.HTML
    ) 