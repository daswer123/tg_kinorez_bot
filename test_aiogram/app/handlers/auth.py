"""Authentication handlers for the bot."""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.infrastructure.database.middleware import (
    IsAuthorizedFilter, NotAuthorizedFilter, IsWaitingForPasswordFilter,
    save_user_info, authorize_user, set_waiting_for_password, check_password, get_password_attempts
)
from app.core.config import settings

# Module logger
logger = logging.getLogger(__name__)

# Create routers
auth_router = Router(name="auth_router")
non_auth_router = Router(name="non_auth_router")
admin_router = Router(name="admin_router")
password_router = Router(name="password_router")

# Add filters to routers
auth_router.message.filter(IsAuthorizedFilter())
non_auth_router.message.filter(NotAuthorizedFilter())
password_router.message.filter(IsWaitingForPasswordFilter())

# --- Password handling ---

@password_router.message()
async def handle_password(message: Message):
    """
    Handle password input from users waiting for password.
    
    Args:
        message (Message): Incoming message with password
    """
    user = message.from_user
    user_id = user.id
    password_attempt = message.text
    
    # Check if password is correct
    is_correct = await check_password(user_id, password_attempt)
    
    if is_correct:
        # Password is correct, send success message
        await message.answer(
            "✅ Авторизация успешна! Теперь вы можете использовать все функции бота."
        )
    else:
        # Password is incorrect
        # Get the number of attempts
        attempts = await get_password_attempts(user_id)
        
        # Send error message
        await message.answer(
            f"❌ Неверный пароль. Пожалуйста, попробуйте снова.\n"
            f"Количество попыток: {attempts}"
        )

# --- Non-authorized users handlers ---

@non_auth_router.message(Command("start"))
async def cmd_start_non_auth(message: Message):
    """
    Handler for /start command from non-authorized users.
    
    Args:
        message (Message): Incoming message with command
    """
    user = message.from_user
    
    # Save user information to database
    await save_user_info(user)
    
    if settings.auth_enabled:
        # Create keyboard with authorization request button
        builder = InlineKeyboardBuilder()
        builder.button(text="Запросить доступ", callback_data="request_auth")
        
        # Send welcome message with auth request button
        await message.answer(
            f"Привет, {user.first_name}! "
            f"Вы еще не авторизованы в системе. "
            f"Для получения доступа к боту, нажмите на кнопку ниже:",
            reply_markup=builder.as_markup()
        )
    else:
        # If auth is disabled, authorize the user automatically
        await authorize_user(user_id=user.id)
        await message.answer(
            f"Привет, {user.first_name}! "
            f"Вы авторизованы и можете использовать все функции бота."
        )

@non_auth_router.callback_query(F.data == "request_auth")
async def request_authorization(callback: CallbackQuery):
    """
    Handler for authorization request button.
    
    Args:
        callback (CallbackQuery): Callback query from the button
    """
    user = callback.from_user
    user_id = user.id
    
    # Mark user as waiting for password
    await save_user_info(user, is_authorized=False, is_waiting_for_password=True)
    
    # Log the authorization request
    logger.info(f"User {user_id} ({user.username or user.first_name}) requested authorization")
    
    # Update answer text
    await callback.message.edit_text(
        f"Для авторизации введите пароль:"
    )
    
    # Answer the callback query
    await callback.answer("Введите пароль")

# --- Authorized users handlers ---

@auth_router.message(Command("start"))
async def cmd_start_auth(message: Message):
    """
    Handler for /start command from authorized users.
    
    Args:
        message (Message): Incoming message with command
    """
    user = message.from_user
    
    # Update user information in database
    await save_user_info(user, is_authorized=True)
    
    # Send welcome message
    await message.answer(
        f"Привет, {user.first_name}! "
        f"Вы авторизованы и можете использовать все функции бота."
    )

# --- Admin commands ---

@admin_router.message(Command("users"))
async def cmd_users(message: Message):
    """
    Handler for /users command (admin only).
    Shows list of users with ability to authorize them.
    
    Args:
        message (Message): Incoming message with command
    """
    # Get DB instance
    from app.infrastructure.database.middleware import auth_db
    
    # Get all users
    users = await auth_db.get_all_users()
    
    if not users:
        await message.answer("В базе данных нет пользователей.")
        return
    
    # Send user list
    await message.answer(f"Список пользователей ({len(users)}):")
    
    for user in users[:20]:  # Limit to 20 users
        # Create authorize button for non-authorized users
        builder = InlineKeyboardBuilder()
        if not user['is_authorized']:
            builder.button(
                text="Авторизовать",
                callback_data=f"auth_user_{user['user_id']}"
            )
        
        # Format user info
        username = user['username'] or "Нет username"
        first_name = user['first_name'] or "Нет имени"
        last_name = user['last_name'] or ""
        full_name = f"{first_name} {last_name}".strip()
        status = "✅ авторизован" if user['is_authorized'] else "❌ не авторизован"
        
        # Add password attempts info if any
        password_info = ""
        if user['password_attempts'] and user['password_attempts'] > 0:
            password_info = f"\nПопыток ввода пароля: {user['password_attempts']}"
            if user['last_attempt_at']:
                password_info += f"\nПоследняя попытка: {user['last_attempt_at'].strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Add waiting for password status if true
        waiting_info = "\nОжидает ввода пароля: Да" if user['is_waiting_for_password'] else ""
        
        # Send user info message
        await message.answer(
            f"ID: {user['user_id']}\n"
            f"Username: @{username}\n"
            f"Имя: {full_name}\n"
            f"Статус: {status}"
            f"{password_info}"
            f"{waiting_info}",
            reply_markup=builder.as_markup() if not user['is_authorized'] else None
        )

@admin_router.callback_query(F.data.startswith("auth_user_"))
async def authorize_user_callback(callback: CallbackQuery):
    """
    Handler for authorizing users from the admin panel.
    
    Args:
        callback (CallbackQuery): Callback query from the button
    """
    # Extract user_id from callback data
    user_id = int(callback.data.split("_")[2])
    
    # Authorize user
    success = await authorize_user(user_id)
    
    if success:
        # Update message text
        await callback.message.edit_text(
            callback.message.text.replace("❌ не авторизован", "✅ авторизован"),
            reply_markup=None
        )
        
        # Try to notify the user about successful authorization
        try:
            from app.bot import bot
            await bot.send_message(
                user_id,
                "Поздравляем! Ваш аккаунт был авторизован администратором. "
                "Теперь вы можете использовать все функции бота."
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} about authorization: {e}")
        
        await callback.answer("Пользователь успешно авторизован!")
    else:
        await callback.answer("Ошибка при авторизации пользователя!")

@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    """
    Handler for /stats command (admin only).
    Shows bot usage statistics.
    
    Args:
        message (Message): Incoming message with command
    """
    # Get DB instance
    from app.infrastructure.database.middleware import auth_db
    
    # Get statistics
    stats = await auth_db.get_total_statistics()
    
    # Format statistics
    stats_text = (
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"✅ Авторизованных пользователей: {stats['authorized_users']}\n"
        f"🔄 Всего запросов: {stats['total_requests']}\n"
        f"✅ Успешных запросов: {stats['completed_requests']}\n"
        f"❌ Запросов с ошибками: {stats['error_requests']}\n\n"
    )
    
    if stats["recent_requests"]:
        stats_text += "🕒 Последние запросы:\n\n"
        
        for i, req in enumerate(stats["recent_requests"][:5], 1):
            username = req['username'] or req['first_name'] or f"User {req['user_id']}"
            status_emoji = "✅" if req['status'] == "completed" else "⏳" if req['status'] == "processing" else "❌"
            stats_text += f"{i}. {status_emoji} @{username}: {req['request_text'][:50]}...\n"
    
    await message.answer(stats_text) 