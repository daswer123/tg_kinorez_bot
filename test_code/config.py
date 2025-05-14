import yt_dlp

proxy_url = 'http://root:12qwer34TY!@213.232.204.202:3128'

# Настройка yt_dlp с прокси
ydl_opts = {
    # 'quiet': True,
    # 'no_warnings': True,
    'proxy': proxy_url,  # тут прокси
}

ydl = yt_dlp.YoutubeDL(ydl_opts)

# Параметры авторизации
AUTH_PASSWORD = "5amlItmYu2PUFPrxq9MKUwWrsVZaqW"  # Пароль для авторизации пользователей
AUTH_ENABLED = True
ADMIN_ID = 1741018404  # ID администратора

# Включить/выключить авторизацию