"""
Handlers for different types of messages.
"""

from aiogram import Router
from app.handlers.start import router as start_router
from app.handlers.auth import auth_router, non_auth_router, admin_router, password_router
from app.handlers.video import router as video_router

# Create the main router and include all the other routers
main_handlers_router = Router(name="main-router")

# Auth routers must be included first to ensure auth filtering happens before other handlers
main_handlers_router.include_router(password_router)  # Password handling must be first
main_handlers_router.include_router(non_auth_router)  # Non-authorized users handlers
main_handlers_router.include_router(auth_router)      # Authorized users handlers 
main_handlers_router.include_router(admin_router)     # Admin handlers

# Regular feature routers
main_handlers_router.include_router(start_router)

# Video handler for all video sources (YouTube, VK, Яндекс.Диск)
main_handlers_router.include_router(video_router) 