"""알림 채널: 콘솔, 이메일(SMTP), 텔레그램, 웹훅(슬랙/디스코드 호환)."""

from __future__ import annotations

import json
import logging
import smtplib
import sys
from email.message import EmailMessage
from urllib.parse import quote

log = logging.getLogger(__name__)


class Notifier:
    """알림 채널 공통 인터페이스."""

    def send(self, message: str, important: bool = False) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def __init__(self, bell: bool = True, stream=None):
        self.bell = bell
        self.stream = stream or sys.stdout

    def send(self, message: str, important: bool = False) -> None:
        prefix = "\a" if (self.bell and important) else ""
        self.stream.write(f"{prefix}{message}\n")
        self.stream.flush()


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, timeout: float = 5.0, session=None):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def send(self, message: str, important: bool = False) -> None:
        url = f"https://api.telegram.org/bot{quote(self.token)}/sendMessage"
        try:
            resp = self.session.post(
                url,
                data={"chat_id": self.chat_id, "text": message},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                log.warning("텔레그램 알림 실패 (%s): %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # 알림 실패가 예매를 막으면 안 된다
            log.warning("텔레그램 알림 실패: %s", exc)


class WebhookNotifier(Notifier):
    """{"text": ...} 를 POST 한다. 슬랙/디스코드 웹훅에 그대로 쓸 수 있다."""

    def __init__(self, url: str, timeout: float = 5.0, session=None):
        self.url = url
        self.timeout = timeout
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def send(self, message: str, important: bool = False) -> None:
        payload = {"text": message, "content": message}  # 슬랙/디스코드 양쪽 키
        try:
            resp = self.session.post(
                self.url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                log.warning("웹훅 알림 실패 (%s): %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("웹훅 알림 실패: %s", exc)


class EmailNotifier(Notifier):
    """SMTP로 메일을 보낸다. Gmail은 2단계 인증 + 앱 비밀번호가 필요하다.

    기본값(only_important=True)은 예약 성공처럼 중요한 알림만 보낸다.
    감시 시작/종료 같은 일반 메시지까지 받고 싶으면 only_important=False.
    """

    def __init__(
        self,
        to: str,
        host: str = "smtp.gmail.com",
        port: int = 465,
        user: str = "",
        password: str = "",
        sender: str = "",
        use_ssl: bool = True,
        timeout: float = 15.0,
        subject_prefix: str = "[trainwatch]",
        only_important: bool = True,
        smtp_factory=None,
    ):
        self.to = [address.strip() for address in to.split(",") if address.strip()]
        self.host = host
        self.port = port
        self.user = user or (self.to[0] if self.to else "")
        self.password = password
        self.sender = sender or self.user
        self.use_ssl = use_ssl
        self.timeout = timeout
        self.subject_prefix = subject_prefix
        self.only_important = only_important
        self._smtp_factory = smtp_factory

    def _connect(self):
        if self._smtp_factory is not None:
            return self._smtp_factory(self.host, self.port, self.timeout)
        if self.use_ssl:
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout)

    def _build_message(self, message: str) -> EmailMessage:
        subject = message.strip().splitlines()[0] if message.strip() else "알림"
        mail = EmailMessage()
        mail["Subject"] = f"{self.subject_prefix} {subject}".strip()
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.to)
        mail.set_content(message)
        return mail

    def send(self, message: str, important: bool = False) -> None:
        if not self.to or (self.only_important and not important):
            return
        try:
            with self._connect() as smtp:
                if not self.use_ssl and self._smtp_factory is None:
                    smtp.starttls()
                if self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(self._build_message(message))
            log.info("메일 알림을 보냈습니다: %s", ", ".join(self.to))
        except Exception as exc:  # 알림 실패가 예매를 막으면 안 된다
            log.warning("메일 알림 실패: %s", exc)


class MultiNotifier(Notifier):
    def __init__(self, *notifiers: Notifier):
        self.notifiers = [n for n in notifiers if n is not None]

    def send(self, message: str, important: bool = False) -> None:
        for notifier in self.notifiers:
            notifier.send(message, important=important)


def build(config) -> Notifier:
    """NotifyConfig 로부터 알림기를 조립한다."""

    channels: list[Notifier] = []
    if config.console:
        channels.append(ConsoleNotifier(bell=config.bell))
    if config.telegram_token and config.telegram_chat_id:
        channels.append(TelegramNotifier(config.telegram_token, config.telegram_chat_id))
    if config.webhook_url:
        channels.append(WebhookNotifier(config.webhook_url))
    if config.email_to:
        if not config.email_password:
            log.warning(
                "메일 주소(%s)는 설정됐지만 SMTP 비밀번호가 없습니다. "
                "Gmail 앱 비밀번호를 SMTP_PASSWORD 환경변수에 넣어주세요. (메일 알림 꺼짐)",
                config.email_to,
            )
        else:
            channels.append(
                EmailNotifier(
                    to=config.email_to,
                    host=config.email_host,
                    port=config.email_port,
                    user=config.email_user,
                    password=config.email_password,
                    sender=config.email_from,
                    use_ssl=config.email_ssl,
                    only_important=config.email_only_important,
                )
            )
    return MultiNotifier(*channels)
