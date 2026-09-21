"""도메인 모델: 좌석 등급, 열차, 예약 결과."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SeatClass(str, Enum):
    """원하는 좌석 등급."""

    GENERAL = "general"  # 일반실
    SPECIAL = "special"  # 특실
    ANY = "any"          # 아무거나 먼저 나오는 것

    @classmethod
    def parse(cls, value: "str | SeatClass") -> "SeatClass":
        if isinstance(value, cls):
            return value
        key = str(value).strip().lower()
        aliases = {
            "일반": cls.GENERAL,
            "일반실": cls.GENERAL,
            "특": cls.SPECIAL,
            "특실": cls.SPECIAL,
            "아무": cls.ANY,
            "any": cls.ANY,
            "all": cls.ANY,
        }
        if key in aliases:
            return aliases[key]
        return cls(key)


@dataclass(frozen=True)
class Train:
    """검색 결과 한 편성."""

    provider: str
    train_name: str          # "SRT", "KTX", "무궁화호" ...
    train_no: str
    dep_station: str
    arr_station: str
    dep_date: str            # YYYYMMDD
    dep_time: str            # HHMMSS
    arr_time: str            # HHMMSS
    general_seat: bool = False
    special_seat: bool = False
    waiting_available: bool = False   # 예약대기 가능 여부
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.dep_date}:{self.train_no}:{self.dep_time}"

    @property
    def dep_hhmm(self) -> str:
        return self.dep_time[:4]

    def has_seat(self, seat: SeatClass) -> bool:
        if seat is SeatClass.GENERAL:
            return self.general_seat
        if seat is SeatClass.SPECIAL:
            return self.special_seat
        return self.general_seat or self.special_seat

    def seat_label(self, seat: SeatClass) -> str:
        if seat is SeatClass.ANY:
            return "일반실" if self.general_seat else "특실"
        return "일반실" if seat is SeatClass.GENERAL else "특실"

    def describe(self) -> str:
        def fmt(t: str) -> str:
            return f"{t[:2]}:{t[2:4]}"

        seats = []
        if self.general_seat:
            seats.append("일반실")
        if self.special_seat:
            seats.append("특실")
        if self.waiting_available:
            seats.append("예약대기")
        avail = ", ".join(seats) if seats else "매진"
        return (
            f"{self.train_name} {self.train_no} "
            f"{self.dep_station}({fmt(self.dep_time)}) → {self.arr_station}({fmt(self.arr_time)}) "
            f"[{avail}]"
        )


@dataclass
class Reservation:
    """예약 성공 결과. 결제는 사용자가 앱/웹에서 직접 마무리한다."""

    provider: str
    train: Train
    reservation_no: str = ""
    seat_class: SeatClass = SeatClass.ANY
    waiting: bool = False        # 예약대기로 잡힌 경우
    pay_deadline: str = ""       # 결제 기한 (제공되는 경우)
    raw: dict = field(default_factory=dict, repr=False)

    def describe(self) -> str:
        kind = "예약대기" if self.waiting else "예약"
        parts = [f"[{self.provider}] {kind} 성공: {self.train.describe()}"]
        if self.reservation_no:
            parts.append(f"예약번호 {self.reservation_no}")
        if self.pay_deadline:
            parts.append(f"결제기한 {self.pay_deadline}")
        return " | ".join(parts)
