import sqlite3
import os
import logging
from datetime import datetime

# Настройка логирования
logger = logging.getLogger(__name__)

class AuthDB:
    """Класс для работы с базой данных авторизованных пользователей"""
    
    def __init__(self, db_path="users.db"):
        """
        Инициализация базы данных пользователей
        
        Args:
            db_path (str): Путь к файлу базы данных SQLite
        """
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Инициализация базы данных и создание таблицы пользователей, если она не существует"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Создаем таблицу пользователей, если она не существует
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_authorized BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем таблицу запросов, если она не существует
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    request_text TEXT,
                    video_url TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"База данных пользователей инициализирована: {self.db_path}")
        except Exception as e:
            logger.error(f"Ошибка при инициализации базы данных: {str(e)}")
            raise
    
    def is_user_authorized(self, user_id):
        """
        Проверяет, авторизован ли пользователь
        
        Args:
            user_id (int): ID пользователя в Telegram
            
        Returns:
            bool: True, если пользователь авторизован, иначе False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT is_authorized FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            conn.close()
            
            if result is None:
                return False
            
            return bool(result[0])
        except Exception as e:
            logger.error(f"Ошибка при проверке авторизации пользователя {user_id}: {str(e)}")
            return False
    
    def add_or_update_user(self, user_id, username=None, first_name=None, last_name=None, is_authorized=False):
        """
        Добавляет нового пользователя или обновляет информацию о существующем
        
        Args:
            user_id (int): ID пользователя в Telegram
            username (str, optional): Username пользователя
            first_name (str, optional): Имя пользователя
            last_name (str, optional): Фамилия пользователя
            is_authorized (bool, optional): Статус авторизации пользователя
            
        Returns:
            bool: True, если операция успешна, иначе False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Проверяем, существует ли пользователь
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            user_exists = cursor.fetchone() is not None
            
            if user_exists:
                # Обновляем информацию о пользователе
                cursor.execute("""
                    UPDATE users 
                    SET username = ?, first_name = ?, last_name = ?, is_authorized = ?
                    WHERE user_id = ?
                """, (username, first_name, last_name, is_authorized, user_id))
            else:
                # Добавляем нового пользователя
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, is_authorized)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, first_name, last_name, is_authorized))
            
            conn.commit()
            conn.close()
            
            if user_exists:
                logger.info(f"Обновлена информация о пользователе {user_id}")
            else:
                logger.info(f"Добавлен новый пользователь {user_id}")
                
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении/обновлении пользователя {user_id}: {str(e)}")
            return False
    
    def authorize_user(self, user_id):
        """
        Авторизовать пользователя
        
        Args:
            user_id (int): ID пользователя в Telegram
            
        Returns:
            bool: True, если операция успешна, иначе False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("UPDATE users SET is_authorized = 1 WHERE user_id = ?", (user_id,))
            
            if cursor.rowcount == 0:
                # Если пользователя нет в базе, добавляем его
                cursor.execute("""
                    INSERT INTO users (user_id, is_authorized)
                    VALUES (?, 1)
                """, (user_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Пользователь {user_id} авторизован")
            return True
        except Exception as e:
            logger.error(f"Ошибка при авторизации пользователя {user_id}: {str(e)}")
            return False
    
    def get_all_users(self):
        """
        Получить список всех пользователей
        
        Returns:
            list: Список кортежей с информацией о пользователях
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT user_id, username, first_name, last_name, is_authorized, created_at FROM users")
            users = cursor.fetchall()
            
            conn.close()
            
            return users
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей: {str(e)}")
            return []
            
    def log_request(self, user_id, request_text, video_url=None, start_time=None, end_time=None, status="processing"):
        """
        Логирует запрос пользователя
        
        Args:
            user_id (int): ID пользователя в Telegram
            request_text (str): Текст запроса
            video_url (str, optional): URL видео
            start_time (str, optional): Время начала фрагмента
            end_time (str, optional): Время конца фрагмента
            status (str, optional): Статус запроса (processing, completed, error)
            
        Returns:
            int: ID записи запроса или None в случае ошибки
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO requests (user_id, request_text, video_url, start_time, end_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, request_text, video_url, start_time, end_time, status))
            
            request_id = cursor.lastrowid
            
            conn.commit()
            conn.close()
            
            logger.info(f"Запрос пользователя {user_id} зарегистрирован (ID: {request_id})")
            return request_id
        except Exception as e:
            logger.error(f"Ошибка при логировании запроса пользователя {user_id}: {str(e)}")
            return None
            
    def update_request_status(self, request_id, status, video_url=None, start_time=None, end_time=None):
        """
        Обновляет статус запроса
        
        Args:
            request_id (int): ID запроса
            status (str): Новый статус (processing, completed, error)
            video_url (str, optional): URL видео
            start_time (str, optional): Время начала фрагмента
            end_time (str, optional): Время конца фрагмента
            
        Returns:
            bool: True, если операция успешна, иначе False
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            update_fields = ["status = ?"]
            update_values = [status]
            
            if video_url is not None:
                update_fields.append("video_url = ?")
                update_values.append(video_url)
                
            if start_time is not None:
                update_fields.append("start_time = ?")
                update_values.append(start_time)
                
            if end_time is not None:
                update_fields.append("end_time = ?")
                update_values.append(end_time)
                
            update_values.append(request_id)
            
            query = f"UPDATE requests SET {', '.join(update_fields)} WHERE id = ?"
            
            cursor.execute(query, update_values)
            
            conn.commit()
            conn.close()
            
            logger.info(f"Статус запроса {request_id} обновлен на '{status}'")
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса запроса {request_id}: {str(e)}")
            return False
            
    def get_user_statistics(self, user_id=None, limit=20):
        """
        Получает статистику запросов пользователя
        
        Args:
            user_id (int, optional): ID пользователя в Telegram (None для всех пользователей)
            limit (int, optional): Ограничение количества результатов
            
        Returns:
            list: Список запросов с дополнительной информацией о пользователе
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Чтобы получить словари вместо кортежей
            cursor = conn.cursor()
            
            # Получаем все запросы
            query = "SELECT id, user_id, request_text, created_at FROM requests"
            params = []
            
            if user_id is not None:
                query += " WHERE user_id = ?"
                params.append(user_id)
                
                
            query += " ORDER BY created_at DESC"
            cursor.execute(query, params)
            all_requests = cursor.fetchall()
            
            # Группируем запросы вручную по user_id, request_text и минуте создания
            grouped_requests = {}
            for req in all_requests:
                key = (
                    req['user_id'], 
                    req['request_text'], 
                    req['created_at'].split(':')[0] + ':' + req['created_at'].split(':')[1]  # YYYY-MM-DD HH:MM
                )
                if key not in grouped_requests:
                    grouped_requests[key] = req['id']
            
            # Получаем информацию о уникальных запросах
            requests = []
            if grouped_requests:
                # Формируем список ID уникальных запросов
                request_ids = list(grouped_requests.values())
                # Ограничиваем до указанного лимита
                request_ids = request_ids[:limit]
                
                # Создаем строку с плейсхолдерами для SQL запроса
                placeholders = ', '.join(['?'] * len(request_ids))
                
                # Получаем полную информацию о запросах
                cursor.execute(f"""
                    SELECT 
                        r.id, r.user_id, r.request_text, r.video_url, r.start_time, r.end_time, 
                        r.status, r.created_at, u.username, u.first_name, u.last_name
                    FROM requests r
                    JOIN users u ON r.user_id = u.user_id
                    WHERE r.id IN ({placeholders})
                    ORDER BY r.created_at DESC
                """, request_ids)
                
                requests = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            
            return requests
        except Exception as e:
            logger.error(f"Ошибка при получении статистики запросов: {str(e)}")
            return []
            
    def get_total_statistics(self):
        """
        Получает общую статистику использования бота
        
        Returns:
            dict: Статистика использования (количество пользователей, запросов, успешных запросов и т.д.)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Чтобы получить словари вместо кортежей
            cursor = conn.cursor()
            
            # Количество пользователей
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            # Количество авторизованных пользователей
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_authorized = 1")
            authorized_users = cursor.fetchone()[0]
            
            # Общее количество запросов
            cursor.execute("""
                SELECT COUNT(*) FROM requests
                WHERE video_url IS NOT NULL AND video_url != ''
            """)
            total_requests = cursor.fetchone()[0]
            
            # Количество успешных запросов
            cursor.execute("""
                SELECT COUNT(*) FROM requests 
                WHERE status = 'completed'
                AND video_url IS NOT NULL AND video_url != ''
            """)
            completed_requests = cursor.fetchone()[0]
            
            # Количество запросов с ошибками
            cursor.execute("""
                SELECT COUNT(*) FROM requests 
                WHERE status = 'error'
                AND video_url IS NOT NULL AND video_url != ''
            """)
            error_requests = cursor.fetchone()[0]
            
            # Получаем все запросы за последние 7 дней
            cursor.execute("""
                SELECT id, user_id, request_text, created_at 
                FROM requests 
                WHERE created_at >= datetime('now', '-7 days')
                ORDER BY created_at DESC
            """)
            all_requests = cursor.fetchall()
            
            # Группируем запросы вручную по user_id, request_text и минуте создания
            grouped_requests = {}
            for req in all_requests:
                key = (
                    req['user_id'], 
                    req['request_text'], 
                    req['created_at'].split(':')[0] + ':' + req['created_at'].split(':')[1]  # YYYY-MM-DD HH:MM
                )
                if key not in grouped_requests:
                    grouped_requests[key] = req['id']
            
            # Получаем информацию о уникальных запросах
            recent_requests = []
            if grouped_requests:
                # Формируем список ID уникальных запросов
                request_ids = list(grouped_requests.values())
                # Ограничиваем до 30 последних запросов
                request_ids = request_ids[:30]
                
                # Создаем строку с плейсхолдерами для SQL запроса
                placeholders = ', '.join(['?'] * len(request_ids))
                
                # Получаем полную информацию о запросах
                cursor.execute(f"""
                    SELECT 
                        r.id, r.user_id, r.request_text, r.status, r.created_at,
                        u.username, u.first_name, u.last_name
                    FROM requests r
                    JOIN users u ON r.user_id = u.user_id
                    WHERE r.id IN ({placeholders})
                    ORDER BY r.created_at DESC
                """, request_ids)
                
                recent_requests = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            
            return {
                "total_users": total_users,
                "authorized_users": authorized_users,
                "total_requests": total_requests,
                "completed_requests": completed_requests,
                "error_requests": error_requests,
                "recent_requests": recent_requests
            }
        except Exception as e:
            logger.error(f"Ошибка при получении общей статистики: {str(e)}")
            return {
                "total_users": 0,
                "authorized_users": 0,
                "total_requests": 0,
                "completed_requests": 0,
                "error_requests": 0,
                "recent_requests": []
            }
            
    def get_request_videos(self, request_id):
        """
        Получает все видео и таймкоды из одного запроса
        
        Args:
            request_id (int): ID запроса
            
        Returns:
            list: Список словарей с информацией о видео в запросе
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Получаем основную информацию о запросе
            cursor.execute("""
                SELECT 
                    id, user_id, request_text, status, created_at
                FROM requests
                WHERE id = ?
            """, (request_id,))
            
            request_info = cursor.fetchone()
            
            if not request_info:
                conn.close()
                return []
            
            # Сначала проверяем, есть ли у запроса URL видео
            # Если есть, это может быть видео-запись или родительский запрос
            cursor.execute("""
                SELECT 
                    id, video_url, start_time, end_time, status
                FROM requests 
                WHERE id = ? AND video_url IS NOT NULL
            """, (request_id,))
            
            main_request_video = cursor.fetchone()
            videos = []
            
            if main_request_video:
                # Это видео-запись, возвращаем только её
                videos.append(dict(main_request_video))
            else:
                # Это родительский запрос, ищем все связанные видео-записи
                cursor.execute("""
                    SELECT 
                        id, video_url, start_time, end_time, status
                    FROM requests
                    WHERE 
                        user_id = ? AND 
                        request_text = ? AND 
                        created_at = ? AND 
                        video_url IS NOT NULL AND
                        id != ?
                    ORDER BY id
                """, (
                    request_info['user_id'], 
                    request_info['request_text'], 
                    request_info['created_at'],
                    request_id
                ))
                
                videos = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            
            return videos
        except Exception as e:
            logger.error(f"Ошибка при получении видео из запроса {request_id}: {str(e)}")
            return []
            
    def log_video_request(self, request_id, video_url, start_time, end_time, status="processing"):
        """
        Добавляет новую запись о видео в рамках запроса
        
        Args:
            request_id (int): ID основного запроса
            video_url (str): URL видео
            start_time (str): Время начала фрагмента
            end_time (str): Время конца фрагмента
            status (str, optional): Статус обработки видео
            
        Returns:
            int: ID новой записи о видео или None в случае ошибки
        """
        try:
            # Получаем информацию об основном запросе
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT user_id, request_text, created_at
                FROM requests
                WHERE id = ?
            """, (request_id,))
            
            parent_request = cursor.fetchone()
            
            if not parent_request:
                conn.close()
                logger.error(f"Не найден родительский запрос с ID {request_id}")
                return None
            
            # Создаем новую запись для видео с теми же данными пользователя и текстом запроса
            cursor.execute("""
                INSERT INTO requests (user_id, request_text, video_url, start_time, end_time, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                parent_request['user_id'],
                parent_request['request_text'],
                video_url,
                start_time,
                end_time,
                status,
                parent_request['created_at']
            ))
            
            video_request_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            logger.info(f"Добавлена запись о видео {video_url} для запроса {request_id} (новый ID: {video_request_id})")
            return video_request_id
        except Exception as e:
            logger.error(f"Ошибка при добавлении записи о видео для запроса {request_id}: {str(e)}")
            return None 