import os
import shutil
from pathlib import Path
from typing import List, Tuple

# Импортируем нашу основную функцию для работы с видео из папки core_logic
from .face_video_processor import split_video_by_face_ffmpeg, check_ffmpeg

# Путь к файлу каскада Хаара - всегда рядом с текущим файлом
DEFAULT_HAAR_CASCADE_PATH = Path(__file__).parent / 'haarcascade_frontalface_default.xml'

def extract_separate_videos_for_faces(
    input_video_path: str,
    output_directory_base: str,
    haar_cascade_path: str = str(DEFAULT_HAAR_CASCADE_PATH),
    padding_factor: float = 2.0,  # Увеличим немного отступ по умолчанию
    target_aspect_ratio: float = 9.0 / 16.0,  # Вертикальный формат (сторис)
    output_width: int = 720,
    output_height: int = 1280,
    initial_detection_frames: int = 30, # Больше кадров для поиска лиц
    overwrite_output: bool = False,
    offsets_x: List[int] = None,
    offsets_y: List[int] = None
) -> Tuple[bool, List[str]]:
    """
    Обнаруживает лица в исходном видео и создает отдельные видеофайлы
    для каждого уникального обнаруженного лица.

    Процесс:
    1. Проверяет наличие FFmpeg.
    2. Проверяет существование входного видео и файла каскада Хаара.
    3. Создает уникальную подпапку для результатов внутри output_directory_base.
    4. Вызывает функцию split_video_by_face_ffmpeg для фактической нарезки.

    Args:
        input_video_path (str): Путь к исходному видеофайлу.
        output_directory_base (str): Базовая директория, внутри которой будет создана
                                     подпапка для сохранения нарезанных видео.
                                     Имя подпапки будет основано на имени входного видео.
        haar_cascade_path (str): Путь к XML-файлу каскада Хаара.
        padding_factor (float): Коэффициент для увеличения области вокруг лица.
        target_aspect_ratio (float): Целевое соотношение сторон для выходных видео.
        output_width (int): Ширина каждого выходного видео с лицом.
        output_height (int): Высота каждого выходного видео с лицом.
        initial_detection_frames (int): Количество начальных кадров видео,
                                        используемых для обнаружения лиц.
        overwrite_output (bool): Если True, существующая папка с результатами
                                 для данного видео будет удалена и создана заново.
                                 Если False и папка существует, функция вернет ошибку.
        offsets_x (List[int]): Офсеты по X для каждого лица.
        offsets_y (List[int]): Офсеты по Y для каждого лица.

    Returns:
        Tuple[bool, List[str]]:
            - bool: True, если обработка прошла успешно и хотя бы одно видео лица создано
                    (или если лица не найдены, но ошибок не было). False в случае серьезной ошибки.
            - List[str]: Список путей к созданным видеофайлам лиц. Пустой, если лица не найдены
                         или произошла ошибка.
    """
    print(f"--- Начало извлечения видео по лицам для: {input_video_path} ---")

    # 1. Проверка наличия FFmpeg
    if not check_ffmpeg():
        print("Ошибка: FFmpeg не найден. Пожалуйста, установите FFmpeg и добавьте его в PATH.")
        return False, []

    # 2. Проверка входных файлов
    if not os.path.exists(input_video_path):
        print(f"Ошибка: Исходное видео не найдено по пути: {input_video_path}")
        return False, []
    if not os.path.exists(haar_cascade_path):
        print(f"Ошибка: Файл каскада Хаара не найден по пути: {haar_cascade_path}")
        return False, []

    # 3. Подготовка выходной директории
    video_name_stem = Path(input_video_path).stem
    specific_output_dir = Path(output_directory_base) / video_name_stem

    if specific_output_dir.exists():
        if overwrite_output:
            print(f"Предупреждение: Папка результатов {specific_output_dir} уже существует и будет перезаписана.")
            try:
                shutil.rmtree(specific_output_dir)
            except OSError as e:
                print(f"Ошибка при удалении существующей папки {specific_output_dir}: {e}")
                return False, []
        else:
            print(f"Ошибка: Папка результатов {specific_output_dir} уже существует. "
                  "Используйте `overwrite_output=True` для перезаписи.")
            # Можно также вернуть уже существующие файлы, если это нужно
            # existing_files = [str(f) for f in specific_output_dir.glob('*.mp4')]
            # return True, existing_files
            return False, [] # Пока просто ошибка

    try:
        specific_output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Видео для лиц будут сохранены в: {specific_output_dir}")
    except OSError as e:
        print(f"Ошибка при создании папки для результатов {specific_output_dir}: {e}")
        return False, []

    # 4. Вызов основной функции нарезки из face_video_processor.py
    # Функция split_video_by_face_ffmpeg возвращает (bool_success, list_of_paths)
    success_status, created_video_paths = split_video_by_face_ffmpeg(
        video_path=input_video_path,
        haar_cascade_path=haar_cascade_path,
        output_dir=str(specific_output_dir), # Передаем как строку
        padding_factor=padding_factor,
        target_aspect_ratio=target_aspect_ratio,
        output_width=output_width,
        output_height=output_height,
        initial_detection_frames=initial_detection_frames,
        offset_x=offsets_x,
        offset_y=offsets_y
    )

    if success_status:
        if created_video_paths:
            print(f"Успешно создано {len(created_video_paths)} видео с лицами.")
        else:
            print("Обработка завершена, но лица для нарезки не были найдены в видео.")
        # Считаем успехом, даже если лица не найдены, но ошибок не было
        return True, created_video_paths
    else:
        print("Произошла ошибка во время нарезки видео по лицам.")
        return False, []


def main():
    """Точка входа для консольной команды extract-faces"""
    print("--- Демонстрация функции extract_separate_videos_for_faces ---")

    # --- НАСТРОЙКИ ДЛЯ ПРИМЕРА ---
    # !!! ВАЖНО: Укажите правильный путь к вашему видеофайлу !!!
    # test_input_video = 'путь/к/вашему/видео.mp4'
    test_input_video = 'test.mp4' # Замените на реальный путь или положите видео с таким именем рядом

    # Убедитесь, что файл haarcascade_frontalface_default.xml находится там,
    # где указано в DEFAULT_HAAR_CASCADE_PATH, или измените путь
    test_haar_cascade = str(DEFAULT_HAAR_CASCADE_PATH)

    # Папка, куда будут сохраняться все результаты (внутри нее создадутся подпапки)
    base_output_folder = "processed_face_videos"

    # Создадим базовую папку для результатов, если ее нет
    Path(base_output_folder).mkdir(parents=True, exist_ok=True)
 
    if os.path.exists(test_input_video) and os.path.exists(test_haar_cascade):
        print(f"\nЗапускаем обработку видео: {test_input_video}")
        print(f"Файл каскада: {test_haar_cascade}")
        print(f"Результаты будут в подпапке внутри: {base_output_folder}")

        # Вызов нашей основной функции
        overall_success, list_of_generated_videos = extract_separate_videos_for_faces(
            input_video_path=test_input_video,
            output_directory_base=base_output_folder,
            haar_cascade_path=test_haar_cascade,
            # Можно переопределить другие параметры здесь, если нужно:
            padding_factor=2.5,
            # target_aspect_ratio=1.0, # квадрат
            output_width=1080,
            output_height=1080,
            offsets_x=[50, -30],
            offsets_y=[-20, 10],
            overwrite_output=True # Разрешаем перезапись для удобства тестов
        )

        if overall_success:
            if list_of_generated_videos:
                print("\n--- Обработка успешно завершена! ---")
                print("Созданы следующие видео с лицами:")
                for video_file in list_of_generated_videos:
                    print(f"  -> {video_file}")
            else:
                print("\n--- Обработка завершена, но лица не были обнаружены в видео. ---")
        else:
            print("\n--- Обработка завершилась с ошибкой. ---")
    else:
        print("\nНе удалось запустить пример из-за отсутствия необходимых файлов (видео или каскад Хаара).")

    print("\n--- Демонстрация завершена ---")


# --- Пример использования ---
if __name__ == "__main__":
    main()