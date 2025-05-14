import logging
import os
import requests
import subprocess
import time
import random
from datetime import datetime
from urllib.parse import urlparse, unquote
from typing import Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Временная директория для скачанных видео
TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "temp_videos")
os.makedirs(TEMP_DIR, exist_ok=True)

# Максимальное количество попыток для обработки видео
MAX_RETRIES = 2

def convert_time_to_seconds(time_str: str) -> int:
    """
    Преобразует строку времени в секунды.
    
    Args:
        time_str (str): Время в формате 'HH:MM:SS', 'MM:SS', или 'SS'
        
    Returns:
        int: Время в секундах
    """
    parts = time_str.split(':')
    if len(parts) == 3:  # HH:MM:SS
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:  # MM:SS
        return int(parts[0]) * 60 + int(parts[1])
    else:  # SS
        return int(parts[0])

def format_time(seconds: int) -> str:
    """
    Преобразует секунды в формат HH:MM:SS.
    
    Args:
        seconds (int): Время в секундах
        
    Returns:
        str: Время в формате HH:MM:SS
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def get_yadisk_video(public_link: str, start_time: str, end_time: str, request_id: str = "", user_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Скачивает видео с Яндекс.Диска по публичной ссылке и вырезает фрагмент
    
    Args:
        public_link (str): Публичная ссылка на файл Яндекс.Диска
        start_time (str): Время начала фрагмента в формате HH:MM:SS или MM:SS
        end_time (str): Время окончания фрагмента в формате HH:MM:SS или MM:SS
        request_id (str): Уникальный ID запроса (для логирования)
        user_id (str): ID пользователя (для логирования)
        
    Returns:
        dict: Словарь с информацией о видео и путь к файлу, или None при ошибке
    """
    # Счетчик попыток
    retry_count = 0
    
    while retry_count <= MAX_RETRIES:
        try:
            # Преобразуем временные метки в секунды
            start_seconds = convert_time_to_seconds(start_time)
            end_seconds = convert_time_to_seconds(end_time)
            
            # Генерируем уникальное имя файла с временной меткой и случайным компонентом
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            random_suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=6))
            
            # Включаем user_id и request_id в путь для лучшего отслеживания
            file_prefix = f"{timestamp}_{random_suffix}"
            if user_id:
                file_prefix = f"{user_id}_{file_prefix}"
            
            # Строим полный путь к видео
            final_path = os.path.join(TEMP_DIR, f"{file_prefix}_{start_seconds}_{end_seconds}.mp4")
            
            # Убеждаемся, что директория существует
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            
            # Проверяем, ссылка на папку или на конкретный файл
            parts = public_link.split('/')
            if '/d/' in public_link and len(parts) > 5:
                # Это ссылка на файл внутри папки
                base_folder_url = '/'.join(parts[:5])  # получаем ссылку на саму папку
                path_inside = '/'.join(parts[5:])      # получаем путь к файлу внутри папки
                
                # Получаем метаданные файла внутри публичной папки
                api_url = "https://cloud-api.yandex.net/v1/disk/public/resources"
                params = {
                    "public_key": base_folder_url,
                    "path": f"/{path_inside}"
                }
                
                response = requests.get(api_url, params=params)
                response.raise_for_status()
                file_data = response.json()
                
                # Получаем ссылку на скачивание
                download_api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
                download_params = {
                    "public_key": base_folder_url,
                    "path": f"/{path_inside}"
                }
                
                title = os.path.basename(path_inside)
            else:
                # Это прямая ссылка на файл
                download_api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
                download_params = {"public_key": public_link}
                title = os.path.basename(public_link)
            
            # Получаем ссылку на скачивание
            response = requests.get(download_api_url, params=download_params)
            response.raise_for_status()
            
            download_url = response.json().get("href")
            if not download_url:
                raise ValueError("Не удалось получить ссылку на скачивание")
            
            # Если указаны таймкоды, используем FFmpeg для прямой обработки потока
            logger.info(f"Загрузка и вырезание фрагмента видео из Яндекс.Диска для запроса {request_id}")
            
            # Создаем новую команду FFmpeg по запрошенному формату
            ffmpeg_cmd = ['ffmpeg', '-ss', format_time(start_seconds), '-i', download_url]
            
            # Вычисляем длительность
            if end_seconds > start_seconds:
                duration = end_seconds - start_seconds
                ffmpeg_cmd.extend(['-t', str(duration)])
            else:
                # Если длительность не указана или некорректна, используем 5 секунд по умолчанию
                ffmpeg_cmd.extend(['-t', '5'])
            
            # Добавляем параметры кодирования согласно запрошенному формату
            ffmpeg_cmd.extend(['-c:v', 'copy', '-c:a', 'aac', '-strict', 'experimental', '-avoid_negative_ts', '1', final_path])
            
            logger.info(f"Выполняем команду: {' '.join(ffmpeg_cmd)}")
            
            # Выполняем команду FFmpeg
            process = subprocess.Popen(
                ffmpeg_cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # Ждем завершения процесса
            stdout, stderr = process.communicate()
            
            # Проверяем успешность выполнения
            if process.returncode != 0:
                logger.error(f"Ошибка FFmpeg: {stderr}")
                # Если не финальная попытка, пробуем снова
                if retry_count < MAX_RETRIES:
                    retry_count += 1
                    time.sleep(2 * retry_count)  # Экспоненциальный откат
                    continue
            
            # Проверяем, что файл существует и имеет ненулевой размер
            if not os.path.exists(final_path) or os.path.getsize(final_path) == 0:
                logger.error(f"Файл не был создан или имеет нулевой размер: {final_path}")
                if retry_count < MAX_RETRIES:
                    retry_count += 1
                    time.sleep(2 * retry_count)
                    continue
                return None
            
            # Возвращаем информацию о видео
            return {
                "url": public_link,
                "start_time": start_time,
                "end_time": end_time,
                "file_path": final_path,
                "title": title,
                "duration": end_seconds - start_seconds,
                "source": "yadisk"
            }
            
        except Exception as e:
            logger.error(f"Ошибка обработки видео с Яндекс.Диска: {e}")
            if retry_count < MAX_RETRIES:
                retry_count += 1
                time.sleep(2 * retry_count)
                continue
            return None
    
    # Если мы дошли до сюда, все попытки не удались
    logger.error(f"Не удалось обработать видео с Яндекс.Диска после {MAX_RETRIES} попыток: {public_link}")
    return None 