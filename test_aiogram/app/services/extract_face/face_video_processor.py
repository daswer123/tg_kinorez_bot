import cv2
import os
import numpy as np
import subprocess
import shutil
import time
import re

def check_ffmpeg():
    """Проверяет, доступен ли FFmpeg в системном PATH."""
    if shutil.which("ffmpeg"):
        print("FFmpeg найден.")
        return True
    else:
        print("Ошибка: FFmpeg не найден в системном PATH.")
        print("Пожалуйста, установите FFmpeg и убедитесь, что он доступен из командной строки.")
        print("Скачать можно здесь: https://ffmpeg.org/download.html")
        return False

def get_video_duration(video_path):
    """Получает продолжительность видео в секундах с помощью FFprobe."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except:
        return None

def parse_ffmpeg_time(line):
    """Парсит строку прогресса FFmpeg и возвращает текущее время в секундах."""
    # Ищем время в формате time=00:01:23.45
    time_match = re.search(r'time=(\d+):(\d+):(\d+)\.(\d+)', line)
    if time_match:
        hours = int(time_match.group(1))
        minutes = int(time_match.group(2))
        seconds = int(time_match.group(3))
        centiseconds = int(time_match.group(4))
        return hours * 3600 + minutes * 60 + seconds + centiseconds / 100
    return None

def run_ffmpeg_with_prints(cmd, face_id, video_duration=None):
    """Запускает FFmpeg с выводом прогресса через принты."""
    print(f"\n🎬 Начинаю обработку лица {face_id}...")
    
    if video_duration:
        print(f"📏 Общая длительность видео: {video_duration:.1f} сек")
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        last_print_time = 0
        print_interval = 5  # Принтить каждые 5 секунд
        
        while True:
            output = process.stderr.readline()
            if output == '' and process.poll() is not None:
                break
            
            if output:
                current_time = parse_ffmpeg_time(output)
                if current_time and video_duration:
                    # Принтим прогресс каждые 5 секунд или при значительном изменении
                    if current_time - last_print_time >= print_interval or current_time == video_duration:
                        progress_percent = (current_time / video_duration) * 100
                        print(f"⏳ Лицо {face_id}: {current_time:.1f}/{video_duration:.1f} сек ({progress_percent:.1f}%)")
                        last_print_time = current_time
        
        process.wait()
        
        if process.returncode == 0:
            print(f"✅ Лицо {face_id} успешно обработано!")
            return True, None
        else:
            print(f"❌ Ошибка при обработке лица {face_id}")
            return False, "FFmpeg завершился с ошибкой"
            
    except Exception as e:
        print(f"❌ Неожиданная ошибка для лица {face_id}: {e}")
        return False, str(e)

def split_video_by_face_ffmpeg(
    video_path: str,
    haar_cascade_path: str,
    output_dir: str,
    padding_factor: float = 1.8,
    target_aspect_ratio: float = 9.0 / 16.0,
    output_width: int = 720,
    output_height: int = 1280,
    initial_detection_frames: int = 10,
    offset_x: list = None,  # 🔥 НОВОЕ: Офсеты по X для каждого лица [x1, x2, ...]
    offset_y: list = None   # 🔥 НОВОЕ: Офсеты по Y для каждого лица [y1, y2, ...]
):
    """
    Находит лица в начале видео, рассчитывает статичные области кадрирования
    и использует FFmpeg для быстрой нарезки видео на отдельные файлы для каждого лица.
    
    Args:
        video_path (str): Путь к исходному видеофайлу.
        haar_cascade_path (str): Путь к файлу каскада Хаара (.xml).
        output_dir (str): Папка для сохранения итоговых видеофайлов.
        padding_factor (float): Коэффициент отступа вокруг лица (1.0 = нет отступа).
        target_aspect_ratio (float): Целевое соотношение сторон (ширина / высота).
        output_width (int): Ширина выходного видео.
        output_height (int): Высота выходного видео.
        initial_detection_frames (int): Количество первых кадров для поиска лиц.
        offset_x (list): Смещение по X для каждого лица. Например: [10, -20] для 2 лиц
        offset_y (list): Смещение по Y для каждого лица. Например: [5, -15] для 2 лиц
    """
    print("🚀 --- Начало обработки видео ---")
    start_total_time = time.time()
    
    # Подготавливаем офсеты
    if offset_x is None:
        offset_x = []
    if offset_y is None:
        offset_y = []
    
    # 0. Проверка наличия FFmpeg
    if not check_ffmpeg():
        return False, []
    
    # 1. Проверка входных файлов и создание папки вывода
    if not os.path.exists(video_path):
        print(f"❌ Ошибка: Исходное видео не найдено: {video_path}")
        return False, []
    
    if not os.path.exists(haar_cascade_path):
        print(f"❌ Ошибка: Файл каскада Хаара не найден: {haar_cascade_path}")
        return False, []
    
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(f"📁 Создана папка для выходных видео: {output_dir}")
        except OSError as e:
            print(f"❌ Ошибка создания папки {output_dir}: {e}")
            return False, []
    
    # Получаем длительность видео
    video_duration = get_video_duration(video_path)
    if video_duration:
        print(f"⏱️ Длительность видео: {video_duration:.2f} секунд ({video_duration/60:.1f} минут)")
    
    # 2. Загрузка каскада Хаара
    face_cascade = cv2.CascadeClassifier(haar_cascade_path)
    if face_cascade.empty():
         print(f"❌ Ошибка: Не удалось загрузить каскад Хаара из {haar_cascade_path}")
         return False, []
    
    # 3. Открытие видеофайла с помощью OpenCV для анализа
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"❌ Ошибка: Не удалось открыть видео для анализа: {video_path}")
        return False, []
    
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"📊 Анализ видео: {video_path} ({frame_width}x{frame_height} @ {fps:.2f} FPS)")
    
    # 4. Поиск лиц в начальных кадрах
    initial_faces_coords = []
    frames_processed_for_detection = 0
    print(f"🔍 Ищу лица в первых {initial_detection_frames} кадрах...")
    
    for i in range(initial_detection_frames):
        ret, frame = cap.read()
        if not ret:
            print(f"⚠️ Предупреждение: Видео закончилось раньше, чем обработано {initial_detection_frames} кадров для поиска лиц.")
            break
        
        frames_processed_for_detection += 1
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray_frame, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
        
        print(f"📷 Кадр {i+1}: найдено {len(faces)} лиц")
        
        if len(faces) > 0:
            print(f"🎯 Найдены лица ({len(faces)}) в кадре {i+1}. Использую их координаты.")
            initial_faces_coords = faces.tolist()
            break
    
    if not initial_faces_coords:
        print(f"❌ Ошибка: Лица не найдены в первых {frames_processed_for_detection} кадрах.")
        cap.release()
        return False, []
    
    cap.release()
    print("✅ Анализ кадров завершен, OpenCV ресурсы освобождены.")
    
    # 5. Расчет финальных областей кадрирования с учетом офсетов
    crop_regions = []
    print("📐 Рассчитываю области кадрирования...")
    
    if offset_x or offset_y:
        print(f"🎯 Применяю офсеты: X={offset_x}, Y={offset_y}")
    
    for i, (x, y, w, h) in enumerate(initial_faces_coords):
        # 🔥 Применяем офсеты
        face_offset_x = offset_x[i] if i < len(offset_x) else 0
        face_offset_y = offset_y[i] if i < len(offset_y) else 0
        
        print(f"👤 Лицо {i+1}: базовые координаты (x={x}, y={y}, w={w}, h={h})")
        if face_offset_x != 0 or face_offset_y != 0:
            print(f"   🎯 Применяю офсет: X+{face_offset_x}, Y+{face_offset_y}")
        
        center_x = x + w / 2 + face_offset_x  # Добавляем офсет к центру
        center_y = y + h / 2 + face_offset_y  # Добавляем офсет к центру
        
        base_dimension = max(w, h) * padding_factor
        
        if target_aspect_ratio > 1:
            crop_w = int(base_dimension)
            crop_h = int(base_dimension / target_aspect_ratio)
        elif target_aspect_ratio < 1:
             crop_h = int(base_dimension)
             crop_w = int(base_dimension * target_aspect_ratio)
        else:
            crop_w = int(base_dimension)
            crop_h = int(base_dimension)

        crop_x = int(center_x - crop_w / 2)
        crop_y = int(center_y - crop_h / 2)
        
        # Коррекция выхода за границы кадра
        crop_x = max(0, crop_x)
        crop_y = max(0, crop_y)
        
        if crop_x + crop_w > frame_width:
            crop_w = frame_width - crop_x
            new_h = int(crop_w / target_aspect_ratio)
            crop_h = new_h
            crop_y = max(0, int(center_y - crop_h / 2))
            
        if crop_y + crop_h > frame_height:
            crop_h = frame_height - crop_y
            new_w = int(crop_h * target_aspect_ratio)
            crop_w = new_w
            crop_x = max(0, int(center_x - crop_w / 2))

        crop_w = max(1, min(crop_w, frame_width - crop_x))
        crop_h = max(1, min(crop_h, frame_height - crop_y))
        
        if crop_w <= 1 or crop_h <= 1:
             print(f"⚠️ Предупреждение: Некорректная область для лица {i+1}. Пропускаю.")
             continue
             
        crop_regions.append({
            'id': i + 1,
            'crop_x': crop_x,
            'crop_y': crop_y,
            'crop_w': crop_w,
            'crop_h': crop_h,
            'offset_x': face_offset_x,
            'offset_y': face_offset_y
        })
        
        print(f"   ✓ Финальная область: crop(x={crop_x}, y={crop_y}, w={crop_w}, h={crop_h})")
    
    if not crop_regions:
        print("❌ Ошибка: Нет валидных областей кадрирования.")
        return False, []
    
    # 6. Запуск FFmpeg для каждого региона
    output_files = []
    total_regions = len(crop_regions)
    
    print(f"\n🎥 Начинаю нарезку {total_regions} видео с помощью FFmpeg...")
    print("=" * 60)
    
    for idx, region in enumerate(crop_regions, 1):
        face_id = region['id']
        cx, cy, cw, ch = region['crop_x'], region['crop_y'], region['crop_w'], region['crop_h']
        offset_info = f" (офсет: X{region['offset_x']:+d}, Y{region['offset_y']:+d})" if region['offset_x'] != 0 or region['offset_y'] != 0 else ""
        
        output_filename = os.path.join(output_dir, f"face_{face_id}_output.mp4")
        
        print(f"\n[{idx}/{total_regions}] 🎬 Лицо {face_id}{offset_info}")
        print(f"📏 Область кропа: {cw}x{ch} в позиции ({cx}, {cy})")
        print(f"🎯 Выходной файл: {output_filename}")
        
        # Собираем команду FFmpeg
        ffmpeg_command = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'crop={cw}:{ch}:{cx}:{cy},scale={output_width}:{output_height}',
            '-c:a', 'copy',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '23',
            '-y',
            output_filename
        ]
        
        start_ffmpeg_time = time.time()
        success, error = run_ffmpeg_with_prints(ffmpeg_command, face_id, video_duration)
        end_ffmpeg_time = time.time()
        
        if success:
            output_files.append(output_filename)
            processing_time = end_ffmpeg_time - start_ffmpeg_time
            print(f"⏱️ Время обработки: {processing_time:.2f} сек")
            
            # Проверяем размер файла
            if os.path.exists(output_filename):
                file_size_mb = os.path.getsize(output_filename) / (1024 * 1024)
                print(f"💾 Размер файла: {file_size_mb:.1f} МБ")
        else:
            print(f"❌ Ошибка при обработке лица {face_id}")
            if error:
                print(f"Детали ошибки: {error}")
    
    # 7. Завершение
    end_total_time = time.time()
    print("\n" + "=" * 60)
    
    if len(output_files) == len(crop_regions):
        print("🎉 Обработка успешно завершена!")
        success = True
    elif len(output_files) > 0:
        print(f"⚠️ Частично завершено: {len(output_files)}/{len(crop_regions)} видео.")
        success = True
    else:
        print("❌ Обработка завершилась с ошибками.")
        success = False
    
    print(f"📂 Видео сохранены в: {output_dir}")
    print(f"📋 Созданные файлы:")
    for i, file in enumerate(output_files, 1):
        print(f"   {i}. {os.path.basename(file)}")
    
    total_time_minutes = (end_total_time - start_total_time) / 60
    print(f"⏱️ Общее время: {end_total_time - start_total_time:.2f} сек ({total_time_minutes:.1f} мин)")
    
    return success, output_files

# --- Пример использования ---
if __name__ == "__main__":
    # --- НАСТРОЙКИ для примера ---
    INPUT_VIDEO = 'test.mp4'
    HAAR_CASCADE_FILE = 'haarcascade_frontalface_default.xml'
    OUTPUT_FOLDER = 'split_static_ffmpeg_9_16'
    PADDING = 1.8
    ASPECT_RATIO = 9.0 / 16.0
    OUT_WIDTH = 720
    OUT_HEIGHT = 1280
    DETECT_FRAMES = 30
    
    # 🔥 НОВЫЕ НАСТРОЙКИ ОФСЕТОВ:
    # Если у тебя 2 лица и хочешь сместить:
    # - Первое лицо на 50 пикселей вправо и 20 вверх
    # - Второе лицо на 30 влево и 10 вниз
    OFFSETS_X = [50, -30]  # Положительное значение = вправо, отрицательное = влево
    OFFSETS_Y = [-20, 10]  # Положительное значение = вниз, отрицательное = вверх
    
    # Или оставь пустые списки если офсеты не нужны:
    # OFFSETS_X = []
    # OFFSETS_Y = []
    
    if not os.path.exists(INPUT_VIDEO):
        print(f"❌ Файл видео не найден: {INPUT_VIDEO}")
    elif not os.path.exists(HAAR_CASCADE_FILE):
         print(f"❌ Файл каскада не найден: {HAAR_CASCADE_FILE}")
    else:
        print("🎯 Настройки офсетов:")
        print(f"   X офсеты: {OFFSETS_X}")
        print(f"   Y офсеты: {OFFSETS_Y}")
        print()
        
        # Вызов основной функции
        success, created_files = split_video_by_face_ffmpeg(
            video_path=INPUT_VIDEO,
            haar_cascade_path=HAAR_CASCADE_FILE,
            output_dir=OUTPUT_FOLDER,
            padding_factor=PADDING,
            target_aspect_ratio=ASPECT_RATIO,
            output_width=OUT_WIDTH,
            output_height=OUT_HEIGHT,
            initial_detection_frames=DETECT_FRAMES,
            offset_x=OFFSETS_X,  # 🔥 Новые параметры
            offset_y=OFFSETS_Y   # 🔥 Новые параметры
        )
        
        if success:
            print("\n🎯 Обработка завершена успешно!")
        else:
            print("\n💥 Обработка завершена с ошибками.")