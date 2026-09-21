"""알림 채널: 콘솔, 텔레그램, 웹훅(슬랙/디스코드 호환)."""

from __future__ import annotations

import json
import logging
import sys
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
    return MultiNotifier(*channels)
