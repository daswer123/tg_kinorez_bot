"""Authentication middleware and decorators for Aiogram."""

import logging
from typing import Callable, Dict, Any, Awaitable, Optional, Union, List
from functools import wraps

from aiogram import types, Router
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Filter

from app.infrastructure.database.auth_db import AuthDB
from app.core.config import settings

# Module logger
logger = logging.getLogger(__name__)

# Authentication database instance
auth_db = AuthDB()

# Define a list of Telegram user IDs that are always authorized (admin users)
ADMIN_USERS: List[int] = []

class IsAuthorizedFilter(Filter):
    """Filter to check if a user is authorized."""
    
    async def __call__(self, message: Message) -> bool:
        """
        Check if user is authorized.
        
        Args:
            message (Message): Incoming message
            
        Returns:
            bool: True if user is authorized, False otherwise
        """
        user_id = message.from_user.id
        
        # Admin users are always authorized
        if user_id in ADMIN_USERS:
            return True
        
        # Check in database
        is_authorized = await auth_db.is_user_authorized(user_id)
        return is_authorized

class NotAuthorizedFilter(Filter):
    """Filter to check if a user is not authorized."""
    
    async def __call__(self, message: Message) -> bool:
        """
        Check if user is not authorized.
        
        Args:
            message (Message): Incoming message
            
        Returns:
            bool: True if user is not authorized, False otherwise
        """
        user_id = message.from_user.id
        
        # Admin users are always authorized
        if user_id in ADMIN_USERS:
            return False
        
        # Check in database
        is_authorized = await auth_db.is_user_authorized(user_id)
        return not is_authorized

class IsWaitingForPasswordFilter(Filter):
    """Filter to check if a user is waiting for password input."""
    
    async def __call__(self, message: Message) -> bool:
        """
        Check if user is waiting for password input.
        
        Args:
            message (Message): Incoming message
            
        Returns:
            bool: True if user is waiting for password, False otherwise
        """
        user_id = message.from_user.id
        
        # Admin users are never waiting for password
        if user_id in ADMIN_USERS:
            return False
        
        # Check in database
        is_waiting = await auth_db.is_waiting_for_password(user_id)
        return is_waiting

class IsAdminFilter(Filter):
    """Filter to check if a user is an admin."""
    
    async def __call__(self, message: Message) -> bool:
        """
        Check if user is an admin.
        
        Args:
            message (Message): Incoming message
            
        Returns:
            bool: True if user is an admin, False otherwise
        """
        user_id = message.from_user.id
        return user_id in ADMIN_USERS

async def save_user_info(
    user: types.User, 
    is_authorized: bool = False, 
    is_waiting_for_password: bool = False
) -> None:
    """
    Save or update user information in the database.
    
    Args:
        user (types.User): User object
        is_authorized (bool, optional): Authorization status
        is_waiting_for_password (bool, optional): Whether user is waiting for password
    """
    await auth_db.add_or_update_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_authorized=is_authorized,
        is_waiting_for_password=is_waiting_for_password
    )

async def set_waiting_for_password(user_id: int, waiting: bool = True) -> bool:
    """
    Set user as waiting or not waiting for password input.
    
    Args:
        user_id (int): Telegram user ID
        waiting (bool): Whether the user is waiting for password
        
    Returns:
        bool: True if operation was successful, False otherwise
    """
    return await auth_db.set_waiting_for_password(user_id, waiting)

async def check_password(user_id: int, password: str) -> bool:
    """
    Check if password is correct and authorize user if it is.
    
    Args:
        user_id (int): Telegram user ID
        password (str): Entered password
        
    Returns:
        bool: True if password is correct, False otherwise
    """
    return await auth_db.check_password(user_id, password, settings.auth_password.get_secret_value())

async def get_password_attempts(user_id: int) -> int:
    """
    Get number of password attempts for a user.
    
    Args:
        user_id (int): Telegram user ID
        
    Returns:
        int: Number of password attempts
    """
    return await auth_db.get_password_attempts(user_id)

async def log_user_request(
    user_id: int, 
    request_text: str, 
    video_url: Optional[str] = None, 
    start_time: Optional[str] = None, 
    end_time: Optional[str] = None, 
    status: str = "processing"
) -> Optional[int]:
    """
    Log user request in the database.
    
    Args:
        user_id (int): Telegram user ID
        request_text (str): Request text
        video_url (str, optional): Video URL
        start_time (str, optional): Start time of fragment
        end_time (str, optional): End time of fragment
        status (str, optional): Request status
        
    Returns:
        int or None: Request ID or None if operation failed
    """
    return await auth_db.log_request(
        user_id=user_id,
        request_text=request_text,
        video_url=video_url,
        start_time=start_time,
        end_time=end_time,
        status=status
    )

async def authorize_user(user_id: int) -> bool:
    """
    Authorize user in the database.
    
    Args:
        user_id (int): Telegram user ID
        
    Returns:
        bool: True if operation was successful, False otherwise
    """
    return await auth_db.authorize_user(user_id)

# Define the authentication router with auth filters
auth_router = Router(name="auth_router")
password_router = Router(name="password_router")

# Register the authentication filters
auth_router.message.filter(IsAuthorizedFilter())
auth_router.callback_query.filter(IsAuthorizedFilter())
password_router.message.filter(IsWaitingForPasswordFilter()) 