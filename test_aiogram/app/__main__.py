import logging
# First set up logging
from app.core.log import setup_logging
setup_logging()

# Then import the rest of the components
from app.webhook import run_webhook

# Get logger for this module *after* setup
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Мультиплатформенный бот KinoRez для нарезки видео запускается...")
    try:
        run_webhook()  # Run the webhook web server
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Application stopped by user (KeyboardInterrupt/SystemExit).")
    except Exception as e:
        # Catch all unhandled exceptions at the top level
        logger.critical(f"Unhandled critical exception during runtime: {e}", exc_info=True)
    finally:
        logger.info("Application finished.") 