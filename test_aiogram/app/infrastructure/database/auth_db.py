"""PostgreSQL authentication database service."""

import logging
from datetime import datetime
import asyncpg
from typing import List, Dict, Any, Optional, Tuple, Union

from app.infrastructure.database.connection import get_database_pool

# Module logger
logger = logging.getLogger(__name__)

class AuthDB:
    """Class for working with authorized users in PostgreSQL database."""

    def __init__(self):
        """Initialize the database service."""
        self.pool = None
    
    async def get_pool(self) -> asyncpg.Pool:
        """Get database connection pool."""
        if self.pool is None:
            self.pool = await get_database_pool()
        return self.pool

    async def is_user_authorized(self, user_id: int) -> bool:
        """
        Check if user is authorized.
        
        Args:
            user_id (int): Telegram user ID
            
        Returns:
            bool: True if user is authorized, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                result = await conn.fetchval(
                    "SELECT is_authorized FROM users WHERE user_id = $1",
                    user_id
                )
            
            if result is None:
                return False
            
            return bool(result)
        except Exception as e:
            logger.error(f"Error checking user authorization for {user_id}: {str(e)}")
            return False
    
    async def is_waiting_for_password(self, user_id: int) -> bool:
        """
        Check if user is waiting for password input.
        
        Args:
            user_id (int): Telegram user ID
            
        Returns:
            bool: True if user is waiting for password, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                result = await conn.fetchval(
                    "SELECT is_waiting_for_password FROM users WHERE user_id = $1",
                    user_id
                )
            
            if result is None:
                return False
            
            return bool(result)
        except Exception as e:
            logger.error(f"Error checking if user {user_id} is waiting for password: {str(e)}")
            return False
    
    async def set_waiting_for_password(self, user_id: int, waiting: bool = True) -> bool:
        """
        Set user as waiting or not waiting for password input.
        
        Args:
            user_id (int): Telegram user ID
            waiting (bool): Whether the user is waiting for password
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET is_waiting_for_password = $1 WHERE user_id = $2",
                    waiting, user_id
                )
            
            logger.info(f"User {user_id} waiting for password status set to {waiting}")
            return True
        except Exception as e:
            logger.error(f"Error setting password waiting status for user {user_id}: {str(e)}")
            return False
    
    async def check_password(self, user_id: int, password: str, correct_password: str) -> bool:
        """
        Check if password is correct and authorize user if it is.
        
        Args:
            user_id (int): Telegram user ID
            password (str): Entered password
            correct_password (str): Correct password to compare with
            
        Returns:
            bool: True if password is correct, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            # Update attempt counter
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Increment password attempt counter and update timestamp
                    await conn.execute(
                        """
                        UPDATE users 
                        SET password_attempts = COALESCE(password_attempts, 0) + 1,
                            last_attempt_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id
                    )
                    
                    # Check if password is correct
                    if password == correct_password:
                        # Password correct, authorize user
                        await conn.execute(
                            """
                            UPDATE users 
                            SET is_authorized = TRUE, 
                                is_waiting_for_password = FALSE
                            WHERE user_id = $1
                            """,
                            user_id
                        )
                        logger.info(f"User {user_id} entered correct password and was authorized")
                        return True
                    else:
                        # Password incorrect
                        logger.info(f"User {user_id} entered incorrect password")
                        return False
        except Exception as e:
            logger.error(f"Error checking password for user {user_id}: {str(e)}")
            return False
    
    async def get_password_attempts(self, user_id: int) -> int:
        """
        Get number of password attempts for a user.
        
        Args:
            user_id (int): Telegram user ID
            
        Returns:
            int: Number of password attempts
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                result = await conn.fetchval(
                    "SELECT COALESCE(password_attempts, 0) FROM users WHERE user_id = $1",
                    user_id
                )
            
            if result is None:
                return 0
            
            return int(result)
        except Exception as e:
            logger.error(f"Error getting password attempts for user {user_id}: {str(e)}")
            return 0

    async def add_or_update_user(
        self, 
        user_id: int, 
        username: Optional[str] = None, 
        first_name: Optional[str] = None, 
        last_name: Optional[str] = None, 
        is_authorized: bool = False,
        is_waiting_for_password: bool = False
    ) -> bool:
        """
        Add a new user or update information for an existing user.
        
        Args:
            user_id (int): Telegram user ID
            username (str, optional): Username
            first_name (str, optional): First name
            last_name (str, optional): Last name
            is_authorized (bool, optional): Authorization status
            is_waiting_for_password (bool, optional): Whether user is waiting for password
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Check if user exists
                    user_exists = await conn.fetchval(
                        "SELECT user_id FROM users WHERE user_id = $1",
                        user_id
                    )
                    
                    if user_exists:
                        # Update existing user
                        await conn.execute("""
                            UPDATE users 
                            SET username = $1, first_name = $2, last_name = $3, is_authorized = $4, is_waiting_for_password = $5
                            WHERE user_id = $6
                        """, username, first_name, last_name, is_authorized, is_waiting_for_password, user_id)
                        logger.info(f"Updated information for user {user_id}")
                    else:
                        # Add new user
                        await conn.execute("""
                            INSERT INTO users (user_id, username, first_name, last_name, is_authorized, is_waiting_for_password)
                            VALUES ($1, $2, $3, $4, $5, $6)
                        """, user_id, username, first_name, last_name, is_authorized, is_waiting_for_password)
                        logger.info(f"Added new user {user_id}")
                    
                    return True
        except Exception as e:
            logger.error(f"Error adding/updating user {user_id}: {str(e)}")
            return False
    
    async def authorize_user(self, user_id: int) -> bool:
        """
        Authorize user.
        
        Args:
            user_id (int): Telegram user ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Try to update an existing user
                    result = await conn.execute(
                        "UPDATE users SET is_authorized = TRUE, is_waiting_for_password = FALSE WHERE user_id = $1",
                        user_id
                    )
                    
                    if result == "UPDATE 0":
                        # User doesn't exist, add them
                        await conn.execute(
                            "INSERT INTO users (user_id, is_authorized, is_waiting_for_password) VALUES ($1, TRUE, FALSE)",
                            user_id
                        )
                    
                    logger.info(f"User {user_id} authorized")
                    return True
        except Exception as e:
            logger.error(f"Error authorizing user {user_id}: {str(e)}")
            return False
    
    async def get_all_users(self) -> List[Dict[str, Any]]:
        """
        Get list of all users.
        
        Returns:
            List[Dict]: List of dictionaries with user information
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        user_id, username, first_name, last_name, is_authorized, 
                        is_waiting_for_password, password_attempts, created_at, last_attempt_at
                    FROM users
                """)
                
                users = [dict(row) for row in rows]
                return users
        except Exception as e:
            logger.error(f"Error getting list of users: {str(e)}")
            return []
    
    async def log_request(
        self, 
        user_id: int, 
        request_text: str, 
        video_url: Optional[str] = None, 
        start_time: Optional[str] = None, 
        end_time: Optional[str] = None, 
        status: str = "processing"
    ) -> Optional[int]:
        """
        Log user request.
        
        Args:
            user_id (int): Telegram user ID
            request_text (str): Request text
            video_url (str, optional): Video URL
            start_time (str, optional): Start time of fragment
            end_time (str, optional): End time of fragment
            status (str, optional): Request status (processing, completed, error)
            
        Returns:
            int or None: Request ID or None if operation failed
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                request_id = await conn.fetchval("""
                    INSERT INTO requests (user_id, request_text, video_url, start_time, end_time, status)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                """, user_id, request_text, video_url, start_time, end_time, status)
                
                logger.info(f"Request from user {user_id} logged (ID: {request_id})")
                return request_id
        except Exception as e:
            logger.error(f"Error logging request from user {user_id}: {str(e)}")
            return None
    
    async def update_request_status(
        self, 
        request_id: int, 
        status: str, 
        video_url: Optional[str] = None, 
        start_time: Optional[str] = None, 
        end_time: Optional[str] = None
    ) -> bool:
        """
        Update request status.
        
        Args:
            request_id (int): Request ID
            status (str): New status (processing, completed, error)
            video_url (str, optional): Video URL
            start_time (str, optional): Start time of fragment
            end_time (str, optional): End time of fragment
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            pool = await self.get_pool()
            
            update_fields = ["status = $1"]
            update_values = [status]
            value_index = 2
            
            if video_url is not None:
                update_fields.append(f"video_url = ${value_index}")
                update_values.append(video_url)
                value_index += 1
                
            if start_time is not None:
                update_fields.append(f"start_time = ${value_index}")
                update_values.append(start_time)
                value_index += 1
                
            if end_time is not None:
                update_fields.append(f"end_time = ${value_index}")
                update_values.append(end_time)
                value_index += 1
                
            # Add request_id as the last parameter
            update_values.append(request_id)
            
            query = f"UPDATE requests SET {', '.join(update_fields)} WHERE id = ${value_index}"
            
            async with pool.acquire() as conn:
                await conn.execute(query, *update_values)
                
                logger.info(f"Status of request {request_id} updated to '{status}'")
                return True
        except Exception as e:
            logger.error(f"Error updating status of request {request_id}: {str(e)}")
            return False
    
    async def get_user_statistics(self, user_id: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get statistics of user requests.
        
        Args:
            user_id (int, optional): Telegram user ID (None for all users)
            limit (int, optional): Limit number of results
            
        Returns:
            List[Dict]: List of requests with additional user information
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                # Get all requests
                if user_id is not None:
                    all_requests = await conn.fetch("""
                        SELECT id, user_id, request_text, created_at 
                        FROM requests
                        WHERE user_id = $1
                        ORDER BY created_at DESC
                    """, user_id)
                else:
                    all_requests = await conn.fetch("""
                        SELECT id, user_id, request_text, created_at 
                        FROM requests
                        ORDER BY created_at DESC
                    """)
                
                # Group requests by user_id, request_text and minute of creation
                grouped_requests = {}
                for req in all_requests:
                    created_at = req['created_at']
                    minute_key = f"{created_at.year}-{created_at.month:02d}-{created_at.day:02d} {created_at.hour:02d}:{created_at.minute:02d}"
                    
                    key = (
                        req['user_id'],
                        req['request_text'],
                        minute_key
                    )
                    
                    if key not in grouped_requests:
                        grouped_requests[key] = req['id']
                
                # Get information about unique requests
                requests = []
                if grouped_requests:
                    # Get list of unique request IDs
                    request_ids = list(grouped_requests.values())
                    # Limit to specified limit
                    request_ids = request_ids[:limit]
                    
                    # Create placeholders for SQL query
                    placeholders = ','.join(f'${i+1}' for i in range(len(request_ids)))
                    
                    # Get full information about requests
                    rows = await conn.fetch(f"""
                        SELECT 
                            r.id, r.user_id, r.request_text, r.video_url, r.start_time, r.end_time, 
                            r.status, r.created_at, u.username, u.first_name, u.last_name
                        FROM requests r
                        JOIN users u ON r.user_id = u.user_id
                        WHERE r.id IN ({placeholders})
                        ORDER BY r.created_at DESC
                    """, *request_ids)
                    
                    requests = [dict(row) for row in rows]
                
                return requests
        except Exception as e:
            logger.error(f"Error getting request statistics: {str(e)}")
            return []
    
    async def get_total_statistics(self) -> Dict[str, Any]:
        """
        Get total statistics of bot usage.
        
        Returns:
            Dict: Usage statistics (number of users, requests, successful requests, etc.)
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                # Number of users
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
                
                # Number of authorized users
                authorized_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_authorized = TRUE")
                
                # Total number of requests
                total_requests = await conn.fetchval("""
                    SELECT COUNT(*) FROM requests
                    WHERE video_url IS NOT NULL AND video_url != ''
                """)
                
                # Number of successful requests
                completed_requests = await conn.fetchval("""
                    SELECT COUNT(*) FROM requests 
                    WHERE status = 'completed'
                    AND video_url IS NOT NULL AND video_url != ''
                """)
                
                # Number of failed requests
                error_requests = await conn.fetchval("""
                    SELECT COUNT(*) FROM requests 
                    WHERE status = 'error'
                    AND video_url IS NOT NULL AND video_url != ''
                """)
                
                # Get all requests for the last 7 days
                all_requests = await conn.fetch("""
                    SELECT id, user_id, request_text, created_at 
                    FROM requests 
                    WHERE created_at >= NOW() - INTERVAL '7 days'
                    ORDER BY created_at DESC
                """)
                
                # Group requests by user_id, request_text and minute of creation
                grouped_requests = {}
                for req in all_requests:
                    created_at = req['created_at']
                    minute_key = f"{created_at.year}-{created_at.month:02d}-{created_at.day:02d} {created_at.hour:02d}:{created_at.minute:02d}"
                    
                    key = (
                        req['user_id'],
                        req['request_text'],
                        minute_key
                    )
                    
                    if key not in grouped_requests:
                        grouped_requests[key] = req['id']
                
                # Get information about unique requests
                recent_requests = []
                if grouped_requests:
                    # Get list of unique request IDs
                    request_ids = list(grouped_requests.values())
                    # Limit to 30 recent requests
                    request_ids = request_ids[:30]
                    
                    # Create placeholders for SQL query
                    placeholders = ','.join(f'${i+1}' for i in range(len(request_ids)))
                    
                    # Get full information about requests
                    rows = await conn.fetch(f"""
                        SELECT 
                            r.id, r.user_id, r.request_text, r.status, r.created_at,
                            u.username, u.first_name, u.last_name
                        FROM requests r
                        JOIN users u ON r.user_id = u.user_id
                        WHERE r.id IN ({placeholders})
                        ORDER BY r.created_at DESC
                    """, *request_ids)
                    
                    recent_requests = [dict(row) for row in rows]
                
                return {
                    "total_users": total_users,
                    "authorized_users": authorized_users,
                    "total_requests": total_requests,
                    "completed_requests": completed_requests,
                    "error_requests": error_requests,
                    "recent_requests": recent_requests
                }
        except Exception as e:
            logger.error(f"Error getting total statistics: {str(e)}")
            return {
                "total_users": 0,
                "authorized_users": 0,
                "total_requests": 0,
                "completed_requests": 0,
                "error_requests": 0,
                "recent_requests": []
            }
    
    async def get_request_videos(self, request_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos and timecodes from one request.
        
        Args:
            request_id (int): Request ID
            
        Returns:
            List[Dict]: List of dictionaries with information about videos in the request
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                # Get basic information about the request
                request_info = await conn.fetchrow("""
                    SELECT 
                        id, user_id, request_text, status, created_at
                    FROM requests
                    WHERE id = $1
                """, request_id)
                
                if not request_info:
                    return []
                
                # First check if the request has a video URL
                # If it does, it could be a video recording or a parent request
                main_request_video = await conn.fetchrow("""
                    SELECT 
                        id, video_url, start_time, end_time, status
                    FROM requests 
                    WHERE id = $1 AND video_url IS NOT NULL
                """, request_id)
                
                videos = []
                
                if main_request_video:
                    # This is a video recording, return only it
                    videos.append(dict(main_request_video))
                else:
                    # This is a parent request, look for all related video recordings
                    rows = await conn.fetch("""
                        SELECT 
                            id, video_url, start_time, end_time, status
                        FROM requests
                        WHERE 
                            user_id = $1 AND 
                            request_text = $2 AND 
                            DATE_TRUNC('second', created_at) = DATE_TRUNC('second', $3) AND 
                            video_url IS NOT NULL AND
                            id != $4
                        ORDER BY id
                    """, 
                        request_info['user_id'], 
                        request_info['request_text'],
                        request_info['created_at'],
                        request_id
                    )
                    
                    videos = [dict(row) for row in rows]
                
                return videos
        except Exception as e:
            logger.error(f"Error getting videos from request {request_id}: {str(e)}")
            return []
    
    async def log_video_request(
        self, 
        request_id: int, 
        video_url: str, 
        start_time: str, 
        end_time: str, 
        status: str = "processing"
    ) -> Optional[int]:
        """
        Add a new record about a video within a request.
        
        Args:
            request_id (int): ID of the main request
            video_url (str): Video URL
            start_time (str): Start time of fragment
            end_time (str): End time of fragment
            status (str, optional): Video processing status
            
        Returns:
            int or None: ID of the new video record or None if operation failed
        """
        try:
            pool = await self.get_pool()
            
            async with pool.acquire() as conn:
                # Get information about the main request
                parent_request = await conn.fetchrow("""
                    SELECT user_id, request_text, created_at
                    FROM requests
                    WHERE id = $1
                """, request_id)
                
                if not parent_request:
                    logger.error(f"Parent request with ID {request_id} not found")
                    return None
                
                # Create a new record for the video with the same user data and request text
                video_request_id = await conn.fetchval("""
                    INSERT INTO requests (user_id, request_text, video_url, start_time, end_time, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                """, 
                    parent_request['user_id'],
                    parent_request['request_text'],
                    video_url,
                    start_time,
                    end_time,
                    status,
                    parent_request['created_at']
                )
                
                logger.info(f"Added record about video {video_url} for request {request_id} (new ID: {video_request_id})")
                return video_request_id
        except Exception as e:
            logger.error(f"Error adding record about video for request {request_id}: {str(e)}")
            return None 