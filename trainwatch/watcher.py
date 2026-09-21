"""감시 루프: 주기적으로 조회하다가 좌석이 보이면 즉시 예약을 시도한다."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

from .config import AppConfig
from .models import Reservation, Train
from .notify import Notifier
from .providers import AuthError, Provider, ProviderError, RateLimited, SoldOut

log = logging.getLogger(__name__)

MAX_BACKOFF = 60.0        # 연속 오류 시 대기 상한(초)
MAX_AUTH_RETRIES = 3      # 연속 로그인 실패 허용 횟수


@dataclass
class WatchStats:
    searches: int = 0
    reserve_attempts: int = 0
    errors: int = 0
    sold_out: int = 0
    started_at: float = 0.0
    reservations: list[Reservation] = field(default_factory=list)

    def summary(self, elapsed: float) -> str:
        return (
            f"조회 {self.searches}회 · 예약시도 {self.reserve_attempts}회 · "
            f"경합실패 {self.sold_out}회 · 오류 {self.errors}회 · "
            f"성공 {len(self.reservations)}건 · 경과 {elapsed / 60:.1f}분"
        )


class StopWatching(Exception):
    """루프 종료 신호 (내부용)."""


class Watcher:
    """provider 를 폴링하면서 조건에 맞는 좌석을 잡는다.

    시간 관련 함수는 주입 가능하게 두어 테스트에서 실제로 기다리지 않게 한다.
    """

    def __init__(
        self,
        provider: Provider,
        config: AppConfig,
        notifier: Notifier,
        sleep=time.sleep,
        monotonic=time.monotonic,
        jitter_fn=random.uniform,
    ):
        self.provider = provider
        self.config = config
        self.notifier = notifier
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter_fn
        self.stats = WatchStats()
        self._stopping = False
        self._auth_failures = 0
        self._consecutive_errors = 0

    # --- 외부 제어 ------------------------------------------------------
    def stop(self) -> None:
        """Ctrl-C 등 외부에서 루프를 멈춘다."""
        self._stopping = True

    # --- 내부 ----------------------------------------------------------
    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def _next_interval(self) -> float:
        jitter = self._jitter(0, self.config.watch.jitter) if self.config.watch.jitter else 0.0
        return max(0.0, self.config.watch.interval + jitter)

    def _should_continue(self, deadline: float | None) -> bool:
        if self._stopping:
            return False
        watch = self.config.watch
        if watch.max_attempts and self.stats.searches >= watch.max_attempts:
            log.info("최대 조회 횟수(%d)에 도달했습니다", watch.max_attempts)
            return False
        if deadline is not None and self._monotonic() >= deadline:
            log.info("지정한 감시 시간이 끝났습니다")
            return False
        if watch.stop_after and len(self.stats.reservations) >= watch.stop_after:
            return False
        return True

    def _candidates(self, trains: list[Train]) -> tuple[list[Train], list[Train]]:
        """(좌석 있는 열차, 예약대기 가능한 열차) 로 나눈다."""

        seat = self.config.filters.seat
        available, waiting = [], []
        for train in trains:
            if not self.config.filters.matches(train):
                continue
            if train.has_seat(seat):
                available.append(train)
            elif self.config.filters.allow_waiting and train.waiting_available:
                waiting.append(train)
        return available, waiting

    def _attempt(self, train: Train, waiting: bool) -> Reservation | None:
        """예약 시도. 경합에서 지면 None."""

        seat = self.config.filters.seat
        if self.config.watch.dry_run:
            kind = "예약대기 대상" if waiting else "빈자리 발견"
            self.notifier.send(f"[드라이런] {kind}: {train.describe()}", important=True)
            return Reservation(
                provider=self.provider.name,
                train=train,
                reservation_no="DRY-RUN",
                seat_class=seat,
                waiting=waiting,
            )

        self.stats.reserve_attempts += 1
        try:
            reservation = self.provider.reserve(
                train, self.config.search, seat=seat, waiting=waiting
            )
        except SoldOut as exc:
            self.stats.sold_out += 1
            log.info("놓쳤습니다 (%s): %s", train.describe(), exc)
            return None
        except AuthError:
            raise
        except ProviderError as exc:
            self.stats.errors += 1
            log.warning("예약 시도 실패 (%s): %s", train.describe(), exc)
            return None
        return reservation

    def _handle_success(self, reservation: Reservation) -> None:
        self.stats.reservations.append(reservation)
        message = (
            f"🎫 {reservation.describe()}\n"
            "→ 결제 기한 내에 앱/홈페이지에서 결제를 마쳐야 취소되지 않습니다."
        )
        self.notifier.send(message, important=True)
        log.info("%s", reservation.describe())

    def _tick(self) -> None:
        """한 번의 조회 + 필요한 경우 예약 시도."""

        self.stats.searches += 1
        trains = self.provider.search(self.config.search)
        self._consecutive_errors = 0
        self._auth_failures = 0

        available, waiting = self._candidates(trains)
        if not available and not waiting:
            if self.stats.searches % max(1, self.config.watch.log_every) == 0:
                log.info(
                    "조회 %d회째 - 아직 빈자리 없음 (후보 %d편성)",
                    self.stats.searches,
                    len([t for t in trains if self.config.filters.matches(t)]),
                )
            return

        for train in available:
            reservation = self._attempt(train, waiting=False)
            if reservation:
                self._handle_success(reservation)
                if self._reached_goal():
                    return
        for train in waiting:
            reservation = self._attempt(train, waiting=True)
            if reservation:
                self._handle_success(reservation)
                if self._reached_goal():
                    return

    def _reached_goal(self) -> bool:
        stop_after = self.config.watch.stop_after
        return bool(stop_after) and len(self.stats.reservations) >= stop_after

    def _on_error(self, exc: Exception) -> float:
        """오류를 기록하고 다음 대기 시간을 돌려준다."""

        self.stats.errors += 1
        self._consecutive_errors += 1
        if isinstance(exc, RateLimited):
            delay = min(MAX_BACKOFF, self.config.watch.interval * 4 * self._consecutive_errors)
            log.warning("호출이 제한되었습니다. %.1f초 쉬어갑니다: %s", delay, exc)
            return delay
        delay = min(MAX_BACKOFF, self.config.watch.interval * (2 ** min(self._consecutive_errors, 5)))
        log.warning("오류(%d회 연속): %s → %.1f초 후 재시도", self._consecutive_errors, exc, delay)
        return delay

    def _relogin(self, exc: AuthError) -> float:
        self._auth_failures += 1
        if self._auth_failures > MAX_AUTH_RETRIES:
            raise StopWatching(f"로그인에 계속 실패했습니다: {exc}")
        delay = min(MAX_BACKOFF, 2.0 * self._auth_failures)
        log.warning("세션이 끊겼습니다(%d/%d). 재로그인합니다: %s",
                    self._auth_failures, MAX_AUTH_RETRIES, exc)
        self._wait(delay)
        try:
            self.provider.login()
        except AuthError as login_exc:
            if self._auth_failures >= MAX_AUTH_RETRIES:
                raise StopWatching(f"로그인 실패: {login_exc}") from login_exc
            log.warning("재로그인 실패: %s", login_exc)
        return 0.0

    # --- 진입점 ---------------------------------------------------------
    def run(self) -> list[Reservation]:
        config = self.config
        config.validate()
        self.stats.started_at = self._monotonic()
        deadline = (
            self.stats.started_at + config.watch.duration_minutes * 60
            if config.watch.duration_minutes
            else None
        )

        self.notifier.send(
            f"🚄 감시 시작: [{config.provider}] {config.search.departure} → "
            f"{config.search.arrival} {config.search.date} "
            f"{config.search.time[:2]}:{config.search.time[2:4]} 이후 "
            f"/ 좌석 {config.filters.seat.value} / {config.watch.interval}초 간격"
            + (" / 드라이런" if config.watch.dry_run else "")
        )

        try:
            self.provider.login()
        except AuthError as exc:
            self.notifier.send(f"❌ 로그인 실패: {exc}", important=True)
            raise

        try:
            while self._should_continue(deadline):
                try:
                    self._tick()
                    delay = self._next_interval()
                except AuthError as exc:
                    delay = self._relogin(exc)
                except (ProviderError, RateLimited) as exc:
                    delay = self._on_error(exc)
                except StopWatching:
                    raise
                except Exception as exc:  # 예기치 못한 오류로 루프가 죽지 않게
                    log.exception("예상치 못한 오류")
                    delay = self._on_error(exc)
                if self._should_continue(deadline):
                    self._wait(delay)
        except StopWatching as exc:
            self.notifier.send(f"❌ 감시를 중단합니다: {exc}", important=True)
        finally:
            elapsed = self._monotonic() - self.stats.started_at
            summary = self.stats.summary(elapsed)
            log.info("%s", summary)
            if self.stats.reservations:
                self.notifier.send(f"✅ 감시 종료 — {summary}")
            else:
                self.notifier.send(f"🛑 감시 종료 — {summary}")

        return self.stats.reservations
