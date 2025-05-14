"""Workers for processing tasks from the queue."""

from app.workers.video_worker import VideoWorker

__all__ = [
    "VideoWorker",
] 