import instructor
from pydantic import BaseModel
from openai import OpenAI
from typing import List

from test_code.funcs import get_video_by_url_and_timings

# Define your desired output structure
class YoutubeVideo(BaseModel):
    url: str
    start_time: str
    end_time: str
    correct_timings: bool

API_KEY = "-"
BASE_URL = "https://openrouter.ai/api/v1"

# Patch the OpenAI client
client = instructor.from_openai(OpenAI(base_url=BASE_URL,api_key=API_KEY),mode=instructor.Mode.JSON)

# Пример обработки ссылок на YouTube видео с временными метками
example_input = """
https://www.youtube.com/watch?v=Rjl07pIFiz4
00:10 - 00:4

https://www.youtube.com/watch?v=Rjl07pIFiz4
С 10 секунды до минуты

https://www.youtube.com/watch?v=Rjl07pIFiz4
Вырежи с начала и до 40 секунды
"""

# Извлечение структурированных данных из текста с YouTube ссылками
response = client.chat.completions.create(
    model="google/gemini-2.0-flash-001",
    response_model=List[YoutubeVideo],
    messages=[
        {"role": "user", "content": f"Извлеки данные из текста: {example_input} необходимо получить чистую ссылку, время начало и время конца. Просто извлеки данные, не добавляй ничего лишнего. Время извлекай в формате 00:00:00 без милисекунд. Если тайминги не корректные или не подходят, верни correct_timings=False"}
    ],
)

# Вывод результатов
for i, video in enumerate(response):
    i += 1  # Начинаем с 1, потому что enumerate начинается с 0
    print(f"Видео {i}:")
    print(f"URL: {video.url}")
    print(f"Начало: {video.start_time}")
    print(f"Конец: {video.end_time}")
    print(f"Корректные тайминги: {video.correct_timings}")
    

    # Скачиваем видео в указанном временном диапазоне в папку videos
    try:
        if video.correct_timings:
            get_video_by_url_and_timings(video.url, video.start_time, video.end_time, "videos")
            print(f"Видео {i} успешно скачано")
        else:
            print(f"Видео {i} не скачано, тайминги не корректные")
    except Exception as e:
        print(f"Ошибка при скачивании видео {i}: {str(e)}")

    print("\n")

# Пример вывода:
# Видео 1:
# URL: https://youtu.be/xxxxx
# Начало: 6:10
# Конец: 6:42
#
# Видео 2:
# URL: https://youtu.be/zzzzzzz
# Начало: 8:10
# Конец: 9:15
#
# Видео 3:
# URL: https://youtu.be/tyyyyyy
# Начало: 65:12
# Конец: 69:1
