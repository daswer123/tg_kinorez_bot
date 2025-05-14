import asyncio
import logging
import os
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo
from telethon.errors import MessageIdInvalidError, MessageNotModifiedError
import instructor
from openai import OpenAI
from pydantic import BaseModel
from typing import List, Dict, Set
from dotenv import load_dotenv
import time

import sqlite3

from funcs import get_video_by_url_and_timings
from auth_db import AuthDB
from config import AUTH_PASSWORD, AUTH_ENABLED, ADMIN_ID

# Загружаем переменные окружения из .env файла
dotenv_path = os.path.join(os.path.dirname(__file__), '.env.template')
load_dotenv(dotenv_path)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define Pydantic model for structured output from LLM
class YoutubeVideo(BaseModel):
    url: str
    start_time: str
    end_time: str
    correct_timings: bool
    error_details: str = ""

# Конфигурация Telethon из переменных окружения
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

# Проверяем, что все необходимые переменные определены
if not API_ID or not API_HASH or not PHONE:
    raise ValueError("Необходимо указать API_ID, API_HASH и PHONE в файле .env")

# OpenRouter конфигурация
OPENROUTER_API_KEY = "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"

BASE_URL = "https://openrouter.ai/api/v1"

# Инициализация AI клиента
client = instructor.from_openai(
    OpenAI(base_url=BASE_URL, api_key=OPENROUTER_API_KEY),
    mode=instructor.Mode.JSON
)

# Временная директория для загрузок
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "temp_videos")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Инициализация базы данных пользователей
auth_db = AuthDB(os.path.join(os.path.dirname(__file__), "users.db"))

# Создаем экземпляр клиента Telethon
tg_client = TelegramClient('user_session', API_ID, API_HASH)

# Хранилище для пользователей, ожидающих ввода пароля
waiting_for_password: Set[int] = set()

# Функция для безопасного редактирования сообщений
async def safe_edit_message(message, text):
    """Безопасно редактирует сообщение, обрабатывая возможные ошибки"""
    try:
        if message:
            return await message.edit(text=text)
    except (MessageIdInvalidError, MessageNotModifiedError) as e:
        logger.warning(f"Не удалось отредактировать сообщение: {str(e)}")
        try:
            # Если редактирование не удалось, попробуем отправить новое сообщение
            return await tg_client.send_message(message.chat_id, text)
        except Exception as send_error:
            logger.error(f"Не удалось отправить новое сообщение: {str(send_error)}")
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения: {str(e)}")
    return None

# Функция для извлечения данных из текста с помощью LLM
def extract_video_data(text):
    """Extract structured data from text using LLM"""
    try:
        response = client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            response_model=List[YoutubeVideo],
            messages=[
                {
                    "role": "user", 
                    "content": f"Извлеки данные из текста: {text} необходимо получить чистую ссылку, "
                              "время начала и время конца. Просто извлеки данные, не добавляй ничего лишнего. "
                              "Время извлекай в формате 00:00:00 без милисекунд. "
                              "Если тайминги не корректные или не подходят, верни correct_timings=False и "
                              "добавь поле error_details с пояснением проблемы, например: 'Конечное время меньше начального', "
                              "'Некорректный формат времени', 'Тайминги выходят за пределы длительности видео' и т.д."
                }
            ],
        )
        return response
    except Exception as e:
        logger.error(f"Error extracting video data: {e}")
        return None

# Middleware для авторизации пользователей
@tg_client.on(events.NewMessage)
async def auth_middleware(event):
    """
    Middleware для авторизации пользователей перед обработкой их запросов
    """
    # Если авторизация отключена, пропускаем все сообщения
    if not AUTH_ENABLED:
        return
    
    # Получаем ID пользователя
    sender = await event.get_sender()
    user_id = sender.id
    
    # Проверяем, авторизован ли пользователь
    if auth_db.is_user_authorized(user_id):
        # Если авторизован, позволяем продолжить обработку сообщения
        return
    
    # Проверяем, ожидает ли пользователь ввода пароля
    if user_id in waiting_for_password:
        # Пользователь ввел пароль
        password_attempt = event.message.text
        
        if password_attempt == AUTH_PASSWORD:
            # Пароль верный, авторизуем пользователя
            auth_db.add_or_update_user(
                user_id, 
                sender.username, 
                sender.first_name, 
                sender.last_name, 
                is_authorized=True
            )
            
            # Удаляем пользователя из списка ожидающих ввода пароля
            waiting_for_password.remove(user_id)
            
            # Отправляем сообщение об успешной авторизации
            await event.reply("✅ Авторизация успешна! Теперь вы можете пользоваться ботом.")
        else:
            # Неверный пароль
            await event.reply("❌ Неверный пароль. Пожалуйста, попробуйте снова.")
        
        # Прерываем дальнейшую обработку сообщения
        raise events.StopPropagation
    else:
        # Пользователь не авторизован и не ожидает ввода пароля
        # Добавляем информацию о пользователе в базу
        auth_db.add_or_update_user(
            user_id, 
            sender.username, 
            sender.first_name, 
            sender.last_name, 
            is_authorized=False
        )
        
        # Добавляем пользователя в список ожидающих ввода пароля
        waiting_for_password.add(user_id)
        
        # Отправляем запрос на ввод пароля
        await event.reply("🔒 Для использования бота необходима авторизация. Пожалуйста, введите пароль:")
        
        # Прерываем дальнейшую обработку сообщения
        raise events.StopPropagation

# Обработчик команды /start
@tg_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Обработчик команды /start"""
    # Проверяем необходимость авторизации
    sender = await event.get_sender()
    user_id = sender.id
    
    if AUTH_ENABLED and not auth_db.is_user_authorized(user_id):
        # Если пользователь не авторизован, отправляем запрос на авторизацию
        auth_db.add_or_update_user(
            user_id, 
            sender.username, 
            sender.first_name, 
            sender.last_name, 
            is_authorized=False
        )
        
        # Добавляем пользователя в список ожидающих ввода пароля
        waiting_for_password.add(user_id)
        
        # Отправляем запрос на ввод пароля
        await event.reply("🔒 Для использования бота необходима авторизация. Пожалуйста, введите пароль:")
        return
    
    # Стандартное приветственное сообщение
    await event.respond(
        "Привет! Отправь мне ссылку на YouTube видео и таймкоды в любом формате.\n\n"
        "Примеры:\n"
        "• https://www.youtube.com/watch?v=dQw4w9WgXcQ с 1:30 до 2:00\n"
        "• Вырежи с 45 секунды по 1:15 https://youtu.be/dQw4w9WgXcQ\n"
        "• https://www.youtube.com/watch?v=dQw4w9WgXcQ 00:20 - 00:45"
    )

# Обработчик команды /users (только для администратора)
@tg_client.on(events.NewMessage(pattern='^/users$'))
async def users_handler(event):
    """Обработчик команды /users для просмотра списка пользователей"""
    sender = await event.get_sender()
    user_id = sender.id
    
    # Проверяем, является ли пользователь администратором
    # Замените это условие на проверку ID вашего аккаунта
    if user_id == ADMIN_ID:
        users = auth_db.get_all_users()
        
        if not users:
            await event.respond("Список пользователей пуст.")
            return
        
        # Формируем сообщение со списком пользователей
        message = "📊 Список пользователей:\n\n"
        
        for user in users:
            user_id, username, first_name, last_name, is_authorized, created_at = user
            
            status = "✅ Авторизован" if is_authorized else "❌ Не авторизован"
            name = f"{first_name or ''} {last_name or ''}".strip()
            username_str = f"@{username}" if username else "нет"
            
            message += f"ID: {user_id}\n"
            message += f"Имя: {name or 'нет'}\n"
            message += f"Username: {username_str}\n"
            message += f"Статус: {status}\n"
            message += f"Дата регистрации: {created_at}\n\n"
        
        await event.respond(message)
    else:
        # Если пользователь не администратор, игнорируем команду
        pass

# Обработчик команды /stats (только для администратора)
@tg_client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    """Обработчик команды /stats для просмотра общей статистики использования"""
    sender = await event.get_sender()
    user_id = sender.id
    
    # Проверяем, является ли пользователь администратором
    if user_id == ADMIN_ID:
        # Получаем общую статистику
        stats = auth_db.get_total_statistics()
        
        # Формируем сообщение со статистикой
        message = "📊 **Общая статистика использования бота**\n\n"
        
        message += f"👥 **Пользователи:**\n"
        message += f"• Всего пользователей: {stats['total_users']}\n"
        message += f"• Авторизованных пользователей: {stats['authorized_users']}\n\n"
        
        message += f"🔄 **Запросы:**\n"
        message += f"• Всего запросов: {stats['total_requests']}\n"
        message += f"• Успешных запросов: {stats['completed_requests']}\n"
        message += f"• Запросов с ошибками: {stats['error_requests']}\n\n"
        
        # Добавляем информацию о последних запросах
        if stats['recent_requests']:
            message += f"📅 **Последние запросы (за 7 дней):**\n"
            
            # Группируем запросы по дате
            requests_by_date = {}
            for req in stats['recent_requests']:
                date = req['created_at'].split()[0]  # Берем только дату, без времени
                if date not in requests_by_date:
                    requests_by_date[date] = []
                requests_by_date[date].append(req)
            
            # Выводим запросы по дням
            for date in sorted(requests_by_date.keys(), reverse=True):
                message += f"\n📆 **{date}**:\n"
                
                # Добавляем запросы за этот день
                day_requests = requests_by_date[date]
                for i, req in enumerate(day_requests[:5], 1):  # Ограничиваем 5 запросами в день
                    # Формируем информацию о пользователе
                    name = f"{req['first_name'] or ''} {req['last_name'] or ''}".strip() or "Неизвестно"
                    username = f"@{req['username']}" if req['username'] else ""
                    user_info = f"{name} {username}" if name or username else f"ID: {req['user_id']}"
                    
                    # Получаем статус
                    status_emoji = "✅" if req['status'] == 'completed' else "❌" if req['status'] == 'error' else "⏳"
                    
                    # Формируем информацию о запросе
                    request_text = req['request_text']
                    if len(request_text) > 50:
                        request_text = request_text[:47] + "..."
                    
                    
                    # Время запроса
                    time = req['created_at'].split()[1].split('.')[0]
                    
                    # Добавляем базовую информацию о запросе
                    message += f"{i}. {status_emoji} {time} - {user_info}: {request_text}\n"
                    
                    # Получаем все видео для этого запроса
                    videos = auth_db.get_request_videos(req['id'])
                    
                    if videos:
                        # Если слишком много видео, показываем только первые 2
                        videos_to_show = videos[:2]
                        
                        # Если есть видео, добавляем их в сообщение с отступом
                        for j, video in enumerate(videos_to_show, 1):
                            video_url = video['video_url'] or "Нет URL"
                            # Сокращаем URL, если он слишком длинный
                            if len(video_url) > 25:
                                video_url = video_url[:22] + "..."
                            
                            timings = ""
                            if video['start_time'] and video['end_time']:
                                timings = f" ({video['start_time']}-{video['end_time']})"
                            
                            message += f"   • {video_url}{timings}\n"
                        
                        # Если есть еще видео, показываем количество
                        if len(videos) > len(videos_to_show):
                            message += f"   • ...и еще {len(videos) - len(videos_to_show)} видео\n"
                
                # Если запросов больше 5, показываем, сколько еще осталось
                if len(day_requests) > 5:
                    message += f"...и еще {len(day_requests) - 5} запросов за этот день\n"
        
        await event.respond(message)
    else:
        # Если пользователь не администратор, отправляем сообщение об ошибке
        await event.respond("❌ У вас нет доступа к этой команде")

# Обработчик команды /userstats (для администратора или для просмотра своей статистики)
@tg_client.on(events.NewMessage(pattern='/userstats(?:\s+(\d+))?'))
async def userstats_handler(event):
    """Обработчик команды /userstats для просмотра статистики использования определенным пользователем"""
    sender = await event.get_sender()
    sender_id = sender.id
    
    # Получаем ID пользователя из параметра команды (если есть)
    match = event.pattern_match.group(1)
    target_user_id = int(match) if match else sender_id
    
    # Если запрашивается статистика другого пользователя, проверяем права администратора
    if target_user_id != sender_id and sender_id != ADMIN_ID:
        await event.respond("❌ У вас нет доступа к статистике других пользователей")
        return
    
    # Получаем статистику пользователя
    user_requests = auth_db.get_user_statistics(target_user_id)
    
    if not user_requests:
        await event.respond(f"Пользователь с ID {target_user_id} не найден или у него нет запросов")
        return
    
    # Получаем информацию о пользователе
    user_info = user_requests[0]
    name = f"{user_info['first_name'] or ''} {user_info['last_name'] or ''}".strip() or "Неизвестно"
    username = f"@{user_info['username']}" if user_info['username'] else ""
    
    # Формируем сообщение
    message = f"📊 **Статистика пользователя {name} {username} (ID: {target_user_id})**\n\n"
    
    # Подсчитываем общее количество запросов
    total_count = sum(1 for r in user_requests if r['status'] == 'completed' or r['status'] == 'error')
    completed_count = sum(1 for r in user_requests if r['status'] == 'completed')
    error_count = sum(1 for r in user_requests if r['status'] == 'error')
    
    message += f"• Всего запросов: {total_count}\n"
    message += f"• Успешных запросов: {completed_count}\n"
    message += f"• Запросов с ошибками: {error_count}\n\n"
    
    # Последние 10 запросов
    message += "**Последние запросы:**\n"
    
    shown_requests = 0
    for i, req in enumerate(user_requests, 1):
        if shown_requests >= 10:
            break
            
        status_emoji = "✅" if req['status'] == 'completed' else "❌" if req['status'] == 'error' else "⏳"
        req_date = req['created_at'].split('.')[0]  # Удаляем миллисекунды
        
        # Получаем текст запроса
        request_text = req['request_text']
        if len(request_text) > 40:
            request_text = request_text[:37] + "..."
        
        # Добавляем базовую информацию о запросе
        message += f"{shown_requests + 1}. {status_emoji} {req_date} - {request_text}\n"
        
        # Получаем все видео для этого запроса
        videos = auth_db.get_request_videos(req['id'])
        
        if videos:
            # Если есть видео, добавляем их в сообщение
            for j, video in enumerate(videos, 1):
                video_url = video['video_url'] or "Нет URL"
                # Сокращаем URL, если он слишком длинный
                if len(video_url) > 30:
                    video_url = video_url[:27] + "..."
                
                timings = ""
                if video['start_time'] and video['end_time']:
                    timings = f" ({video['start_time']} - {video['end_time']})"
                
                video_status = "✅" if video['status'] == 'completed' else "❌" if video['status'] == 'error' else "⏳"
                
                # Добавляем информацию о видео с отступом
                message += f"   {video_status} Видео {j}: {video_url}{timings}\n"
        else:
            # Если видео не найдены, но запрос существует
            message += f"   Нет данных о видео\n"
        
        # Инкрементируем счетчик показанных запросов
        shown_requests += 1
        
        # Добавляем пустую строку между запросами для лучшей читаемости
        message += "\n"
    
    # Если есть еще запросы, сообщаем об этом
    if len(user_requests) > shown_requests:
        message += f"...и еще {len(user_requests) - shown_requests} запросов\n"
    
    await event.respond(message)

# Главный обработчик сообщений
@tg_client.on(events.NewMessage)
async def message_handler(event):
    """Обработчик входящих сообщений"""
    # Игнорируем команды
    if event.message.text.startswith('/'):
        return
    
    user_text = event.message.text
    sender = await event.get_sender()
    user_id = sender.id
    
    # Логируем запрос в базу данных
    request_id = auth_db.log_request(user_id, user_text)
    
    # Отправляем статусное сообщение
    processing_message = await event.respond("Обрабатываю ваш запрос...")
    
    try:
        # Извлекаем структурированные данные с помощью LLM
        response = extract_video_data(user_text)
        
        if not response:
            await safe_edit_message(
                processing_message,
                "Не удалось распознать ссылку на YouTube или таймкоды. "
                "Пожалуйста, проверьте формат и попробуйте снова."
            )
            
            # Обновляем статус запроса в базе данных
            if request_id:
                auth_db.update_request_status(request_id, "error")
            
            return
        
        # Обрабатываем каждое видео в ответе
        for video in response:
            # Создаем новую запись для каждого видео в рамках одного запроса
            video_request_id = None
            if request_id:
                video_request_id = auth_db.log_video_request(
                    request_id,
                    video.url,
                    video.start_time,
                    video.end_time,
                    "processing"
                )
            
            # Если не удалось создать запись для видео, используем основной ID запроса
            current_request_id = video_request_id or request_id
            
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
                
                await safe_edit_message(
                    processing_message,
                    f"{status_text}\n❌ {error_message}. Пожалуйста, проверьте и попробуйте снова."
                )
                
                # Обновляем статус запроса в базе данных
                if current_request_id:
                    auth_db.update_request_status(current_request_id, "error")
                
                continue
            
            # Обновляем статус
            status_message = await safe_edit_message(
                processing_message,
                f"{status_text}\n⏳ Скачиваю и обрезаю видео..."
            )
            
            # Если не удалось обновить сообщение, используем новое сообщение для следующих обновлений
            if status_message:
                processing_message = status_message
            
            try:
                # Скачиваем видео
                result = get_video_by_url_and_timings(
                    video.url, 
                    video.start_time, 
                    video.end_time, 
                    DOWNLOAD_DIR
                )
                
                if not result:
                    await safe_edit_message(
                        processing_message,
                        f"{status_text}\n❌ Ошибка при обработке видео. Возможно, видео недоступно."
                    )
                    
                    # Обновляем статус запроса в базе данных
                    if current_request_id:
                        auth_db.update_request_status(current_request_id, "error")
                    
                    continue
                
                # Получаем путь к файлу из результата функции
                video_file_path = result.get('file_path')
                
                if not video_file_path or not os.path.exists(video_file_path):
                    logger.error(f"File does not exist: {video_file_path}")
                    await safe_edit_message(
                        processing_message,
                        f"{status_text}\n❌ Ошибка: файл видео не был создан."
                    )
                    
                    # Обновляем статус запроса в базе данных
                    if current_request_id:
                        auth_db.update_request_status(current_request_id, "error")
                    
                    continue
                
                # Проверяем размер файла
                file_size = os.path.getsize(video_file_path)
                logger.info(f"File size: {file_size} bytes")
                
                # Telethon имеет повышенные лимиты на размер файла по сравнению с Bot API
                if file_size > 2 * 1024 * 1024 * 1024:  # 2 GB - примерный лимит для Telethon
                    await safe_edit_message(
                        processing_message,
                        f"{status_text}\n❌ Видео слишком большое для отправки в Telegram (>2GB)."
                    )
                    
                    # Обновляем статус запроса в базе данных
                    if current_request_id:
                        auth_db.update_request_status(current_request_id, "error")
                    
                    continue
                
                # Отправляем видео
                try:
                    # Получаем длительность видео
                    duration = result.get('segment_duration', 0)
                    
                    # Получаем информацию о разрешении видео
                    max_resolution = result.get('max_resolution', 0)
                    resolution_info = ""
                    
                    source = result.get('source', 'YouTube')
                    
                    # Определяем название источника
                    source_name = source
                    
                    # Добавляем предупреждение, если разрешение ниже 1080p
                    if max_resolution and max_resolution < 1080:
                        resolution_info = f"\n⚠️ Видео из источника \"{source_name}\" доступно только в низком качестве ({max_resolution}p)"
                    
                    # Отправляем как видео
                    await tg_client.send_file(
                        event.chat_id,
                        video_file_path,
                        caption=f"Фрагмент: {video.start_time} - {video.end_time}{resolution_info}",
                        attributes=[
                            DocumentAttributeVideo(
                                duration=int(duration),
                                w=1280,  # Примерная ширина
                                h=720,   # Примерная высота
                                supports_streaming=True
                            )
                        ],
                        progress_callback=lambda current, total: logger.info(f"Upload progress: {current}/{total}")
                    )
                    
                    # Обновляем статус запроса в базе данных
                    if current_request_id:
                        auth_db.update_request_status(current_request_id, "completed")
                        
                except Exception as video_error:
                    logger.error(f"Error sending as video: {video_error}")
                    
                    # Пробуем отправить как документ
                    try:
                        await tg_client.send_file(
                            event.chat_id,
                            video_file_path,
                            caption=f"Фрагмент: {video.start_time} - {video.end_time} (отправлен как файл){resolution_info}",
                            force_document=True
                        )
                        
                        # Обновляем статус запроса в базе данных
                        if current_request_id:
                            auth_db.update_request_status(current_request_id, "completed")
                            
                    except Exception as doc_error:
                        logger.error(f"Error sending as document: {doc_error}")
                        await safe_edit_message(
                            processing_message,
                            f"{status_text}\n❌ Не удалось отправить видео: {str(doc_error)}"
                        )
                        
                        # Обновляем статус запроса в базе данных
                        if current_request_id:
                            auth_db.update_request_status(current_request_id, "error")
                            
                        continue
                
                # Удаляем статусное сообщение - делаем это безопасно
                try:
                    await processing_message.delete()
                except Exception as del_error:
                    logger.warning(f"Не удалось удалить сообщение: {str(del_error)}")
                
                # Удаляем временный файл
                try:
                    os.remove(video_file_path)
                    logger.info(f"Removed file: {video_file_path}")
                except Exception as remove_error:
                    logger.error(f"Error removing file: {remove_error}")
                    
            except Exception as e:
                logger.error(f"Error processing video: {e}")
                await safe_edit_message(
                    processing_message,
                    f"{status_text}\n❌ Ошибка при скачивании: {str(e)}"
                )
                
                # Обновляем статус запроса в базе данных
                if current_request_id:
                    auth_db.update_request_status(current_request_id, "error")
                
    except Exception as e:
        logger.error(f"Error in message handler: {e}")
        await safe_edit_message(
            processing_message,
            f"❌ Произошла ошибка: {str(e)}"
        )
        
        # Обновляем статус запроса в базе данных
        if request_id:
            auth_db.update_request_status(request_id, "error")

async def main():
    """Основная функция для запуска бота"""
    logger.info("Starting Telethon client...")
    
    # Запускаем клиент
    await tg_client.start(phone=PHONE)
    
    # Получаем информацию о себе
    me = await tg_client.get_me()
    logger.info(f"Client started successfully as {me.first_name} (@{me.username})")
    
    # Статус авторизации
    if AUTH_ENABLED:
        logger.info(f"Авторизация включена. Пароль: {AUTH_PASSWORD}")
    else:
        logger.info("Авторизация отключена")
    
    # Запускаем бесконечный цикл
    try:
        logger.info("Client is now running. Press Ctrl+C to stop.")
        await tg_client.run_until_disconnected()
    finally:
        await tg_client.disconnect()
        logger.info("Client disconnected")

if __name__ == "__main__":
    asyncio.run(main()) 