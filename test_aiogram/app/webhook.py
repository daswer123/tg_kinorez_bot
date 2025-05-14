import logging
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.core.config import settings
from app.bot import bot, dp
from app.workers import VideoWorker

logger = logging.getLogger(__name__)

# Worker tasks storage
worker_tasks = []
# Worker instances storage for proper shutdown
workers = []

async def on_startup(dispatcher: Dispatcher, bot: Bot):
    """
    Setup webhook when the bot starts.
    """
    logger.warning("Bot starting...")
    logger.info(f"Bot uses API Server: {bot.session.api.base}")
    logger.info(f"Attempting to set webhook to: {settings.telegram_webhook_full_url}")

    try:
        
        # First delete the old webhook if it exists
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Previous webhook deleted successfully.")

        # Set the new webhook
        webhook_set = await bot.set_webhook(
            url=settings.telegram_webhook_full_url,
            allowed_updates=dispatcher.resolve_used_update_types(),  # Only the updates we need
            drop_pending_updates=True  # Skip old updates
        )

        if webhook_set:
            bot_info = await bot.get_me()
            logger.warning(f"Webhook set successfully for bot @{bot_info.username} (ID: {bot_info.id})")
            logger.info(f"Listening for webhooks at: {settings.telegram_webhook_full_url}")
        else:
            logger.error("Failed to set webhook! Check API server and network.")
        
        # Start the Video workers (supports YouTube, VK, Яндекс.Диск)
        num_workers = 2
        await start_video_workers(num_workers=num_workers)
        logger.info(f"Started {num_workers} video worker instances")

    except Exception as e:
        # Catch and log any errors during webhook setup
        logger.critical(f"CRITICAL ERROR during webhook setup: {e}", exc_info=True)
        raise

async def start_video_workers(num_workers=2):
    """Start multiple video workers."""
    global worker_tasks, workers
    
    for i in range(num_workers):
        worker = VideoWorker(bot)
        workers.append(worker)
        task = asyncio.create_task(worker.start())
        worker_tasks.append(task)
        logger.info(f"Started video worker #{i+1}")

async def on_shutdown(dispatcher: Dispatcher, bot: Bot):
    """
    Graceful shutdown: stop workers and close connections.
    """
    logger.warning("Shutting down webhook and workers...")
    
    # Stop all workers
    for worker in workers:
        await worker.stop()
    
    # Cancel all worker tasks
    for task in worker_tasks:
        task.cancel()
    
    # Wait for worker tasks to complete if not already done
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        logger.info("All worker tasks stopped")

def run_webhook():
    """
    Run the webhook server.
    """
    # Register startup handler
    dp.startup.register(on_startup)
    
    # Register shutdown handler
    dp.shutdown.register(on_shutdown)
    
    # Create web application
    app = web.Application()
    
    # Create a request handler
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=None  # Secret token if used
    )
    
    # Register webhook handler
    webhook_handler.register(app, path=settings.telegram_webhook_path)
    
    # Setup application
    setup_application(app, dp, bot=bot)
    
    # Start web server
    web.run_app(
        app,
        host=settings.webapp_host,
        port=settings.webapp_port
    ) 