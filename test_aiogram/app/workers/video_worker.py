import logging
import os
import time
import json
import asyncio
import uuid
import shutil
from typing import Dict, Any, Optional

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError

from app.core.config import settings
from app.infrastructure.redis import TaskManager
from app.services.video_service import VideoSource, download_video_fragment
from app.services.extract_face.extract_face import extract_separate_videos_for_faces
from app.services.extract_face_v2.deepface_detector import process_video_for_speaker_cuts
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
        vertical_crop = task_data.get("vertical_crop", False)
        status_message_id = task_data.get("status_message_id") # Извлекаем ID статусного сообщения
        
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
                end_time=end_time,
                vertical_crop=vertical_crop
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
                if vertical_crop:
                    if status_message_id:
                        try:
                            await self.bot.edit_message_text(
                                text="🎬 Обрабатываю ваше видео для извлечения лиц...",
                                chat_id=chat_id,
                                message_id=status_message_id
                            )
                            # Передаем task_id, status_message_id для дальнейшего редактирования
                            await self.process_vertical_crop(task_id, chat_id, reply_to_message_id, file_path, user_id, status_message_id, self.bot)
                        except TelegramAPIError as e:
                            logger.error(f"Failed to edit status message {status_message_id} for vertical crop: {e}. Sending new message.")
                            # Если не удалось отредактировать, отправляем новое (запасной вариант)
                            new_processing_message = await self.bot.send_message(
                                chat_id=chat_id,
                                reply_to_message_id=reply_to_message_id,
                                text="🎬 Обрабатываю ваше видео для извлечения лиц..."
                            )
                            await self.process_vertical_crop(task_id, chat_id, reply_to_message_id, file_path, user_id, new_processing_message.message_id, self.bot, is_new_message=True)
                    else:
                        # Если status_message_id нет, отправляем новое сообщение (старое поведение)
                        new_processing_message = await self.bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="🎬 Обрабатываю ваше видео для извлечения лиц..."
                        )
                        await self.process_vertical_crop(task_id, chat_id, reply_to_message_id, file_path, user_id, new_processing_message.message_id, self.bot, is_new_message=True)
                
                # Mark task as completed (основная часть задачи завершена, vertical_crop - дополнительно)
                # Статус completion для vertical_crop будет установлен внутри process_vertical_crop,
                # или здесь, если vertical_crop не было.
                if not vertical_crop:
                    await TaskManager.update_task_state(task_id, "completed", result_main_video="Видео успешно отправлено")
                
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

    async def process_vertical_crop(self, task_id: str, chat_id: int, original_message_id: int, file_path: str, user_id: int, message_id_to_edit: int, bot: Bot, is_new_message: bool = False):
        """Process video for vertical crop and face detection."""
        
        async def edit_status_message(text: str):
            try:
                await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id_to_edit)
            except TelegramAPIError as e:
                logger.error(f"Failed to edit status message {message_id_to_edit}: {e}")

        try:
            # Создаем временную директорию для обработки
            # Используем original_message_id для уникальности имени папки, так как оно связано с исходным запросом пользователя
            temp_dir = f"temp/temp_video_{user_id}_{original_message_id}"
            os.makedirs(temp_dir, exist_ok=True)
            
            logger.info(f"Processing vertical crop for task {task_id}, user {user_id}: {file_path}")
            
            await edit_status_message("🔍 Ищу лица в видео...")
          
            # Обрабатываем видео для извлечения лиц
            output_base_dir = os.path.join(temp_dir, "faces_output")
            
            # success, face_videos = extract_separate_videos_for_faces(
            #     input_video_path=file_path,
            #     output_directory_base=output_base_dir,
            #     padding_factor=2.3,
            #     target_aspect_ratio=9.0 / 16.0,  # Вертикальный формат
            #     output_width=1080,
            #     output_height=1920,
            #     initial_detection_frames=300,
            #     overwrite_output=True,
            #     offsets_x=[],
            #     offsets_y=[]
            # )

            success, face_videos, error_msg = process_video_for_speaker_cuts(
                input_video_path=file_path,
                output_save_dir=output_base_dir,
                
            # DeepFace параметры
            recognition_model_name="Facenet512",
            detector_backend="mtcnn",
            similarity_threshold_base=0.68,
            
            # Анализ
            fps_to_process_analysis=5,
            analysis_max_width=480,
            max_frames_to_keep_track_without_detection_factor=3,
            
            # Выбор спикеров
            min_track_duration_seconds=3,
            autodetect_speaker_count=False,
            top_n_faces_to_crop=2,
            
            # Виртуальная камера
            cam_output_width=1080,
            cam_output_height=1920,
            cam_target_head_height_ratio=0.35,
            cam_target_head_pos_x_ratio=0.5,
            cam_target_head_pos_y_ratio=0.5,
            cam_smoothing_factor_position=0.1,
            cam_smoothing_factor_size=0.1,
            
            # Зона комфорта
            cam_comfort_zone_size_delta_r=0.07,
            cam_comfort_zone_pos_x_delta_r=0.07,
            cam_comfort_zone_pos_y_delta_r=0.07,
            
            # Вывод
            output_video_fps_factor=1.0,
            output_video_codec='mp4v',
            add_audio_to_output=True
            )

            
            if not success:
                await edit_status_message(
                    "❌ Произошла ошибка при извлечении лиц из видео. Пожалуйста, попробуйте позже."
                )
                logger.error(f"Face extraction failed for task {task_id}, user {user_id}")
                await TaskManager.update_task_state(task_id, "failed", error_vertical_crop="Face extraction failed")
                return
            
            if not face_videos:
                await edit_status_message(
                    "😔 В вашем видео не удалось обнаружить лица. "
                    "Попробуйте загрузить видео с более четкими лицами."
                )
                logger.info(f"No faces found in video for task {task_id}, user {user_id}")
                # Считаем это успешным завершением этапа vertical_crop, но без результатов
                await TaskManager.update_task_state(task_id, "completed", result_vertical_crop="No faces found")
                return
            
            await edit_status_message(
                f"✅ Найдено {len(face_videos)} лиц! Отправляю видео..."
            )
            
            # Отправляем каждое видео с лицом
            sent_face_videos_count = 0
            for i, face_video_path in enumerate(face_videos, 1):
                try:
                    if not os.path.exists(face_video_path):
                        logger.warning(f"Face video file not found: {face_video_path} for task {task_id}")
                        continue
                    
                    # Создаем FSInputFile для отправки
                    video_file_to_send = FSInputFile(
                        face_video_path,
                        filename=f"face_{i}.mp4"
                    )
                    
                    # Отправляем видео
                    await bot.send_video(
                        chat_id=chat_id,
                        reply_to_message_id=original_message_id,
                        video=video_file_to_send,
                        caption=f"🎭 Лицо #{i} из вашего видео"
                    )
                    sent_face_videos_count += 1
                    logger.info(f"Sent face video {i} to user {user_id} for task {task_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send face video {i} to user {user_id} for task {task_id}: {e}", exc_info=True)
                    await bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=original_message_id,
                        text=f"❌ Не удалось отправить видео с лицом #{i}"
                    )
            
            # Обновляем финальное сообщение
            final_status_text = f"🎉 Обработка вертикальной обрезки завершена! Отправлено {sent_face_videos_count} из {len(face_videos)} видео с лицами."
            await edit_status_message(final_status_text)
            
            # Отмечаем основную задачу как выполненную
            await TaskManager.update_task_state(task_id, "completed", result_vertical_crop=f"Отправлено {sent_face_videos_count}/{len(face_videos)} видео с лицами")
            
        except Exception as e:
            logger.error(f"Error processing vertical crop for task {task_id}, user {user_id}: {e}", exc_info=True)
            try:
                await edit_status_message(
                    "❌ Произошла ошибка при обработке видео для вертикальной обрезки. Пожалуйста, попробуйте позже."
                )
                await TaskManager.update_task_state(task_id, "failed", error_vertical_crop=str(e))
            except:
                await TaskManager.update_task_state(task_id, "failed", error_vertical_crop=str(e), error_sending_status_update=True)
                pass
        
        finally:
            # Удаляем временные файлы
            try:
                if 'temp_dir' in locals() and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")