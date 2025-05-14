import requests
import os
import subprocess
import tempfile
from urllib.parse import urlparse, unquote

def download_yadisk_video(public_link, output_path=None, start_time=None, end_time=None):
    """
    Скачивает видео с Яндекс.Диска по публичной ссылке с возможностью вырезать фрагмент
    
    Args:
        public_link (str): Публичная ссылка на файл Яндекс.Диска
        output_path (str, optional): Путь для сохранения файла. Если не указан,
                                    используется оригинальное имя файла.
        start_time (str, optional): Время начала фрагмента в формате HH:MM:SS или в секундах
        end_time (str, optional): Время окончания фрагмента в формате HH:MM:SS или в секундах
    
    Returns:
        str: Путь к сохраненному файлу
    """
    try:
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
        else:
            # Это прямая ссылка на файл
            download_api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
            download_params = {"public_key": public_link}
        
        # Получаем ссылку на скачивание
        response = requests.get(download_api_url, params=download_params)
        response.raise_for_status()
        
        download_url = response.json().get("href")
        if not download_url:
            raise ValueError("Не удалось получить ссылку на скачивание")
        
        # Получаем имя файла из URL, если путь не указан
        if not output_path:
            # Сначала пробуем получить из исходной ссылки
            if '/d/' in public_link and len(parts) > 5:
                output_path = os.path.basename(parts[-1])
            else:
                # Пробуем получить из URL скачивания
                parsed_url = urlparse(download_url)
                filename = os.path.basename(unquote(parsed_url.path))
                # Удаляем параметры запроса из имени файла
                if "?" in filename:
                    filename = filename.split("?")[0]
                output_path = filename
        
        # Если указаны таймкоды, используем FFmpeg для прямой обработки потока
        if start_time is not None or end_time is not None:
            print(f"Загрузка и вырезание фрагмента видео напрямую...")
            
            # Создаем команду для FFmpeg
            ffmpeg_cmd = ['ffmpeg']
            
            # Добавляем параметр начала, если указан
            # Для оптимизации ставим seek перед входным файлом, что позволяет FFmpeg 
            # запрашивать данные только с нужной позиции
            if start_time is not None:
                ffmpeg_cmd.extend(['-ss', str(start_time)])
            
            # Добавляем входной файл (URL)
            ffmpeg_cmd.extend(['-i', download_url])
            
            # Если указано время окончания, добавляем его после входного файла
            if end_time is not None:
                # Если указано и начало, и конец, вычисляем длительность
                if start_time is not None:
                    # Преобразуем строки времени в секунды для расчета длительности
                    start_seconds = time_to_seconds(start_time)
                    end_seconds = time_to_seconds(end_time)
                    duration = end_seconds - start_seconds
                    ffmpeg_cmd.extend(['-t', str(duration)])
                else:
                    ffmpeg_cmd.extend(['-to', str(end_time)])
            
            # Добавляем параметры кодирования и выходной файл
            # Используем -c copy для быстрого копирования без перекодирования
            ffmpeg_cmd.extend(['-c', 'copy', '-avoid_negative_ts', '1', output_path])
            
            print(f"Выполняем команду: {' '.join(ffmpeg_cmd)}")
            process = subprocess.Popen(
                ffmpeg_cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # Обрабатываем вывод FFmpeg для отображения прогресса
            while True:
                output = process.stderr.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    # Ищем строки с информацией о прогрессе
                    if "time=" in output:
                        print(f"\r{output.strip()}", end="")
            
            # Проверяем успешность выполнения
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd)
            
            print(f"\nФрагмент видео успешно сохранен в {output_path}")
        else:
            # Скачиваем файл целиком
            print(f"Скачивание файла в {output_path}...")
            download_file(download_url, output_path)
            print(f"Файл успешно скачан в {output_path}")
        
        return output_path
    
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при запросе: {e}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при выполнении FFmpeg: {e}")
        return None
    except Exception as e:
        print(f"Произошла ошибка: {e}")
        return None

def download_file(url, output_path):
    """
    Скачивает файл по URL с отображением прогресса
    """
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('content-length', 0))
        
        with open(output_path, 'wb') as f:
            if total_size == 0:
                f.write(r.content)
            else:
                downloaded = 0
                total_size_mb = total_size / (1024 * 1024)
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = int(downloaded / total_size * 100)
                        downloaded_mb = downloaded / (1024 * 1024)
                        print(f"\rПрогресс: {percent}% ({downloaded_mb:.2f}/{total_size_mb:.2f} МБ)", end="")
                print()

def format_time(time_input):
    """
    Преобразует ввод времени в формат, подходящий для FFmpeg
    
    Args:
        time_input (str): Время в формате HH:MM:SS, MM:SS или в секундах
    
    Returns:
        str: Время в формате, пригодном для FFmpeg
    """
    if time_input is None:
        return None
    
    # Если введено число секунд
    if time_input.isdigit():
        seconds = int(time_input)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    # Если введено время в формате MM:SS или HH:MM:SS
    parts = time_input.split(':')
    if len(parts) == 2:  # MM:SS
        return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    elif len(parts) == 3:  # HH:MM:SS
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
    
    # Если формат не распознан, возвращаем как есть
    return time_input

def time_to_seconds(time_str):
    """
    Преобразует строку времени в секунды
    
    Args:
        time_str (str): Время в формате HH:MM:SS или в секундах
        
    Returns:
        int: Время в секундах
    """
    if time_str.isdigit():
        return int(time_str)
    
    parts = time_str.split(':')
    if len(parts) == 3:  # HH:MM:SS
        h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:  # MM:SS
        m, s = map(int, parts)
        return m * 60 + s
    
    return 0

if __name__ == "__main__":
    # Пример использования
    public_link = input("Введите публичную ссылку на видео Яндекс.Диска: ")
    output_path = input("Введите путь для сохранения (оставьте пустым для автоматического выбора): ")
    
    extract_fragment = input("Вырезать фрагмент видео? (да/нет): ").lower() in ['да', 'yes', 'y', '1', 'true']
    
    start_time = None
    end_time = None
    
    if extract_fragment:
        start_time_input = input("Введите время начала (HH:MM:SS или в секундах, оставьте пустым для начала видео): ")
        end_time_input = input("Введите время окончания (HH:MM:SS или в секундах, оставьте пустым для конца видео): ")
        
        if start_time_input:
            start_time = format_time(start_time_input)
        
        if end_time_input:
            end_time = format_time(end_time_input)
    
    if not output_path:
        output_path = None
        
    download_yadisk_video(public_link, output_path, start_time, end_time) 