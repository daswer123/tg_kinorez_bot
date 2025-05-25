import logging
import os
import time
import uuid
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command

from app.services.video_service import process_multi_source_request
from app.infrastructure.redis import TaskManager

# Create logger for this module
logger = logging.getLogger(__name__)

# Create router for video messages
router = Router(name="video")

# This router will handle text messages that aren't commands
@router.message(F.text)
async def handle_video(message: Message):
    """Обработчик для сообщений с видео ссылками и таймкодами."""
    user_id = message.from_user.id
    user_text = message.text
    
    # Проверяем, не обрабатывалось ли уже это сообщение
    if await TaskManager.is_message_processed(message.message_id, message.chat.id):
        logger.info(f"Message {message.message_id} in chat {message.chat.id} was already processed, skipping")
        return
    
    # Send processing message
    processing_message = await message.reply("Обрабатываю ваш запрос...")
    
    # Extract structured data using LLM
    video_sources = await process_multi_source_request(user_text)
    
    if not video_sources:
        await processing_message.edit_text(
            "Не удалось распознать ссылки на видео или таймкоды. "
            "Пожалуйста, проверьте формат и попробуйте снова."
        )
        logger.warning(f"Failed to extract video data for user {user_id}")
        return
    
    logger.info(f"Extracted {len(video_sources)} video(s) to process for user {user_id}")
    
    # Count videos by platform
    platforms = {}
    for video in video_sources:
        platforms[video.platform] = platforms.get(video.platform, 0) + 1
    
    platforms_summary = ", ".join([f"{count} видео с {platform}" for platform, count in platforms.items()])
    
    # Update processing message with detected videos count
    try:
        await processing_message.edit_text(
            f"Обнаружено {len(video_sources)} видео ({platforms_summary}). "
            f"Отправляю в очередь обработки..."
        )
    except TelegramNetworkError as e:
        logger.warning(f"Failed to update processing message: {e}")
    
    # Process each video in the response
    tasks_info = []
    
    for i, video in enumerate(video_sources, 1):
        # Create individual status for each video
        if len(video_sources) > 1:
            status_text = (
                f"Видео {i}/{len(video_sources)}:\n"
                f"• Платформа: {video.platform}\n"
                f"• URL: {video.url}\n"
                f"• Начало: {video.start_time}\n"
                f"• Конец: {video.end_time}\n"
                f"• Вертикальная обрезка: {'Да' if video.vertical_crop else 'Нет'}\n"
            )
        else:
            status_text = (
                f"✅ Распознано:\n"
                f"• Платформа: {video.platform}\n"
                f"• Видео: {video.url}\n"
                f"• Начало: {video.start_time}\n"
                f"• Конец: {video.end_time}\n"
                f"• Вертикальная обрезка: {'Да' if video.vertical_crop else 'Нет'}\n"
            )
        
        if not video.correct_timings:
            error_message = "Таймкоды некорректны"
            if video.error_details:
                error_message += f": {video.error_details}"
            
            # Если это единственное видео или последнее, изменяем статусное сообщение
            # Иначе отправляем отдельное сообщение с ошибкой и продолжаем обработку
            try:
                if len(video_sources) == 1 or i == len(video_sources):
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
            "platform": video.platform,
            "chat_id": message.chat.id,
            "reply_to_message_id": message.message_id,
            "vertical_crop": video.vertical_crop,
            "status_message_id": processing_message.message_id if processing_message else None
        }
        
        # Add task to queue
        try:
            task_id = await TaskManager.add_task(
                user_id=user_id,
                task_type="video_fragment",
                task_data=task_data
            )
            
            tasks_info.append({
                "task_id": task_id,
                "video_url": video.url,
                "start_time": video.start_time,
                "end_time": video.end_time,
                "platform": video.platform,
                "vertical_crop": video.vertical_crop, # Добавляем vertical_crop сюда
                "index": i,
                "total": len(video_sources)
            })
            
            logger.info(f"Added task {task_id} to queue for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to add task to queue: {e}", exc_info=True)
            
            try:
                if len(video_sources) == 1:
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
                f"• Платформа: {task['platform']}\n"
                f"• URL: {task['video_url']}\n"
                f"• Начало: {task['start_time']}\n"
                f"• Конец: {task['end_time']}\n"
                f"• Вертикальная обрезка: {'Да' if task.get('vertical_crop') else 'Нет'}\n\n"
                f"Вы получите уведомление, когда видео будет готово.",
                parse_mode=ParseMode.HTML
            )
        else:
            tasks_text = "\n".join([
                f"{i+1}. {task['platform']}: {task['video_url']} | {task['start_time']}-{task['end_time']} | Вертикальная обрезка: {'Да' if task.get('vertical_crop') else 'Нет'}"
                for i, task in enumerate(tasks_info)
            ])
            
            await processing_message.edit_text(
                f"✅ Ваши запросы на обработку видео приняты в работу.\n\n"
                f"{tasks_text}\n\n"
                f"Вы получите уведомления о готовности каждого видео.",
                parse_mode=ParseMode.HTML
            )
    elif not any(video.correct_timings for video in video_sources):
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
        
        if task_type in ["youtube_fragment", "video_fragment"]:
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
            platform = task_data.get("platform", "youtube")
            
            task_info = (
                f"• Платформа: {platform}\n"
                f"• URL: {video_url}\n"
                f"• Начало: {start_time}\n"
                f"• Конец: {end_time}\n"
                f"• Вертикальная обрезка: {'Да' if task_data.get('vertical_crop') else 'Нет'}\n"
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
        
        # Get error message if any
        error_info = ""
        if status == "failed":
            error_info = task_state.get("error", "")
            if error_info:
                error_info = f"\n❌ Ошибка: {error_info}"
        
        # Format full message
        full_message = (
            f"{status_emoji} <b>Задача #{task_id}</b>\n\n"
            f"{task_info}\n"
            f"{time_info}"
            f"{error_info}"
        )
        
        await message.reply(
            full_message,
            parse_mode=ParseMode.HTML
        )
            
    except Exception as e:
        logger.error(f"Error checking task status: {e}", exc_info=True)
        await message.reply(
            f"❌ Произошла ошибка при получении информации о задаче. Пожалуйста, попробуйте позже.",
            parse_mode=ParseMode.HTML
        ) 


import shutil
from app.services.extract_face.extract_face import extract_separate_videos_for_faces


@router.message(F.video)
async def handle_video_file(message: Message):
    """Обработчик для видеофайлов - извлекает лица и отправляет отдельные видео."""
    user_id = message.from_user.id
    
    # Проверяем, не обрабатывалось ли уже это сообщение
    if await TaskManager.is_message_processed(message.message_id, message.chat.id):
        logger.info(f"Video message {message.message_id} in chat {message.chat.id} was already processed, skipping")
        return
    
    # Send processing message
    processing_message = await message.reply("🎬 Обрабатываю ваше видео для извлечения лиц...")
    
    try:
        # Создаем временную директорию для обработки
        temp_dir = f"temp/temp_video_{user_id}_{message.message_id}"
        os.makedirs(temp_dir, exist_ok=True)
        
        # Скачиваем видео
        video_file = message.video
        file_info = await message.bot.get_file(video_file.file_id)
        
        # Определяем расширение файла
        file_extension = ".mp4"  # По умолчанию
        if video_file.file_name:
            file_extension = os.path.splitext(video_file.file_name)[1] or ".mp4"
        
        input_video_path = os.path.join(temp_dir, f"input{file_extension}")
        
        await processing_message.edit_text("📥 Скачиваю видео...")
        
        # Скачиваем файл
        await message.bot.download_file(file_info.file_path, input_video_path)
        
        logger.info(f"Downloaded video for user {user_id}: {input_video_path}")
        
        await processing_message.edit_text("🔍 Ищу лица в видео...")
        
        
        # Обрабатываем видео для извлечения лиц
        output_base_dir = os.path.join(temp_dir, "faces_output")
        
        success, face_videos = extract_separate_videos_for_faces(
            input_video_path=input_video_path,
            output_directory_base=output_base_dir,
            padding_factor=2,
            target_aspect_ratio=9.0 / 16.0,  # Вертикальный формат
            output_width=1080,
            output_height=1920,
            initial_detection_frames=100,
            overwrite_output=True,
            offsets_x=[],
            offsets_y=[]
        )
        
        if not success:
            await processing_message.edit_text(
                "❌ Произошла ошибка при обработке видео. Пожалуйста, попробуйте позже."
            )
            logger.error(f"Face extraction failed for user {user_id}")
            return
        
        if not face_videos:
            await processing_message.edit_text(
                "😔 В вашем видео не удалось обнаружить лица. "
                "Попробуйте загрузить видео с более четкими лицами."
            )
            logger.info(f"No faces found in video for user {user_id}")
            return
        
        await processing_message.edit_text(
            f"✅ Найдено {len(face_videos)} лиц! Отправляю видео..."
        )
        
        # Отправляем каждое видео с лицом
        for i, face_video_path in enumerate(face_videos, 1):
            try:
                if not os.path.exists(face_video_path):
                    logger.warning(f"Face video file not found: {face_video_path}")
                    continue
                
                # Создаем FSInputFile для отправки
                video_file_to_send = FSInputFile(
                    face_video_path,
                    filename=f"face_{i}.mp4"
                )
                
                # Отправляем видео
                await message.reply_video(
                    video=video_file_to_send,
                    caption=f"🎭 Лицо #{i} из вашего видео",
                    reply_to_message_id=message.message_id
                )
                
                logger.info(f"Sent face video {i} to user {user_id}")
                
            except Exception as e:
                logger.error(f"Failed to send face video {i} to user {user_id}: {e}", exc_info=True)
                await message.reply(
                    f"❌ Не удалось отправить видео с лицом #{i}",
                    reply_to_message_id=message.message_id
                )
        
        # Обновляем финальное сообщение
        await processing_message.edit_text(
            f"🎉 Обработка завершена! Отправлено {len(face_videos)} видео с лицами."
        )
        
        # Отмечаем сообщение как обработанное
        await TaskManager.update_task_state(str(message.message_id), "completed")
        
    except Exception as e:
        logger.error(f"Error processing video file for user {user_id}: {e}", exc_info=True)
        try:
            await processing_message.edit_text(
                "❌ Произошла ошибка при обработке видео. Пожалуйста, попробуйте позже."
            )
        except:
            pass
    
    finally:
        # Удаляем временные файлы
        try:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")

