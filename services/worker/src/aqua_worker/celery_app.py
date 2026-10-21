"""Celery application. Queues match the work types in the implementation plan:

- default : light/system tasks
- geo     : satellite ingestion, compositing (memory-heavy, concurrency 1)
- ml      : photo evidence checks, ecosystem classifier inference (tile-wise, bounded memory)
- chain   : blockchain relayer (MUST run with concurrency 1 for nonce ordering)
- reports : PDF/CSV generation
"""

from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging, worker_ready
from kombu.entity import Queue

from aqua_core import notifications
from aqua_core.logging import configure_logging, get_logger
from aqua_core.settings import Settings, get_settings

QUEUES = ("default", "geo", "ml", "chain", "reports")

log = get_logger("aqua_worker.beat")


def beat_schedule(settings: Settings) -> dict[str, Any]:
    """Every periodic job, in one place. Run by `celery -A aqua_worker.celery_app beat`
    (exactly ONE beat process, or jobs are scheduled twice).

    - geo-nightly-ingest: new Sentinel-2 scenes + loss alerts (GEO_NIGHTLY_HOUR_UTC).
    - geo-reap-stale-runs: hourly; on `default` because the geo worker (concurrency 1) can be busy
      for hours with the very job that may have died.
    - chain-index-events: every CHAIN_INDEX_INTERVAL_S, only when the chain is configured.
    - chain-top-up-org-wallets: every CHAIN_TOP_UP_INTERVAL_S, only when the chain is configured.
    - ml-classify-dry-season: once a year (ML_ANNUAL_CLASSIFICATION_DAY of April, 02:00 UTC), after
      the dry season (Nov–Mar) has ended and the nightly ingestion has caught up: classifies that
      season for every active project that has no classification of it yet.
    """
    schedule: dict[str, Any] = {
        "geo-nightly-ingest": {
            "task": "aqua.geo.ingest_new_scenes",
            "schedule": crontab(hour=settings.geo_nightly_hour_utc, minute=0),
            "options": {"queue": "geo", "expires": 20 * 3600},  # skip if a whole day late
        },
        "geo-reap-stale-runs": {
            "task": "aqua.geo.reap_stale_runs",
            "schedule": crontab(minute=17),  # hourly, off the top of the hour
            "options": {"queue": "default", "expires": 3600},
        },
        "ml-classify-dry-season": {
            "task": "aqua.ml.classify_dry_season",
            "schedule": crontab(
                month_of_year=4,
                day_of_month=settings.ml_annual_classification_day,
                hour=2,
                minute=0,
            ),
            "options": {"queue": "ml", "expires": 20 * 3600},
        },
    }
    if settings.chain_configured:
        interval = settings.chain_index_interval_s
        schedule["chain-index-events"] = {
            "task": "aqua.chain.index_events",
            "schedule": interval,
            # A tick still waiting when the next is due is redundant (the indexer is cursor-based).
            "options": {"queue": "chain", "expires": interval},
        }
        top_up = settings.chain_top_up_interval_s
        schedule["chain-top-up-org-wallets"] = {
            "task": "aqua.chain.top_up_org_wallets",
            "schedule": top_up,
            "options": {"queue": "chain", "expires": top_up},
        }
    else:
        log.info(
            "beat.chain_index_skipped",
            reason="chain not configured (RELAYER_MNEMONIC and contract addresses)",
        )
    return schedule


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
        # Redis redelivers unacknowledged tasks after the visibility timeout (default 1 h). A
        # backfill can run longer than that; geo tasks are idempotent, but avoid needless reruns.
        broker_transport_options={"visibility_timeout": 7 * 3600},
        beat_schedule=beat_schedule(settings),
    )
    app.autodiscover_tasks(["aqua_worker"], related_name="tasks", force=True)
    # Notification emails are queued after the commit of the transaction that created them.
    notifications.set_default_email_dispatch(
        lambda name, *args, queue="default": app.send_task(name, args=list(args), queue=queue),
        enabled=settings.notification_email_enabled,
    )
    return app


def consumes_ml_queue(sender: Any) -> bool:
    """Whether a starting worker consumes the `ml` queue (unknown → True, verify anyway)."""
    try:
        names = {q.name for q in sender.app.amqp.queues.consume_from.values()}
    except Exception:
        return True
    return not names or "ml" in names


@worker_ready.connect
def _verify_classifier(sender: Any = None, **_: object) -> None:
    """Fetch the classifier into the disk cache and verify its SHA-256 when the worker starts.
    A missing or mismatching model is logged as an error (the worker still serves its other
    queues); every classification re-verifies the files and fails clearly without them."""
    if sender is None or not consumes_ml_queue(sender):
        return
    from aqua_core.storage import S3ObjectStorage
    from aqua_worker.model_store import ECOSYSTEM_CLASSIFIER_V1, fetch, load_model

    settings = get_settings()
    manifest = ECOSYSTEM_CLASSIFIER_V1
    try:
        model = load_model(fetch(S3ObjectStorage(settings), settings.ml_model_cache_dir, manifest))
    except Exception as exc:
        log.error("ml.model.unavailable", model_version=manifest.version, error=str(exc)[:500])
        return
    log.info("ml.model.verified", model_version=model.version, sha256=model.model_sha256)


@setup_logging.connect
def _configure_logging(**_: object) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)


app = create_celery()
