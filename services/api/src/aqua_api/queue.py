"""Celery client used by the API to enqueue work. The API never imports worker code; it sends
tasks by name so the two services can be deployed and scaled independently."""

from functools import lru_cache

from celery import Celery

from aqua_core.settings import get_settings


@lru_cache
def celery_client() -> Celery:
    settings = get_settings()
    return Celery("aqua-api", broker=settings.redis_url, backend=settings.redis_url)


def enqueue(task_name: str, *args: str, queue: str = "default") -> None:
    """Send a task to the workers by name (the API never imports worker code)."""
    celery_client().send_task(task_name, args=list(args), queue=queue)
