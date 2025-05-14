import logging
import os
import yt_dlp
import time
import random
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

# Temporary directory for downloaded videos
TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "temp_videos")
os.makedirs(TEMP_DIR, exist_ok=True)

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
    'proxy': settings.proxy_url if hasattr(settings, 'proxy_url') else None,
}

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

def get_vk_video(url: str, start_time: str, end_time: str, request_id: str = "", user_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Extract information about a VK video by URL and timestamps, with download capability.
    
    Args:
        url (str): VK video URL
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
                'format': 'best',
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
                logger.info(f"Retry attempt {retry_count}/{MAX_RETRIES} for VK video {url} ({start_time}-{end_time}) for request {request_id}")
                
            # Allow some time between retries to prevent rate limiting or resource contention
            if retry_count > 0:
                time.sleep(2 * retry_count)  # Exponential backoff
                
            info = download_ydl.extract_info(url, download=True)
            
            # Check for file after download
            expected_file = f"{final_path}.mp4"
            
            if not os.path.exists(expected_file):
                # Check other possible extensions
                for ext in ['webm', 'mkv', 'mp4', 'avi']:
                    alt_file = f"{final_path}.{ext}"
                    if os.path.exists(alt_file):
                        expected_file = alt_file
                        break
                else:
                    logger.error(f"Could not find downloaded file with any known extension for request {request_id}")
                    # Just retry if we couldn't find the file
                    retry_count += 1
                    continue
            
            # Return information about the video
            return {
                "url": url,
                "start_time": start_time,
                "end_time": end_time,
                "file_path": expected_file,
                "title": info.get('title', 'Unknown'),
                "duration": info.get('duration', 0),
                "source": "vk"
            }
            
        except Exception as e:
            logger.error(f"Error processing VK video: {e}")
            retry_count += 1
            
    # If we reach here, all retries failed
    logger.error(f"Failed to download VK video after {MAX_RETRIES} retries: {url}")
    return None 