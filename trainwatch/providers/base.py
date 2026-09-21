"""예매 사업자(SRT/코레일) 공통 인터페이스."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..models import Reservation, SeatClass, Train


class ProviderError(Exception):
    """사업자 API 호출 중 발생한 일반 오류."""


class AuthError(ProviderError):
    """로그인 실패 또는 세션 만료. 감시 루프가 재로그인을 시도한다."""


class SoldOut(ProviderError):
    """예약 시도 시점에 이미 좌석이 나간 경우 (정상적인 경합 실패)."""


class RateLimited(ProviderError):
    """호출이 너무 잦아 사업자 쪽에서 차단/지연시킨 경우."""


@dataclass
class SearchRequest:
    """열차 조회 조건."""

    departure: str
    arrival: str
    date: str                 # YYYYMMDD
    time: str = "000000"      # HHMMSS, 이 시각 이후 열차만 조회
    adults: int = 1
    children: int = 0
    seniors: int = 0

    @property
    def passengers(self) -> int:
        return self.adults + self.children + self.seniors

    def validate(self) -> None:
        if len(self.date) != 8 or not self.date.isdigit():
            raise ValueError(f"date 는 YYYYMMDD 형식이어야 합니다: {self.date!r}")
        if len(self.time) != 6 or not self.time.isdigit():
            raise ValueError(f"time 은 HHMMSS 형식이어야 합니다: {self.time!r}")
        if self.passengers < 1:
            raise ValueError("승객 수는 1명 이상이어야 합니다")


class Provider(abc.ABC):
    """로그인 / 조회 / 예약 세 가지만 구현하면 감시 루프가 동작한다."""

    name: str = "provider"

    @abc.abstractmethod
    def login(self) -> None:
        """세션을 연다. 실패 시 AuthError."""

    @abc.abstractmethod
    def search(self, request: SearchRequest) -> list[Train]:
        """조건에 맞는 열차 목록을 반환한다."""

    @abc.abstractmethod
    def reserve(
        self,
        train: Train,
        request: SearchRequest,
        seat: SeatClass = SeatClass.ANY,
        waiting: bool = False,
    ) -> Reservation:
        """좌석을 잡는다. 이미 매진이면 SoldOut."""

    def close(self) -> None:  # pragma: no cover - 선택 구현
        """리소스 정리."""


@dataclass
class FakeProvider(Provider):
    """테스트/드라이런용. 정해진 횟수만큼 매진을 반환한 뒤 자리를 내준다."""

    name: str = "fake"
    open_after: int = 3              # N번째 조회부터 좌석이 열림
    trains: list[Train] = field(default_factory=list)
    fail_reserve_times: int = 0      # 예약 시도 중 SoldOut 을 낼 횟수
    searches: int = 0
    reserve_attempts: int = 0
    logins: int = 0

    def login(self) -> None:
        self.logins += 1

    def _base_trains(self, request: SearchRequest) -> list[Train]:
        if self.trains:
            return self.trains
        return [
            Train(
                provider=self.name,
                train_name="FAKE",
                train_no=f"{100 + i}",
                dep_station=request.departure,
                arr_station=request.arrival,
                dep_date=request.date,
                dep_time=f"{8 + i:02d}0000",
                arr_time=f"{10 + i:02d}0000",
            )
            for i in range(3)
        ]

    def search(self, request: SearchRequest) -> list[Train]:
        self.searches += 1
        opened = self.searches >= self.open_after
        result = []
        for i, train in enumerate(self._base_trains(request)):
            # 첫 번째 편성에만 자리가 난다고 가정.
            result.append(
                Train(**{**train.__dict__, "general_seat": opened and i == 0})
            )
        return result

    def reserve(
        self,
        train: Train,
        request: SearchRequest,
        seat: SeatClass = SeatClass.ANY,
        waiting: bool = False,
    ) -> Reservation:
        self.reserve_attempts += 1
        if self.reserve_attempts <= self.fail_reserve_times:
            raise SoldOut("방금 다른 사람이 가져갔습니다")
        return Reservation(
            provider=self.name,
            train=train,
            reservation_no=f"FAKE{self.reserve_attempts:06d}",
            seat_class=seat,
            waiting=waiting,
        )
