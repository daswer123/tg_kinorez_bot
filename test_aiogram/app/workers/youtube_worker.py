"""Worker for processing YouTube fragment tasks."""

import logging
import asyncio
import time
import json
import os
from typing import Dict, Any, Optional

from app.infrastructure.redis import TaskManager
from app.services.youtube import get_video_by_url_and_timings
from app.bot import bot
from aiogram.types import FSInputFile

# Module logger
logger = logging.getLogger(__name__)

# Flag to control worker stop
_worker_running = False

async def process_youtube_task(task: Dict[str, Any]) -> bool:
    """
    Process a YouTube fragment task.
    
    Args:
        task (Dict[str, Any]): Task data
        
    Returns:
        bool: True if successful, False otherwise
    """
    task_id = task.get("task_id")
    user_id = task.get("user_id")
    task_data = task.get("task_data", {})
    
    if not task_id or not user_id or not task_data:
        logger.error(f"Invalid task data: {task}")
        return False
    
    # Mark task as running
    await TaskManager.update_task_state(
        task_id=task_id,
        status="running"
    )
    
    # Extract video info
    video_url = task_data.get("video_url")
    start_time = task_data.get("start_time")
    end_time = task_data.get("end_time")
    chat_id = task_data.get("chat_id")
    reply_to_message_id = task_data.get("reply_to_message_id")
    
    if not video_url or not start_time or not end_time or not chat_id:
        logger.error(f"Invalid task data: {task_data}")
        
        # Mark task as failed
        await TaskManager.update_task_state(
            task_id=task_id,
            status="failed",
            error="Incomplete task data"
        )
        return False
    
    try:
        # Log task start
        logger.info(f"Processing task {task_id} for user {user_id}: {video_url} ({start_time}-{end_time})")
        
        # Process video
        result = get_video_by_url_and_timings(
            url=video_url,
            start_time=start_time,
            end_time=end_time,
            request_id=task_id,
            user_id=str(user_id)
        )
        
        if not result:
            # Mark task as failed
            await TaskManager.update_task_state(
                task_id=task_id,
                status="failed",
                error="Failed to process video"
            )
            
            # Send error message to user
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Не удалось обработать видео.\n\n"
                         f"• URL: {video_url}\n"
                         f"• Начало: {start_time}\n"
                         f"• Конец: {end_time}",
                    reply_to_message_id=reply_to_message_id
                )
            except Exception as e:
                logger.error(f"Error sending error message to user: {e}")
            
            return False
        
        # If result indicates already processed, just update status
        if result.get("already_processed"):
            await TaskManager.update_task_state(
                task_id=task_id,
                status="completed",
                result="Already processed before"
            )
            
            # Send message to user
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Этот фрагмент видео уже был обработан ранее.\n\n"
                         f"• URL: {video_url}\n"
                         f"• Начало: {start_time}\n"
                         f"• Конец: {end_time}",
                    reply_to_message_id=reply_to_message_id
                )
            except Exception as e:
                logger.error(f"Error sending already processed message: {e}")
            
            return True
        
        # Get video file path
        video_path = result.get("file_path")
        
        if not video_path or not os.path.exists(video_path):
            # Mark task as failed
            await TaskManager.update_task_state(
                task_id=task_id,
                status="failed",
                error="Video file not found"
            )
            
            # Cleanup any files that might be associated with this task
            try:
                download_path = result.get("download_path", "")
                if download_path:
                    for ext in ['mp4', 'webm', 'mkv', 'avi']:
                        potential_file = f"{download_path}.{ext}"
                        if os.path.exists(potential_file):
                            os.remove(potential_file)
                            logger.info(f"Removed leftover file: {potential_file}")
            except Exception as remove_err:
                logger.warning(f"Failed to clean up potential video files: {remove_err}")
            
            # Send error message to user
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Не удалось найти обработанный файл.\n\n"
                         f"• URL: {video_url}\n"
                         f"• Начало: {start_time}\n"
                         f"• Конец: {end_time}",
                    reply_to_message_id=reply_to_message_id
                )
            except Exception as e:
                logger.error(f"Error sending error message to user: {e}")
            
            return False
        
        # Send video to user
        try:
            video_file = FSInputFile(path=video_path)
            
            # Get resolution info
            max_resolution = result.get('max_resolution', 0)
            resolution_text = ""
            if max_resolution > 0:
                if max_resolution < 1080:
                    resolution_text = f"\n\n⚠️ Качество видео: {max_resolution}p"
            
            await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=f"✅ Фрагмент видео:\n\n"
                        f"• URL: {video_url}\n"
                        f"• Начало: {start_time}\n"
                        f"• Конец: {end_time}{resolution_text}",
                reply_to_message_id=reply_to_message_id
            )
            
            # Mark task as completed
            await TaskManager.update_task_state(
                task_id=task_id,
                status="completed",
                result=f"Video sent to user"
            )
            
            # Remove the video file after successfully sending it
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"Removed video file: {video_path}")
            except Exception as remove_err:
                logger.warning(f"Failed to remove video file {video_path}: {remove_err}")
            
            logger.info(f"Task {task_id} completed successfully")
            return True
        except Exception as e:
            logger.error(f"Error sending video to user: {e}", exc_info=True)
            
            # Mark task as failed
            await TaskManager.update_task_state(
                task_id=task_id,
                status="failed",
                error=f"Error sending video: {str(e)}"
            )
            
            # Try to remove the video file even if sending failed
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
                    logger.info(f"Removed video file after send failure: {video_path}")
            except Exception as remove_err:
                logger.warning(f"Failed to remove video file {video_path}: {remove_err}")
            
            # Try to send error message to user
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка при отправке видео.\n\n"
                         f"• URL: {video_url}\n"
                         f"• Начало: {start_time}\n"
                         f"• Конец: {end_time}\n\n"
                         f"Ошибка: {str(e)}",
                    reply_to_message_id=reply_to_message_id
                )
            except Exception as send_err:
                logger.error(f"Error sending error message to user: {send_err}")
            
            return False
    except Exception as e:
        logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
        
        # Mark task as failed
        await TaskManager.update_task_state(
            task_id=task_id,
            status="failed",
            error=f"Error processing task: {str(e)}"
        )
        
        # Try to send error message to user
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ Ошибка при обработке видео.\n\n"
                     f"• URL: {video_url}\n"
                     f"• Начало: {start_time}\n"
                     f"• Конец: {end_time}\n\n"
                     f"Ошибка: {str(e)}",
                reply_to_message_id=reply_to_message_id
            )
        except Exception as send_err:
            logger.error(f"Error sending error message to user: {send_err}")
        
        return False

async def youtube_worker_loop(worker_id: int = 0):
    """
    Main worker loop that processes tasks from the queue.
    
    Args:
        worker_id (int): Unique ID for this worker instance
    """
    global _worker_running
    
    logger.info(f"Starting YouTube worker loop #{worker_id}")
    _worker_running = True
    
    while _worker_running:
        try:
            # Get task from queue with timeout
            task = await TaskManager.get_task(timeout=5)
            
            if not task:
                # No tasks, sleep briefly to avoid CPU spin
                await asyncio.sleep(0.1)
                continue
            
            # Check task type
            task_type = task.get("task_type")
            
            if task_type == "youtube_fragment":
                # Process YouTube fragment task
                logger.info(f"Worker #{worker_id} processing task {task.get('task_id')}")
                await process_youtube_task(task)
            else:
                logger.warning(f"Unknown task type: {task_type}")
                
                # Mark task as failed
                task_id = task.get("task_id")
                if task_id:
                    await TaskManager.update_task_state(
                        task_id=task_id,
                        status="failed",
                        error=f"Unknown task type: {task_type}"
                    )
        except asyncio.CancelledError:
            logger.info(f"YouTube worker #{worker_id} received cancel signal")
            _worker_running = False
            break
        except Exception as e:
            logger.error(f"Error in YouTube worker #{worker_id}: {e}", exc_info=True)
            # Sleep to avoid tight error loop
            await asyncio.sleep(1)
    
    logger.info(f"YouTube worker #{worker_id} loop stopped")

async def notification_loop():
    """Notification loop that processes completed tasks and notifies users."""
    logger.info("Starting notification loop")
    
    pubsub = await TaskManager.subscribe_to_results()
    
    try:
        while _worker_running:
            try:
                # Get message from PubSub
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                
                if not message:
                    # No messages, sleep briefly to avoid CPU spin
                    await asyncio.sleep(0.1)
                    continue
                
                # Extract task ID
                task_id = message.get("data")
                if not task_id:
                    continue
                
                logger.info(f"Received notification for task {task_id}")
                
                # Get task state
                task_state = await TaskManager.get_task_state(task_id)
                
                if not task_state:
                    logger.warning(f"Task state not found for task {task_id}")
                    continue
                
                # If task already handled by the direct worker, skip
                if task_state.get("notification_sent"):
                    logger.info(f"Notification already sent for task {task_id}")
                    continue
                
                # Mark notification as sent
                await TaskManager.update_task_state(
                    task_id=task_id,
                    status=task_state.get("status", "unknown"),
                    notification_sent=True
                )
                
                # Additional notification logic can be added here if needed
                
            except Exception as e:
                logger.error(f"Error in notification loop: {e}", exc_info=True)
                # Sleep to avoid tight error loop
                await asyncio.sleep(1)
    finally:
        # Unsubscribe and close
        await pubsub.unsubscribe()
        logger.info("Notification loop stopped")

def start_youtube_worker(num_workers: int = 1):
    """
    Start the YouTube worker and notification loop.
    
    Args:
        num_workers (int): Number of worker instances to start
    
    Returns:
        list: List of worker tasks and notification task
    """
    # Start workers in tasks
    worker_tasks = [asyncio.create_task(youtube_worker_loop(i)) for i in range(num_workers)]
    notification_task = asyncio.create_task(notification_loop())
    
    # Return all tasks
    return worker_tasks + [notification_task]

def stop_youtube_worker():
    """Stop the YouTube worker."""
    global _worker_running
    _worker_running = False
    logger.info("YouTube worker stop signal sent") 