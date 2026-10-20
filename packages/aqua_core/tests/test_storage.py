from urllib.parse import parse_qs, urlparse

from aqua_core.settings import Settings
from aqua_core.storage import S3ObjectStorage


def test_presign_get_uses_public_endpoint_and_download_name() -> None:
    storage = S3ObjectStorage(
        Settings(
            _env_file=None,
            s3_endpoint_url="http://s3:8333",
            s3_public_endpoint_url="http://localhost:8333",
            s3_bucket="reports",
        )
    )
    url = urlparse(storage.presign_get("projects/p/report.pdf", "report.pdf"))
    assert (url.scheme, url.netloc, url.path) == (
        "http",
        "localhost:8333",
        "/reports/projects/p/report.pdf",
    )
    query = parse_qs(url.query)
    assert query["response-content-disposition"] == ['attachment; filename="report.pdf"']
    assert "X-Amz-Signature" in query
    assert "response-content-disposition" not in parse_qs(
        urlparse(storage.presign_get("projects/p/report.json")).query
    )
