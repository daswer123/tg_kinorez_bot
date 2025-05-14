import logging
import os
import time
import uuid
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command

from app.services.youtube import extract_video_data, get_video_by_url_and_timings
from app.infrastructure.redis import TaskManager

# Create logger for this module
logger = logging.getLogger(__name__)

# Create router for YouTube messages
router = Router(name="youtube")

# This router will handle text messages that aren't commands
@router.message(F.text)
async def handle_youtube(message: Message):
    """Handler for messages with YouTube URLs and timestamps."""
    user_id = message.from_user.id
    user_text = message.text
    
    # Send processing message
    processing_message = await message.reply("Обрабатываю ваш запрос...")
    
    # Extract structured data using LLM
    response = extract_video_data(user_text)
    
    if not response:
        await processing_message.edit_text(
            "Не удалось распознать ссылку на YouTube или таймкоды. "
            "Пожалуйста, проверьте формат и попробуйте снова."
        )
        logger.warning(f"Failed to extract video data for user {user_id}")
        return
    
    logger.info(f"Extracted {len(response)} video(s) to process for user {user_id}")
    
    # Update processing message with detected videos count
    if len(response) > 1:
        try:
            await processing_message.edit_text(
                f"Обнаружено {len(response)} видео. Отправляю в очередь обработки..."
            )
        except TelegramNetworkError as e:
            logger.warning(f"Failed to update processing message: {e}")
    
    # Process each video in the response
    tasks_info = []
    
    for i, video in enumerate(response, 1):
        # Create individual status for each video
        if len(response) > 1:
            status_text = (
                f"Видео {i}/{len(response)}:\n"
                f"• URL: {video.url}\n"
                f"• Начало: {video.start_time}\n"
                f"• Конец: {video.end_time}\n"
            )
        else:
            status_text = (
                f"✅ Распознано:\n"
                f"• Видео: {video.url}\n"
                f"• Начало: {video.start_time}\n"
                f"• Конец: {video.end_time}\n"
            )
        
        if not video.correct_timings:
            error_message = "Таймкоды некорректны"
            if hasattr(video, 'error_details') and video.error_details:
                error_message += f": {video.error_details}"
            
            # Если это единственное видео или последнее, изменяем статусное сообщение
            # Иначе отправляем отдельное сообщение с ошибкой и продолжаем обработку
            try:
                if len(response) == 1 or i == len(response):
                    await processing_message.edit_text(
                        f"{status_text}\n❌ {error_message}. Пожалуйста, проверьте и попробуйте снова."
                    )
                else:
                    await message.reply(
                        f"{status_text}\n❌ {error_message}. Пожалуйста, проверьте и попробуйте снова."
                    )
            except TelegramNetworkError as e:
                logger.warning(f"Failed to send error message: {e}")
            continue
        
        # Create task data
        task_data = {
            "video_url": video.url,
            "start_time": video.start_time,
            "end_time": video.end_time,
            "chat_id": message.chat.id,
            "reply_to_message_id": message.message_id
        }
        
        # Add task to queue
        try:
            task_id = await TaskManager.add_task(
                user_id=user_id,
                task_type="youtube_fragment",
                task_data=task_data
            )
            
            tasks_info.append({
                "task_id": task_id,
                "video_url": video.url,
                "start_time": video.start_time,
                "end_time": video.end_time,
                "index": i,
                "total": len(response)
            })
            
            logger.info(f"Added task {task_id} to queue for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to add task to queue: {e}", exc_info=True)
            
            try:
                if len(response) == 1:
                    await processing_message.edit_text(
                        f"{status_text}\n❌ Ошибка при постановке задачи в очередь. Пожалуйста, попробуйте позже."
                    )
                else:
                    await message.reply(
                        f"{status_text}\n❌ Ошибка при постановке задачи в очередь. Пожалуйста, попробуйте позже."
                    )
            except TelegramNetworkError as e:
                logger.warning(f"Failed to send error message: {e}")
    
    # Update final message with task IDs
    if tasks_info:
        if len(tasks_info) == 1:
            task = tasks_info[0]
            await processing_message.edit_text(
                f"✅ Ваш запрос на обработку видео принят в работу.\n\n"
                f"• URL: {task['video_url']}\n"
                f"• Начало: {task['start_time']}\n"
                f"• Конец: {task['end_time']}\n\n"
                f"Вы получите уведомление, когда видео будет готово.",
                parse_mode=ParseMode.HTML
            )
        else:
            tasks_text = "\n".join([
                f"{i+1}. URL: {task['video_url']} | {task['start_time']}-{task['end_time']}"
                for i, task in enumerate(tasks_info)
            ])
            
            await processing_message.edit_text(
                f"✅ Ваши запросы на обработку видео приняты в работу.\n\n"
                f"{tasks_text}\n\n"
                f"Вы получите уведомления о готовности каждого видео.",
                parse_mode=ParseMode.HTML
            )
    elif not any(video.correct_timings for video in response):
        # All videos had incorrect timings, status already updated
        pass
    else:
        # No tasks were added successfully
        await processing_message.edit_text(
            "❌ Не удалось добавить задачи в очередь обработки. Пожалуйста, попробуйте позже."
        )

@router.message(Command("task"))
async def cmd_check_task(message: Message):
    """Handler for checking task status by ID."""
    command_parts = message.text.split(maxsplit=1)
    
    if len(command_parts) < 2:
        await message.reply(
            "Пожалуйста, укажите ID задачи. Например: /task 1234-5678-90ab-cdef",
            parse_mode=ParseMode.HTML
        )
        return
    
    task_id = command_parts[1].strip()
    
    # Get task state
    try:
        task_state = await TaskManager.get_task_state(task_id)
        
        if not task_state:
            await message.reply(
                f"❌ Задача с ID {task_id} не найдена.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Check if task belongs to this user
        user_id = message.from_user.id
        task_user_id = int(task_state.get("user_id", "0"))
        
        if task_user_id != user_id:
            await message.reply(
                f"❌ Задача с ID {task_id} не принадлежит вам.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Format task info
        status = task_state.get("status", "unknown")
        task_type = task_state.get("task_type", "unknown")
        
        if task_type == "youtube_fragment":
            task_data = task_state.get("task_data", {})
            if isinstance(task_data, str):
                import json
                try:
                    task_data = json.loads(task_data)
                except:
                    task_data = {}
            
            video_url = task_data.get("video_url", "Неизвестно")
            start_time = task_data.get("start_time", "Неизвестно")
            end_time = task_data.get("end_time", "Неизвестно")
            
            task_info = (
                f"• URL: {video_url}\n"
                f"• Начало: {start_time}\n"
                f"• Конец: {end_time}\n"
            )
        else:
            task_info = f"• Тип задачи: {task_type}\n"
        
        # Get formatted time info
        created_at = float(task_state.get("created_at", 0))
        queued_at = float(task_state.get("queued_at", 0))
        running_at = float(task_state.get("running_at", 0))
        completed_at = float(task_state.get("completed_at", 0))
        failed_at = float(task_state.get("failed_at", 0))
        
        time_info = f"• Создана: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created_at)) if created_at else 'Неизвестно'}\n"
        
        if running_at:
            time_info += f"• Запущена: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(running_at))}\n"
        
        if completed_at:
            time_info += f"• Завершена: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(completed_at))}\n"
        
        if failed_at:
            time_info += f"• Завершена с ошибкой: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(failed_at))}\n"
        
        # Format status emoji
        status_emoji = {
            "queued": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌"
        }.get(status, "❓")
        
        # Format result or error
        result_info = ""
        if status == "completed":
            result = task_state.get("result", "")
            if result:
                result_info = f"\n✅ Результат: {result}"
        elif status == "failed":
            error = task_state.get("error", "")
            if error:
                result_info = f"\n❌ Ошибка: {error}"
        
        # Send task info
        await message.reply(
            f"Информация о задаче {task_id}:\n\n"
            f"Статус: {status_emoji} {status.capitalize()}\n\n"
            f"{task_info}\n"
            f"{time_info}"
            f"{result_info}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error checking task status: {e}", exc_info=True)
        await message.reply(
            f"❌ Ошибка при получении информации о задаче {task_id}: {str(e)}",
            parse_mode=ParseMode.HTML
        ) 