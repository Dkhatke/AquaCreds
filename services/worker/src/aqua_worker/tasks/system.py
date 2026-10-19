"""System tasks."""

import socket
from datetime import UTC, datetime
from typing import Any

from celery import shared_task

from aqua_worker import __version__


@shared_task(name="aqua.system.ping")
def ping() -> dict[str, Any]:
    return {
        "pong": True,
        "worker": socket.gethostname(),
        "version": __version__,
        "at": datetime.now(UTC).isoformat(),
    }
