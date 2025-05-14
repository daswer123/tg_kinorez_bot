import logging
import os
import time
import json
import asyncio
import uuid
from typing import Dict, Any, Optional

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError

from app.core.config import settings
from app.infrastructure.redis import TaskManager
from app.services.video_service import VideoSource, download_video_fragment

# Initialize logger
logger = logging.getLogger(__name__)

class VideoWorker:
    """Worker for processing video fragment extraction tasks."""
    
    def __init__(self, bot: Bot):
        """Initialize the worker with the bot instance."""
        self.bot = bot
        self.running = False
        self.tasks_processed = 0
    
    async def start(self):
        """Start the worker."""
        if self.running:
            logger.warning("Worker already running")
            return
        
        self.running = True
        logger.info("Video worker started")
        
        try:
            while self.running:
                # Get a task from the queue
                task = await TaskManager.get_task(timeout=2)
                
                if not task:
                    # No tasks, sleep for a bit
                    await asyncio.sleep(1)
                    continue
                
                # Check if this is a task we can handle
                task_type = task.get("task_type", "")
                if task_type not in ["video_fragment", "youtube_fragment"]:
                    logger.warning(f"Ignoring task of unknown type: {task_type}")
                    continue
                
                # Process the task
                await self.process_task(task)
                
                # Increment counter
                self.tasks_processed += 1
                
        except asyncio.CancelledError:
            logger.info("Worker cancelled")
            self.running = False
        except Exception as e:
            logger.error(f"Error in worker loop: {e}", exc_info=True)
            self.running = False
            raise
    
    async def process_task(self, task: Dict[str, Any]):
        """Process a video fragment extraction task."""
        task_id = task.get("task_id")
        user_id = task.get("user_id")
        task_data = task.get("task_data", {})
        
        if isinstance(task_data, str):
            try:
                task_data = json.loads(task_data)
            except json.JSONDecodeError:
                logger.error(f"Could not parse task data for task {task_id}")
                await TaskManager.update_task_state(task_id, "failed", error="Could not parse task data")
                return
        
        video_url = task_data.get("video_url")
        start_time = task_data.get("start_time")
        end_time = task_data.get("end_time")
        platform = task_data.get("platform", "youtube")  # Default to YouTube for backward compatibility
        chat_id = task_data.get("chat_id")
        reply_to_message_id = task_data.get("reply_to_message_id")
        
        if not all([video_url, start_time, end_time, chat_id]):
            logger.error(f"Missing required parameters for task {task_id}")
            await TaskManager.update_task_state(task_id, "failed", error="Missing required parameters")
            return
        
        try:
            # Mark task as running
            await TaskManager.update_task_state(task_id, "running")
            
            # Create a VideoSource object
            video_source = VideoSource(
                platform=platform,
                url=video_url,
                start_time=start_time,
                end_time=end_time
            )
            
            # Process the video
            logger.info(f"Processing {platform} video: {video_url} ({start_time}-{end_time})")
            start_process_time = time.time()
            
            # Download video fragment
            video_info = await download_video_fragment(
                video=video_source,
                request_id=task_id,
                user_id=str(user_id)
            )
            
            process_time = time.time() - start_process_time
            
            if not video_info or "file_path" not in video_info:
                logger.error(f"Failed to process video for task {task_id}")
                await TaskManager.update_task_state(task_id, "failed", error="Failed to process video")
                
                # Notify user
                await self.bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text=f"❌ Не удалось обработать {platform} видео: {video_url}\n"
                          "Проверьте правильность ссылки и таймкодов и попробуйте ещё раз."
                )
                return
            
            # Get video info
            file_path = video_info["file_path"]
            title = video_info.get("title", "Unknown")
            source = video_info.get("source", platform)
            
            # Check if file exists
            if not os.path.exists(file_path):
                logger.error(f"Video file not found: {file_path}")
                await TaskManager.update_task_state(task_id, "failed", error="Video file not found")
                
                # Notify user
                await self.bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text=f"❌ Ошибка: файл видео не найден для задачи {task_id}."
                )
                return
            
            # Get file size
            file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB
            logger.info(f"Video processed in {process_time:.2f}s, size: {file_size:.2f}MB")
            
            # Create video caption
            truncated_title = title[:50] + "..." if len(title) > 50 else title
            video_caption = (
                f"✅ <b>Фрагмент {source} видео</b>\n"
                f"• <b>Начало:</b> {start_time}\n"
                f"• <b>Конец:</b> {end_time}\n"
            )
            
            # Send video
            try:
                video_file = FSInputFile(file_path)
                await self.bot.send_video(
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    video=video_file,
                    caption=video_caption,
                    parse_mode="HTML"
                )
                
                # Mark task as completed
                await TaskManager.update_task_state(task_id, "completed")
                
            except TelegramAPIError as e:
                logger.error(f"Error sending video: {e}")
                
                # Try sending as document if video format isn't supported
                try:
                    document_file = FSInputFile(file_path)
                    await self.bot.send_document(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        document=document_file,
                        caption=video_caption,
                        parse_mode="HTML"
                    )
                    
                    # Mark task as completed
                    await TaskManager.update_task_state(task_id, "completed")
                    
                except TelegramAPIError as e2:
                    logger.error(f"Error sending document: {e2}")
                    
                    # Notify user
                    await self.bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        text=f"❌ Ошибка отправки видео: {str(e2)}"
                    )
                    
                    # Mark task as failed
                    await TaskManager.update_task_state(task_id, "failed", error=f"Error sending video: {str(e2)}")
            
            # Clean up
            try:
                os.unlink(file_path)
                logger.info(f"Deleted video file: {file_path}")
            except Exception as e:
                logger.warning(f"Error deleting video file: {e}")
            
        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
            
            # Mark task as failed
            await TaskManager.update_task_state(task_id, "failed", error=str(e))
            
            # Notify user
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text=f"❌ Произошла ошибка при обработке видео: {str(e)}"
                )
            except Exception as e2:
                logger.error(f"Error sending error message: {e2}")
    
    async def stop(self):
        """Stop the worker."""
        self.running = False
        logger.info(f"Video worker stopped. Processed {self.tasks_processed} tasks.") 