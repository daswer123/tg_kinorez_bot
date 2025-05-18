import logging
import os
import yt_dlp
import time
import random
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from pydantic import BaseModel
import instructor
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Temporary directory for downloaded videos
TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "temp_videos")
os.makedirs(TEMP_DIR, exist_ok=True)

# Define Pydantic model for structured output from LLM
class YoutubeVideo(BaseModel):
    url: str
    start_time: str
    end_time: str
    correct_timings: bool
    error_details: str = ""

# Define format priorities from high to low
FORMAT_PRIORITIES = [
    # 1440p
    '308+251',  # webm 1440p + audio
    '400+140',  # mp4 1440p + audio
    # 1080p
    '248+251',  # webm 1080p + audio
    '137+140',  # mp4 1080p + audio
    '299+140',  # mp4 1080p60 + audio
    # 720p
    '247+251',  # webm 720p + audio
    '136+140',  # mp4 720p + audio
    '22',       # mp4 720p (with audio)
    # 480p and lower
    '135+140',  # mp4 480p + audio
    '18',       # mp4 360p (with audio)
]

# Maximum number of retries for video processing
MAX_RETRIES = 2

# Base YouTube DLP options
YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'proxy': settings.proxy_url,
}

def extract_video_data(text: str) -> Optional[List[YoutubeVideo]]:
    """Extract structured data from text using LLM."""
    try:
        # Initialize AI client with instructor
        openai_client = OpenAI(
            # base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key.get_secret_value()
        )
        client = instructor.from_openai(
            openai_client,
            mode=instructor.Mode.JSON
        )
        
        response = client.chat.completions.create(
            model="gpt-4.1-nano",
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

def convert_time_to_seconds(time_str: str) -> int:
    """
    Convert time string to seconds.
    
    Args:
        time_str (str): Time in 'HH:MM:SS', 'MM:SS', or 'SS' format
        
    Returns:
        int: Time in seconds
    """
    parts = time_str.split(':')
    if len(parts) == 3:  # HH:MM:SS
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:  # MM:SS
        return int(parts[0]) * 60 + int(parts[1])
    else:  # SS
        return int(parts[0])

def get_download_ranges(start_seconds: int, end_seconds: int) -> Callable:
    """
    Create a function to define download ranges for a video.
    
    Args:
        start_seconds (int): Start time in seconds
        end_seconds (int): End time in seconds
        
    Returns:
        function: Function that returns a list of ranges to download
    """
    def download_ranges_func(info_dict, ranges_fileobj=None):
        return [{
            'start_time': start_seconds,
            'end_time': end_seconds,
        }]
    return download_ranges_func

def get_video_by_url_and_timings(url: str, start_time: str, end_time: str, request_id: str = "", user_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Extract information about a video by URL and timestamps, with download capability.
    
    Args:
        url (str): YouTube video URL
        start_time (str): Start time in 'HH:MM:SS' or 'MM:SS' format
        end_time (str): End time in 'HH:MM:SS' or 'MM:SS' format
        request_id (str): Unique ID for this request (for logging)
        user_id (str): ID of the user making the request (for logging)
        
    Returns:
        dict: Dictionary with video information and timestamps, or None on error
    """
    # Keep track of retries
    retry_count = 0
    
    while retry_count <= MAX_RETRIES:
        try:
            # Convert timestamps to seconds
            start_seconds = convert_time_to_seconds(start_time)
            end_seconds = convert_time_to_seconds(end_time)

            # Generate unique filename with timestamp and random component to prevent collisions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            random_suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=6))
            
            # Include user_id and request_id in path for better tracing
            file_prefix = f"{timestamp}_{random_suffix}"
            if user_id:
                file_prefix = f"{user_id}_{file_prefix}"
            
            # Build full path to video
            final_path = os.path.join(TEMP_DIR, f"{file_prefix}_{start_seconds}_{end_seconds}")
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            
            # Form format string with priorities from high to low
            format_string = '/'.join(FORMAT_PRIORITIES)
            
            download_opts = YDL_OPTS.copy()
            download_opts.update({
                'format': format_string,
                'outtmpl': f'{final_path}.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'retries': 10,
                'fragment_retries': 10,
                'youtube_include_dash_manifest': True,  # Include DASH manifests for 4K/8K
                'verbose': retry_count > 0,  # Enable verbose on retries for better error info
            })
            
            # Add parameters for cutting video by timestamps
            if start_seconds < end_seconds:
                download_opts.update({
                    'download_ranges': get_download_ranges(start_seconds, end_seconds),
                    'force_keyframes_at_cuts': True,
                })
            
            # Create a new instance with updated settings
            download_ydl = yt_dlp.YoutubeDL(download_opts)
            
            # Log retry attempt if applicable
            if retry_count > 0:
                logger.info(f"Retry attempt {retry_count}/{MAX_RETRIES} for video {url} ({start_time}-{end_time}) for request {request_id}")
                
            # Allow some time between retries to prevent rate limiting or resource contention
            if retry_count > 0:
                time.sleep(2 * retry_count)  # Exponential backoff
                
            info = download_ydl.extract_info(url, download=True)
            
            # Check for file after download
            expected_file = f"{final_path}.mp4"
            final_file_path = expected_file  # Default expect mp4
            
            if not os.path.exists(expected_file):
                # Check other possible extensions
                for ext in ['webm', 'mkv', 'mp4', 'avi']:
                    alt_file = f"{final_path}.{ext}"
                    if os.path.exists(alt_file):
                        final_file_path = alt_file
                        break
                else:
                    logger.error(f"Could not find downloaded file with any known extension for request {request_id}")
                    # Just retry if we couldn't find the file
                    retry_count += 1
                    continue
            
            # Verify the downloaded file using ffmpeg to ensure it's valid
            try:
                check_cmd = [
                    "ffmpeg", 
                    "-v", "error", 
                    "-i", final_file_path, 
                    "-f", "null", 
                    "-"
                ]
                process = subprocess.run(
                    check_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30  # Timeout to prevent hangs
                )
                
                if process.returncode != 0:
                    error_output = process.stderr.decode()
                    logger.error(f"FFmpeg validation failed: {error_output}")
                    
                    # Дополнительно проверяем размер файла
                    file_size = os.path.getsize(final_file_path) if os.path.exists(final_file_path) else 0
                    if file_size < 1024:  # Файл меньше 1KB, вероятно поврежденный
                        logger.error(f"Suspiciously small file ({file_size} bytes), considering it corrupt")
                        # Если на финальной попытке
                        if retry_count == MAX_RETRIES:
                            logger.warning(f"Using potentially corrupt file after all retries exhausted for request {request_id}")
                        else:
                            # Удаляем поврежденный файл и пробуем заново
                            if os.path.exists(final_file_path):
                                os.remove(final_file_path)
                            retry_count += 1
                            continue
                    else:
                        # Файл имеет нормальный размер, проверим его дополнительно
                        try:
                            # Проверяем продолжительность видео с помощью ffprobe
                            duration_cmd = [
                                "ffprobe", 
                                "-v", "error",
                                "-show_entries", "format=duration",
                                "-of", "default=noprint_wrappers=1:nokey=1",
                                final_file_path
                            ]
                            duration_process = subprocess.run(
                                duration_cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                timeout=15
                            )
                            
                            if duration_process.returncode == 0:
                                duration_str = duration_process.stdout.decode().strip()
                                try:
                                    duration = float(duration_str)
                                    # Если длительность слишком короткая, считаем видео поврежденным
                                    expected_duration = end_seconds - start_seconds
                                    if duration < expected_duration * 0.5:  # меньше 50% ожидаемой длительности
                                        logger.error(f"Video duration too short: {duration}s (expected ~{expected_duration}s)")
                                        # Если на финальной попытке
                                        if retry_count == MAX_RETRIES:
                                            logger.warning(f"Using short video after all retries exhausted for request {request_id}")
                                        else:
                                            # Удаляем поврежденный файл и пробуем заново
                                            if os.path.exists(final_file_path):
                                                os.remove(final_file_path)
                                            retry_count += 1
                                            continue
                                except (ValueError, TypeError) as e:
                                    logger.error(f"Could not parse video duration: {duration_str}: {e}")
                            else:
                                logger.error(f"ffprobe failed: {duration_process.stderr.decode()}")
                        except Exception as probe_error:
                            logger.error(f"Error checking video duration: {probe_error}")
                            
                        # Решаем, использовать ли файл или удалить его и повторить попытку
                        if retry_count == MAX_RETRIES:
                            logger.warning(f"Using potentially corrupt file after all retries exhausted for request {request_id}")
                        else:
                            # Удаляем поврежденный файл и пробуем заново
                            if os.path.exists(final_file_path):
                                os.remove(final_file_path)
                            retry_count += 1
                            continue
            except subprocess.TimeoutExpired:
                logger.error(f"FFmpeg validation timed out for request {request_id}")
                if retry_count < MAX_RETRIES:
                    retry_count += 1
                    continue
            except Exception as validation_error:
                logger.error(f"Error during validation: {validation_error}")
                if retry_count < MAX_RETRIES:
                    retry_count += 1
                    continue
            
            # Form result
            result = {
                'video_info': info,
                'title': info.get('title', ''),
                'duration': info.get('duration', 0),
                'start_time': start_time,
                'end_time': end_time,
                'start_seconds': start_seconds,
                'end_seconds': end_seconds,
                'segment_duration': end_seconds - start_seconds if end_seconds > start_seconds else 0,
                'source': info.get('extractor', 'YouTube'),
                'downloaded': True,
                'download_path': final_path,
                'filename': f"{info.get('title', 'video')}.mp4",
                'file_path': final_file_path,  # Add full path to file
                'request_id': request_id,
                'user_id': user_id,
                'url': url
            }
            
            # Add format and resolution information
            formats = info.get('formats', [])
            max_height = 0
            for fmt in formats:
                if fmt.get('height'):
                    max_height = max(max_height, fmt.get('height'))
            result['max_resolution'] = max_height
            
            return result
            
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Download error on {'retry ' + str(retry_count) if retry_count > 0 else 'initial attempt'}: {e}")
            if retry_count < MAX_RETRIES:
                retry_count += 1
            else:
                logger.error(f"Failed to download video after {MAX_RETRIES} retries: {e}")
                return None
                
        except Exception as e:
            logger.error(f"Error processing video on {'retry ' + str(retry_count) if retry_count > 0 else 'initial attempt'}: {e}")
            if retry_count < MAX_RETRIES:
                retry_count += 1
            else:
                logger.error(f"Failed to process video after {MAX_RETRIES} retries: {e}")
                return None