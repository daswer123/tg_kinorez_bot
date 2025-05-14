from datetime import datetime
import yt_dlp
from config import ydl_opts
import os

# Определяем форматы с приоритетом от высокого к низкому
FORMAT_PRIORITIES = [
    # 8K форматы
    '571+251', # webm 8K + audio
    # 4K форматы
    '313+251',  # webm 4K + audio 
    '315+251',  # webm 4K+ + audio
    '401+140',  # mp4 4K + audio
    '337+251',  # webm 4K + audio 
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
    '22',       # mp4 720p (с аудио)
    # 480p и ниже
    '135+140',  # mp4 480p + audio
    '18',       # mp4 360p (с аудио)
]

# Функция для получения информации о видео и его скачивания
def get_video_by_url_and_timings(url, start_time, end_time, download_path=None):
    """
    Извлекает информацию о видео по URL и временным меткам, с возможностью скачивания.
    
    Args:
        url (str): URL-адрес YouTube видео
        start_time (str): Время начала в формате 'HH:MM:SS' или 'MM:SS'
        end_time (str): Время окончания в формате 'HH:MM:SS' или 'MM:SS'
        download_path (str, optional): Путь для сохранения видео. Если указан, видео будет скачано.
        
    Returns:
        dict: Словарь с информацией о видео и временными метками, или None при ошибке
    """
    try:
        # Настройки для скачивания, если указан путь
        if download_path:
            # Конвертируем временные метки в секунды
            start_seconds = convert_time_to_seconds(start_time)
            end_seconds = convert_time_to_seconds(end_time)

            # Генерируем уникальное имя файла с таймстампом
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Строим полный путь к видео
            final_path = os.path.join(download_path, f"{timestamp}_{start_seconds}_{end_seconds}")
            
            # Убедимся, что директория существует
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            
            # Формируем строку форматов с приоритетом от высокого к низкому
            format_string = '/'.join(FORMAT_PRIORITIES)
            
            download_opts = ydl_opts.copy()
            download_opts.update({
                'format': format_string,
                'outtmpl': f'{final_path}.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'retries': 10,
                'fragment_retries': 10,
                'youtube_include_dash_manifest': True,  # Включаем DASH манифесты для 4K/8K
            })
            
            # Добавляем параметры для обрезки видео по временным меткам
            if start_seconds < end_seconds:
                download_opts.update({
                    'download_ranges': get_download_ranges(start_seconds, end_seconds),
                    'force_keyframes_at_cuts': True,
                })
            
            # Создаем новый экземпляр с обновленными настройками
            download_ydl = yt_dlp.YoutubeDL(download_opts)
            info = download_ydl.extract_info(url, download=True)
            
            # Проверяем наличие файла после скачивания
            expected_file = f"{final_path}.mp4"
            final_file_path = expected_file  # По умолчанию ожидаем mp4
            
            if not os.path.exists(expected_file):
                # Проверка других возможных расширений
                for ext in ['webm', 'mkv', 'mp4', 'avi']:
                    alt_file = f"{final_path}.{ext}"
                    if os.path.exists(alt_file):
                        final_file_path = alt_file
                        break
                else:
                    print(f"Could not find downloaded file with any known extension")
            else:
                print(f"Found downloaded file: {expected_file}")
        else:
            # Просто получаем информацию без скачивания
            # Установим опции для получения информации о высококачественных форматах
            info_opts = ydl_opts.copy()
            info_opts.update({
                'youtube_include_dash_manifest': True
            })
            info_ydl = yt_dlp.YoutubeDL(info_opts)
            info = info_ydl.extract_info(url, download=False)
            final_path = None
            final_file_path = None
        
        # Преобразование временных меток в секунды
        start_seconds = convert_time_to_seconds(start_time)
        end_seconds = convert_time_to_seconds(end_time)
        
        # Формируем результат
        result = {
            'video_info': info,
            'title': info.get('title', ''),
            'duration': info.get('duration', 0),
            'start_time': start_time,
            'end_time': end_time,
            'start_seconds': start_seconds,
            'end_seconds': end_seconds,
            'segment_duration': end_seconds - start_seconds if end_seconds > start_seconds else 0,
            'source': info.get('extractor', 'YouTube')
        }
        
        # Добавляем информацию о скачанном файле, если был запрос на скачивание
        if download_path:
            result['downloaded'] = True
            result['download_path'] = final_path
            result['filename'] = f"{info.get('title', 'video')}.mp4"
            result['file_path'] = final_file_path  # Добавляем полный путь к файлу
            
            # Добавляем информацию о формате и разрешении
            formats = info.get('formats', [])
            max_height = 0
            for fmt in formats:
                if fmt.get('height'):
                    max_height = max(max_height, fmt.get('height'))
            result['max_resolution'] = max_height
        
        return result
    except Exception as e:
        print(f"Ошибка при обработке видео: {e}")
        return None

# Функция для создания диапазонов скачивания
def get_download_ranges(start_seconds, end_seconds):
    """
    Создает функцию для определения диапазонов скачивания видео.
    
    Args:
        start_seconds (int): Время начала в секундах
        end_seconds (int): Время окончания в секундах
        
    Returns:
        function: Функция, возвращающая список диапазонов для скачивания
    """
    def download_ranges_func(info_dict, ranges_fileobj=None):
        return [{
            'start_time': start_seconds,
            'end_time': end_seconds,
        }]
    return download_ranges_func

# Вспомогательная функция для преобразования времени в секунды
def convert_time_to_seconds(time_str):
    """
    Преобразует строку времени в секунды.
    
    Args:
        time_str (str): Время в формате 'HH:MM:SS', 'MM:SS' или 'SS'
        
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
