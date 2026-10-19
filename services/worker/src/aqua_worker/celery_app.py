"""Celery application. Queues match the work types in the implementation plan:

- default : light/system tasks
- geo     : satellite ingestion, compositing, model inference on rasters (memory-heavy, concurrency 1)
- ml      : photo evidence checks
- chain   : blockchain relayer (MUST run with concurrency 1 for nonce ordering)
- reports : PDF/CSV generation
"""

from celery import Celery
from celery.signals import setup_logging
from kombu.entity import Queue

from aqua_core.logging import configure_logging
from aqua_core.settings import get_settings

QUEUES = ("default", "geo", "ml", "chain", "reports")


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("aquacreds", broker=settings.redis_url, backend=settings.redis_url)
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue="default",
        task_routes={f"aqua.{name}.*": {"queue": name} for name in QUEUES if name != "default"},
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=3600,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        enable_utc=True,
    )
    app.autodiscover_tasks(["aqua_worker"], related_name="tasks", force=True)
    return app


@setup_logging.connect
def _configure_logging(**_: object) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)


app = create_celery()
