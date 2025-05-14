import logging
import sys
from app.core.config import settings

def setup_logging():
    """
    Configure logging for the application.
    This should be called before any other imports.
    """
    logging_level = getattr(logging, settings.logging_level.upper(), logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=logging_level,
        format=settings.logging_format,
        datefmt=settings.logging_datefmt,
        stream=sys.stdout
    )
    
    # Set lower log levels for some chatty libraries
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
    
    # Our app's logger can stay at the configured level
    logger = logging.getLogger("app")
    logger.setLevel(logging_level)
    
    return logger 