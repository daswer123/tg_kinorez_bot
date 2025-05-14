"""Redis task queue manager."""

import logging
import json
import time
import uuid
from typing import Dict, Any, Optional, List, Tuple

from app.infrastructure.redis.connection import get_redis_connection

# Module logger
logger = logging.getLogger(__name__)

# Queue names
TASK_QUEUE = "task_queue"
RESULTS_QUEUE = "results_queue"
TASK_STATE_PREFIX = "task_state:"
PUBSUB_CHANNEL = "task_results"

class TaskManager:
    """
    Manager for Redis task queues and task state tracking.
    """
    
    @staticmethod
    async def generate_task_id() -> str:
        """
        Generate a unique task ID.
        
        Returns:
            str: Unique task ID
        """
        return str(uuid.uuid4())
    
    @staticmethod
    async def add_task(
        user_id: int, 
        task_type: str, 
        task_data: Dict[str, Any],
        priority: int = 0
    ) -> str:
        """
        Add a task to the queue.
        
        Args:
            user_id (int): Telegram user ID
            task_type (str): Type of task (e.g., 'youtube_fragment')
            task_data (Dict[str, Any]): Task parameters
            priority (int): Task priority (higher values = higher priority)
            
        Returns:
            str: Task ID
        """
        redis_client = await get_redis_connection()
        
        # Generate task ID
        task_id = await TaskManager.generate_task_id()
        
        # Create task data structure
        task = {
            "task_id": task_id,
            "user_id": user_id,
            "task_type": task_type,
            "task_data": task_data,
            "created_at": time.time()
        }
        
        # Convert to JSON
        task_json = json.dumps(task)
        
        # Add task to queue
        if priority > 0:
            # Use sorted set for priority queue
            await redis_client.zadd("priority_task_queue", {task_json: priority})
            logger.info(f"Added task {task_id} to priority queue with priority {priority}")
        else:
            # Use regular list for normal queue
            await redis_client.lpush(TASK_QUEUE, task_json)
            logger.info(f"Added task {task_id} to queue")
        
        # Set initial task state
        await TaskManager.update_task_state(
            task_id=task_id,
            status="queued",
            user_id=user_id,
            task_type=task_type,
            queued_at=time.time()
        )
        
        return task_id
    
    @staticmethod
    async def get_task(
        timeout: int = 0,
        priority_first: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Get a task from the queue.
        
        Args:
            timeout (int): Timeout in seconds (0 = no timeout)
            priority_first (bool): Check priority queue first
            
        Returns:
            Optional[Dict[str, Any]]: Task data or None if no tasks
        """
        redis_client = await get_redis_connection()
        
        task_json = None
        
        if priority_first:
            # Try to get from priority queue first
            highest_priority = await redis_client.zrevrange("priority_task_queue", 0, 0, withscores=True)
            if highest_priority:
                task_json_bytes, _ = highest_priority[0]
                # Remove from priority queue
                await redis_client.zrem("priority_task_queue", task_json_bytes)
                task_json = task_json_bytes
        
        if task_json is None:
            # Get from regular queue if nothing in priority queue or priority not checked
            if timeout > 0:
                queue_result = await redis_client.brpop(TASK_QUEUE, timeout=timeout)
                if queue_result:
                    _, task_json = queue_result
            else:
                task_json = await redis_client.rpop(TASK_QUEUE)
        
        if task_json:
            try:
                task = json.loads(task_json)
                logger.info(f"Got task {task.get('task_id')} from queue")
                return task
            except json.JSONDecodeError:
                logger.error(f"Error decoding task JSON: {task_json}")
                return None
        
        return None
    
    @staticmethod
    async def update_task_state(
        task_id: str,
        status: str,
        **fields
    ) -> None:
        """
        Update task state in Redis.
        
        Args:
            task_id (str): Task ID
            status (str): Task status (queued, running, completed, failed)
            **fields: Additional fields to store
        """
        redis_client = await get_redis_connection()
        
        # Add status and timestamp to fields
        fields["status"] = status
        fields[f"{status}_at"] = time.time()
        
        # Convert any non-string values to strings
        string_fields = {}
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                string_fields[key] = json.dumps(value)
            else:
                string_fields[key] = str(value)
        
        # Update hash in Redis
        await redis_client.hset(f"{TASK_STATE_PREFIX}{task_id}", mapping=string_fields)
        
        # If task completed or failed, add to results queue and publish notification
        if status in ["completed", "failed"]:
            # Add to results queue
            await redis_client.lpush(RESULTS_QUEUE, task_id)
            
            # Publish notification
            await redis_client.publish(PUBSUB_CHANNEL, task_id)
            
            logger.info(f"Task {task_id} {status}, notification sent")
    
    @staticmethod
    async def get_task_state(task_id: str) -> Dict[str, Any]:
        """
        Get task state from Redis.
        
        Args:
            task_id (str): Task ID
            
        Returns:
            Dict[str, Any]: Task state
        """
        redis_client = await get_redis_connection()
        
        # Get all fields from hash
        state = await redis_client.hgetall(f"{TASK_STATE_PREFIX}{task_id}")
        
        # Parse JSON fields
        for key, value in state.items():
            if key in ["task_data", "result"]:
                try:
                    state[key] = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Convert timestamp fields to float
            if key.endswith("_at"):
                try:
                    state[key] = float(value)
                except (ValueError, TypeError):
                    pass
        
        return state
    
    @staticmethod
    async def get_result(timeout: int = 0) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Get a completed task result.
        
        Args:
            timeout (int): Timeout in seconds (0 = no timeout)
            
        Returns:
            Optional[Tuple[str, Dict[str, Any]]]: (task_id, task_state) or None
        """
        redis_client = await get_redis_connection()
        
        # Get task ID from results queue
        task_id = None
        if timeout > 0:
            result = await redis_client.brpop(RESULTS_QUEUE, timeout=timeout)
            if result:
                _, task_id = result
        else:
            task_id = await redis_client.rpop(RESULTS_QUEUE)
        
        if task_id:
            # Get task state
            state = await TaskManager.get_task_state(task_id)
            return task_id, state
        
        return None
    
    @staticmethod
    async def subscribe_to_results():
        """
        Subscribe to task result notifications.
        
        Returns:
            redis.client.PubSub: PubSub object
        """
        redis_client = await get_redis_connection()
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(PUBSUB_CHANNEL)
        return pubsub 