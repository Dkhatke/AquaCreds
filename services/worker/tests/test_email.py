"""Notification email rendering and delivery backends (no network: smtplib is faked)."""

import smtplib
from typing import Any, ClassVar

import pytest

from aqua_core.settings import Settings
from aqua_worker import email as mail

ADDRESS = "ranger.priya@example.org"


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, public_base_url="https://app.aquacreds.example/", **kw)


def _message() -> mail.OutgoingEmail:
    return mail.render(
        to=ADDRESS,
        full_name="Priya <Ranger>",
        title="High-severity alert: Bhitarkanika",
        body="NDVI fell below the envelope & stayed there.",
        link="/projects/p1/alerts",
        settings=_settings(),
    )


def test_render_plain_text_html_and_unsubscribe() -> None:
    m = _message()
    assert m.subject == "High-severity alert: Bhitarkanika"
    assert "https://app.aquacreds.example/projects/p1/alerts" in m.text
    assert m.unsubscribe_url == "https://app.aquacreds.example/settings/notifications"
    assert f"Unsubscribe or change which emails you get: {m.unsubscribe_url}" in m.text
    assert "Priya &lt;Ranger&gt;" in m.html
    assert "&amp; stayed" in m.html
    assert f'href="{m.unsubscribe_url}"' in m.html
    no_link = mail.render(
        to=ADDRESS, full_name="P", title="t", body="b", link=None, settings=_settings()
    )
    assert "Open in AquaCreds: https://app.aquacreds.example\n" in no_link.text

    mime = mail.to_mime(m, "AquaCreds <alerts@aquacreds.example>")
    assert mime["List-Unsubscribe"] == f"<{m.unsubscribe_url}>"
    assert mime["To"] == ADDRESS
    assert [p.get_content_type() for p in mime.iter_parts()] == ["text/plain", "text/html"]


def test_address_hash_is_keyed_and_stable() -> None:
    a = mail.address_hash(ADDRESS, _settings(jwt_secret="k" * 40))
    assert a == mail.address_hash(" Ranger.Priya@Example.org ", _settings(jwt_secret="k" * 40))
    assert a != mail.address_hash(ADDRESS, _settings(jwt_secret="j" * 40))
    assert len(a) == 16
    assert ADDRESS not in a


def test_console_backend_never_logs_the_address(capsys: pytest.CaptureFixture[str]) -> None:
    settings = _settings()
    backend = mail.email_backend(settings)
    assert isinstance(backend, mail.ConsoleEmailBackend)
    backend.send(_message())
    out = capsys.readouterr().out
    assert "email.console" in out
    assert mail.address_hash(ADDRESS, settings) in out
    assert ADDRESS not in out


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []
    fail_with: BaseException | None = None

    def __init__(self, host: str, port: int, timeout: float, context: Any = None) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[str] = []
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}:{password}")

    def send_message(self, msg: Any) -> None:
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances, FakeSMTP.fail_with = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _smtp(**kw: Any) -> mail.EmailBackend:
    return mail.email_backend(
        _settings(
            notification_email_backend="smtp",
            smtp_host="smtp-relay.example",
            notification_email_from="AquaCreds <alerts@aquacreds.example>",
            **kw,
        )
    )


def test_smtp_starttls_login_and_send(fake_smtp: type[FakeSMTP]) -> None:
    _smtp(smtp_username="apikey", smtp_password="s3cret").send(_message())
    [conn] = fake_smtp.instances
    assert (conn.host, conn.port) == ("smtp-relay.example", 587)
    assert conn.calls == ["starttls", "login:apikey:s3cret", "quit"]
    assert conn.sent[0]["From"] == "AquaCreds <alerts@aquacreds.example>"


def test_smtp_implicit_tls_and_plain(fake_smtp: type[FakeSMTP]) -> None:
    _smtp(smtp_security="ssl", smtp_port=465).send(_message())
    _smtp(smtp_security="none", smtp_port=25).send(_message())
    ssl_conn, plain = fake_smtp.instances
    assert ssl_conn.port == 465
    assert ssl_conn.context is not None
    assert ssl_conn.calls == ["quit"]
    assert plain.calls == ["quit"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (smtplib.SMTPAuthenticationError(535, b"bad"), mail.PermanentEmailError),
        (smtplib.SMTPRecipientsRefused({ADDRESS: (550, b"no")}), mail.PermanentEmailError),
        (smtplib.SMTPDataError(552, b"too big"), mail.PermanentEmailError),
        (smtplib.SMTPDataError(451, b"try later"), mail.TransientEmailError),
        (smtplib.SMTPServerDisconnected("gone"), mail.TransientEmailError),
        (TimeoutError("slow"), mail.TransientEmailError),
    ],
)
def test_smtp_errors_are_classified_without_leaking_the_address(
    fake_smtp: type[FakeSMTP], error: BaseException, expected: type[Exception]
) -> None:
    fake_smtp.fail_with = error
    with pytest.raises(expected) as info:
        _smtp().send(_message())
    assert ADDRESS not in str(info.value)


def test_smtp_requires_a_host() -> None:
    with pytest.raises(mail.PermanentEmailError, match="SMTP_HOST"):
        mail.email_backend(_settings(notification_email_backend="smtp"))
