"""Retirement certificate PDF (WeasyPrint from a Jinja2 template, like the MRV report PDF).

Deterministic: the document is a pure function of the retirement's indexed facts and the public
base URL. Its PDF dates are the retirement's block time and nothing reads the clock, so the same
retirement always renders to the same bytes. Rendering never fetches anything: the only resource
is the inline QR code (data: URI), and every other URL scheme is refused.
"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import segno
from jinja2 import Environment, PackageLoader, select_autoescape

# Same wording as the MRV report PDF (aqua_mrv.report.STATUS_LABEL; a test keeps them in sync).
STATUS_LABEL = "Estimated — pending accredited verification; testnet registry units"
METHODOLOGY_TEXT = {
    "ccts_bm_fr05_001": "India CCTS BM FR05.001 (mangrove afforestation/reforestation)",
    "vm0033_v2_1": "Verra VM0033 v2.1 (tidal wetland and seagrass restoration)",
}
CREDIT_TYPE_TEXT = {
    "temporary": "Temporary (tCCC, expires)",
    "long_term": "Long-term (lCCC)",
    None: "Unknown (vintage not indexed)",
}

_env = Environment(
    loader=PackageLoader("aqua_api", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


class CertificateNotReadyError(Exception):
    """The retirement's block time is not indexed yet (the certificate must not use the clock)."""


@dataclass(frozen=True)
class CertificateData:
    retirement_id: int
    amount: int
    credit_unit: str
    credit_type: str | None
    token_id: int
    project_name: str | None
    project_key: str | None
    methodology: str
    report_id: int | None
    report_hash: str | None
    beneficiary: str
    purpose: str
    chain_id: int
    contract_address: str
    tx_hash: str
    log_index: int
    block_number: int
    block_hash: str
    block_time: datetime
    explorer_url: str | None
    certificate_url: str
    payload_sha256: str


def _qr_data_uri(url: str) -> str:
    uri: str = segno.make(url, error="m", micro=False).png_data_uri(scale=8, border=4)
    return uri


def to_html(data: CertificateData) -> str:
    when = data.block_time.astimezone(UTC)
    context = {
        **asdict(data),
        "block_time_iso": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "block_time_text": when.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "credit_type_text": CREDIT_TYPE_TEXT.get(data.credit_type, str(data.credit_type)),
        "status_label": STATUS_LABEL,
        "qr_data_uri": _qr_data_uri(data.certificate_url),
    }
    return _env.get_template("retirement_certificate.html.j2").render(c=context)


def etag(html: str) -> str:
    """Strong validator from the rendering input (the output is a pure function of it)."""
    import weasyprint

    digest = hashlib.sha256(f"{weasyprint.__version__}\n{html}".encode()).hexdigest()
    return f'"{digest[:32]}"'


def to_pdf(html: str) -> bytes:
    from weasyprint import HTML  # imported lazily: needs the Pango system library
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False)
    pdf: bytes = HTML(string=html, url_fetcher=fetcher).write_pdf()
    return pdf
