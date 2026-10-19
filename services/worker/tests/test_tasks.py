from aqua_worker.celery_app import QUEUES, app
from aqua_worker.tasks.system import ping


def test_ping_task_registered_under_stable_name() -> None:
    assert "aqua.system.ping" in app.tasks


def test_ping_returns_pong() -> None:
    result = ping.apply().get()
    assert result["pong"] is True
    assert result["worker"]


def test_queue_routing_by_task_name_prefix() -> None:
    routes = app.conf.task_routes
    assert routes["aqua.chain.*"] == {"queue": "chain"}
    assert routes["aqua.geo.*"] == {"queue": "geo"}
    assert set(QUEUES) == {"default", "geo", "ml", "chain", "reports"}
