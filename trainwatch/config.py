"""설정 로딩: YAML 파일 + 환경변수 치환 + 검증."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta

from .models import SeatClass
from .providers import SearchRequest

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

MIN_INTERVAL = 1.0  # 초. 이보다 짧으면 사업자 쪽에서 차단당하기 쉽다.


class ConfigError(Exception):
    """설정 파일이 잘못된 경우."""


def expand_env(value):
    """문자열 안의 ${VAR} / ${VAR:-기본값} 을 환경변수로 치환한다."""

    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            return os.environ.get(match.group(1), match.group(2) or "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def normalize_date(value) -> str:
    """'2026-01-01', '20260101', date 객체, '내일' 등을 YYYYMMDD 로."""

    if isinstance(value, (date_cls, datetime)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if text in ("오늘", "today"):
        return date_cls.today().strftime("%Y%m%d")
    if text in ("내일", "tomorrow"):
        return (date_cls.today() + timedelta(days=1)).strftime("%Y%m%d")
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) == 8:
        return digits
    if len(digits) == 4:  # MMDD -> 올해(지났으면 내년)
        today = date_cls.today()
        candidate = f"{today.year}{digits}"
        if candidate < today.strftime("%Y%m%d"):
            candidate = f"{today.year + 1}{digits}"
        return candidate
    raise ConfigError(f"날짜 형식을 이해할 수 없습니다: {value!r} (예: 2026-01-01)")


def normalize_time(value, default: str = "000000") -> str:
    """'06:00', '0600', '060000', 6 등을 HHMMSS 로."""

    if value is None or value == "":
        return default
    if isinstance(value, (datetime,)):
        return value.strftime("%H%M%S")
    digits = re.sub(r"[^0-9]", "", str(value).strip())
    if len(digits) <= 2:
        digits = f"{int(digits or 0):02d}0000"
    elif len(digits) == 3:
        digits = f"0{digits}00"
    elif len(digits) == 4:
        digits = f"{digits}00"
    if len(digits) != 6 or not digits.isdigit() or int(digits[:2]) > 23:
        raise ConfigError(f"시각 형식을 이해할 수 없습니다: {value!r} (예: 06:00)")
    return digits


@dataclass
class NotifyConfig:
    console: bool = True
    bell: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""


@dataclass
class WatchConfig:
    """감시 루프 동작 설정."""

    interval: float = 5.0            # 조회 간격(초)
    jitter: float = 1.5              # 간격에 더해지는 무작위 편차(초)
    max_attempts: int = 0            # 0 = 무제한
    duration_minutes: float = 0.0    # 0 = 무제한
    stop_after: int = 1              # 예약 N건 잡으면 종료 (0 = 계속)
    dry_run: bool = False            # 자리를 찾아도 실제 예약은 하지 않음
    log_every: int = 20              # N번 조회마다 진행상황 로그

    def __post_init__(self) -> None:
        if self.interval < MIN_INTERVAL:
            raise ConfigError(
                f"interval 은 {MIN_INTERVAL}초 이상이어야 합니다 "
                "(너무 잦은 조회는 차단 사유가 됩니다)"
            )
        if self.jitter < 0:
            raise ConfigError("jitter 는 0 이상이어야 합니다")


@dataclass
class FilterConfig:
    """어떤 열차를 잡을지."""

    seat: SeatClass = SeatClass.ANY
    train_numbers: list[str] = field(default_factory=list)
    after: str = ""                  # HHMM 이후 출발
    before: str = ""                 # HHMM 이전 출발
    allow_waiting: bool = False      # 좌석이 없으면 예약대기라도 건다

    def matches(self, train) -> bool:
        if self.train_numbers and train.train_no not in self.train_numbers:
            return False
        if self.after and train.dep_hhmm < self.after:
            return False
        if self.before and train.dep_hhmm > self.before:
            return False
        return True


@dataclass
class AppConfig:
    provider: str = "srt"
    user_id: str = ""
    password: str = ""
    train_type: str = "all"          # 코레일 전용
    search: SearchRequest = field(default_factory=lambda: SearchRequest("", "", ""))
    filters: FilterConfig = field(default_factory=FilterConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def validate(self) -> None:
        if not self.search.departure or not self.search.arrival:
            raise ConfigError("출발역과 도착역을 지정해야 합니다")
        if self.search.departure == self.search.arrival:
            raise ConfigError("출발역과 도착역이 같습니다")
        self.search.validate()
        if self.provider != "fake" and not (self.user_id and self.password):
            raise ConfigError(
                "로그인 정보가 없습니다. 환경변수(SRT_ID/SRT_PW 또는 KORAIL_ID/KORAIL_PW)를 "
                "설정하거나 설정 파일의 credentials 를 채우세요."
            )


def _default_credentials(provider: str) -> tuple[str, str]:
    if provider == "korail":
        return os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")
    return os.environ.get("SRT_ID", ""), os.environ.get("SRT_PW", "")


def from_dict(raw: dict) -> AppConfig:
    """YAML/JSON 으로 읽은 dict 를 AppConfig 로."""

    data = expand_env(raw or {})
    provider = str(data.get("provider", "srt")).strip().lower()

    creds = data.get("credentials") or {}
    env_id, env_pw = _default_credentials(provider)
    user_id = str(creds.get("id") or "") or env_id
    password = str(creds.get("password") or "") or env_pw

    trip = data.get("trip") or {}
    try:
        search = SearchRequest(
            departure=str(trip.get("departure", "")).strip(),
            arrival=str(trip.get("arrival", "")).strip(),
            date=normalize_date(trip.get("date", date_cls.today())),
            time=normalize_time(trip.get("time")),
            adults=int(trip.get("adults", 1)),
            children=int(trip.get("children", 0)),
            seniors=int(trip.get("seniors", 0)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"trip 설정이 잘못되었습니다: {exc}") from exc

    f = data.get("filter") or {}
    filters = FilterConfig(
        seat=SeatClass.parse(f.get("seat", "any")),
        train_numbers=[str(n) for n in (f.get("train_numbers") or [])],
        after=normalize_time(f.get("after"), "")[:4] if f.get("after") else "",
        before=normalize_time(f.get("before"), "")[:4] if f.get("before") else "",
        allow_waiting=bool(f.get("allow_waiting", False)),
    )

    w = data.get("watch") or {}
    watch = WatchConfig(
        interval=float(w.get("interval", 5.0)),
        jitter=float(w.get("jitter", 1.5)),
        max_attempts=int(w.get("max_attempts", 0)),
        duration_minutes=float(w.get("duration_minutes", 0)),
        stop_after=int(w.get("stop_after", 1)),
        dry_run=bool(w.get("dry_run", False)),
        log_every=int(w.get("log_every", 20)),
    )

    n = data.get("notify") or {}
    telegram = n.get("telegram") or {}
    webhook = n.get("webhook") or {}
    notify = NotifyConfig(
        console=bool(n.get("console", True)),
        bell=bool(n.get("bell", True)),
        telegram_token=str(telegram.get("token", "")),
        telegram_chat_id=str(telegram.get("chat_id", "")),
        webhook_url=str(webhook.get("url", "")),
    )

    config = AppConfig(
        provider=provider,
        user_id=user_id,
        password=password,
        train_type=str(data.get("train_type", "all")).lower(),
        search=search,
        filters=filters,
        watch=watch,
        notify=notify,
    )
    return config


def load(path: str) -> AppConfig:
    """YAML 설정 파일을 읽는다."""

    import yaml

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"설정 파일 형식 오류: {exc}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError("설정 파일 최상위는 매핑(key: value)이어야 합니다")
    return from_dict(raw)
