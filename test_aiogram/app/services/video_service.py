import logging
import re
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from app.services.youtube import extract_video_data, get_video_by_url_and_timings
from app.services.vk_video import get_vk_video 
from app.services.yadisk_video import get_yadisk_video

logger = logging.getLogger(__name__)

class VideoSource(BaseModel):
    """Модель для представления источника видео"""
    platform: str  # youtube, vk, yadisk
    url: str
    start_time: str
    end_time: str
    correct_timings: bool = True
    error_details: str = ""

def detect_video_source(url: str) -> str:
    """
    Определяет платформу видео по URL
    
    Args:
        url (str): URL видео
        
    Returns:
        str: Название платформы (youtube, vk, yadisk или unknown)
    """
    # Паттерны для определения платформы
    youtube_patterns = [
        r'(youtube\.com|youtu\.be)',
        r'(youtube\.com\/shorts)'
    ]
    
    vk_patterns = [
        r'(vk\.com\/video)',
        r'(vk\.com\/.*video)',
        r'(vkvideo\.ru\/video)',
        r'(vk\.com\/-\d+_\d+)',
        r'(vk\.com\/.*-\d+_\d+)'
    ]
    
    yadisk_patterns = [
        r'(disk\.yandex\.(ru|com))',
        r'(yadi\.sk)'
    ]
    
    # Проверяем URL на соответствие паттернам
    for pattern in youtube_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return "youtube"
    
    for pattern in vk_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return "vk"
    
    for pattern in yadisk_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return "yadisk"
    
    return "unknown"

async def process_multi_source_request(text: str) -> List[VideoSource]:
    """
    Обрабатывает текст пользователя и извлекает информацию о видео из разных источников
    
    Args:
        text (str): Текст запроса пользователя
    
    Returns:
        List[VideoSource]: Список объектов VideoSource с информацией о видео
    """
    # Используем существующую функцию для извлечения данных
    videos_data = extract_video_data(text)
    
    if not videos_data:
        return []
    
    # Определяем источник каждого видео и формируем список
    video_sources = []
    
    for video in videos_data:
        platform = detect_video_source(video.url)
        
        # Если платформа не определена, пытаемся обработать как YouTube
        if platform == "unknown":
            platform = "youtube"
        
        video_source = VideoSource(
            platform=platform,
            url=video.url,
            start_time=video.start_time,
            end_time=video.end_time,
            correct_timings=video.correct_timings,
            error_details=video.error_details if hasattr(video, 'error_details') else ""
        )
        
        video_sources.append(video_source)
    
    return video_sources

async def download_video_fragment(video: VideoSource, request_id: str = "", user_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Скачивает фрагмент видео с указанной платформы
    
    Args:
        video (VideoSource): Информация о видео
        request_id (str): ID запроса
        user_id (str): ID пользователя
    
    Returns:
        Optional[Dict[str, Any]]: Информация о скачанном видео или None при ошибке
    """
    try:
        if not video.correct_timings:
            logger.warning(f"Некорректные тайминги для видео {video.url}: {video.error_details}")
            return None
        
        if video.platform == "youtube":
            # Используем существующую функцию для YouTube
            result = get_video_by_url_and_timings(
                video.url, 
                video.start_time, 
                video.end_time, 
                request_id, 
                user_id
            )
            if result:
                result["source"] = "youtube"
            return result
            
        elif video.platform == "vk":
            # Используем функцию для VK
            return get_vk_video(
                video.url, 
                video.start_time, 
                video.end_time, 
                request_id, 
                user_id
            )
            
        elif video.platform == "yadisk":
            # Используем функцию для Яндекс.Диска
            return get_yadisk_video(
                video.url, 
                video.start_time, 
                video.end_time, 
                request_id, 
                user_id
            )
            
        else:
            logger.error(f"Неизвестная платформа: {video.platform}")
            return None
            
    except Exception as e:
        logger.error(f"Ошибка при скачивании видео {video.url}: {e}")
        return None 