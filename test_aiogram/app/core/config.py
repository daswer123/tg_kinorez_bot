import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, HttpUrl, Field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """Main configuration class that reads variables from .env file."""
    # .env file configuration
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # --- Telegram Bot API Server ---
    telegram_api_id: int = Field(alias='TELEGRAM_API_ID')
    telegram_api_hash: str = Field(alias='TELEGRAM_API_HASH')
    telegram_local: bool = Field(default=True, alias='TELEGRAM_LOCAL')

    # --- Aiogram Bot ---
    telegram_token: SecretStr = Field(alias='TELEGRAM_TOKEN')

    # --- OpenRouter API ---
    openrouter_api_key: SecretStr = Field(alias='OPENROUTER_API_KEY')
    openrouter_base_url: str = Field(default='https://openrouter.ai/api/v1', alias='OPENROUTER_BASE_URL')

    # --- Proxy settings ---
    proxy_url: str = Field(alias='PROXY_URL')

    # --- Connection Settings ---
    telegram_local_server_url: str = Field(default='http://api', alias='TELEGRAM_LOCAL_SERVER_URL')
    telegram_webhook_url: str = Field(default='http://bot', alias='TELEGRAM_WEBHOOK_URL')
    telegram_webhook_path: str = Field(default='/webhook', alias='TELEGRAM_WEBHOOK_PATH')

    # --- Authentication ---
    auth_enabled: bool = Field(default=True, alias='AUTH_ENABLED')
    auth_password: SecretStr = Field(alias='AUTH_PASSWORD')

    # --- PostgreSQL Database ---
    postgres_user: str = Field(alias='POSTGRES_USER', default='postgres')
    postgres_password: SecretStr = Field(alias='POSTGRES_PASSWORD')
    postgres_db: str = Field(alias='POSTGRES_DB', default='youtubebot')
    postgres_host: str = Field(alias='POSTGRES_HOST', default='postgres')
    postgres_port: int = Field(alias='POSTGRES_PORT', default=5432)

    # --- Redis Settings ---
    redis_host: str = Field(alias='REDIS_HOST', default='redis')
    redis_port: int = Field(alias='REDIS_PORT', default=6379)
    redis_db: int = Field(alias='REDIS_DB', default=0)
    redis_password: Optional[SecretStr] = Field(alias='REDIS_PASSWORD', default=None)

    # --- Bot Web Server (aiohttp) ---
    webapp_host: str = Field(default='0.0.0.0', alias='WEBAPP_HOST')
    webapp_port: int = Field(default=80, alias='WEBAPP_PORT')

    # --- Logging ---
    logging_level: str = Field(default='INFO', alias='LOGGING_LEVEL')
    logging_format: str = Field(default="[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s", alias='LOGGING_FORMAT')
    logging_datefmt: str = Field(default="%Y-%m-%d %H:%M:%S", alias='LOGGING_DATEFMT')

    # --- Computed Properties ---
    @property
    def telegram_webhook_full_url(self) -> str:
        """Returns the full URL for setting up the webhook."""
        return f"{self.telegram_webhook_url.rstrip('/')}{self.telegram_webhook_path}"

    @property
    def telegram_api_server(self):
        """Creates API server object for Aiogram."""
        from aiogram.client.telegram import TelegramAPIServer
        return TelegramAPIServer.from_base(self.telegram_local_server_url, is_local=self.telegram_local)
    
    @property
    def postgres_dsn(self) -> str:
        """Returns the DSN for PostgreSQL connection."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password.get_secret_value()}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

# Create a single instance of the config
try:
    settings = Settings()
except Exception as e:
    logging.basicConfig(level="INFO")
    logging.critical(f"CRITICAL ERROR: Could not load settings! Error: {e}", exc_info=True)
    logging.critical("Please check your .env file and environment variables.")
    exit(1)

# Check for required fields
if not settings.telegram_token.get_secret_value() or \
   not settings.telegram_api_id or \
   not settings.telegram_api_hash or \
   not settings.postgres_password.get_secret_value() or \
   (settings.auth_enabled and not settings.auth_password.get_secret_value()):
    logging.basicConfig(level="INFO")
    logging.critical("CRITICAL ERROR: TELEGRAM_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, POSTGRES_PASSWORD, and AUTH_PASSWORD (if AUTH_ENABLED=true) must be set in .env!")
    exit(1) 