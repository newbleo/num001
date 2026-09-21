"""SRT(수서고속철도) 모바일 API 클라이언트.

주의: 비공개 모바일 API라 엔드포인트/파라미터가 예고 없이 바뀔 수 있다.
로그인이나 예약이 실패하면 `--debug` 로 원본 응답을 확인하고 payload 를 맞춰야 한다.
"""

from __future__ import annotations

import logging
import re

import requests

from ..models import Reservation, SeatClass, Train
from .base import AuthError, Provider, ProviderError, RateLimited, SearchRequest, SoldOut

log = logging.getLogger(__name__)

BASE = "https://app.srail.or.kr:443"
MAIN = f"{BASE}/main/main.do"
LOGIN = f"{BASE}/apb/selectListApb01080.do"
LOGOUT = f"{BASE}/login/loginOut.do"
SEARCH = f"{BASE}/ara/selectListAra10007_n.do"
RESERVE = f"{BASE}/arc/selectListArc05013_n.do"

HEADERS = {
    "User-Agent": "nSRT-Mobile-App-Android-V2",
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
}

# SRT 정차역 코드
STATIONS = {
    "수서": "0551", "동탄": "0552", "평택지제": "0553", "지제": "0553",
    "천안아산": "0502", "오송": "0297", "대전": "0010", "김천구미": "0507",
    "서대구": "0506", "동대구": "0015", "신경주": "0508", "울산(통도사)": "0509",
    "울산": "0509", "부산": "0020", "공주": "0514", "익산": "0030",
    "정읍": "0033", "광주송정": "0036", "나주": "0037", "목포": "0041",
    "전주": "0045", "남원": "0048", "곡성": "0049", "구례구": "0050",
    "순천": "0051", "여천": "0053", "여수EXPO": "0054", "여수": "0054",
    "포항": "0515", "진주": "0063", "창원중앙": "0512", "창원": "0508",
    "마산": "0059", "밀양": "0017",
}

# 로그인 구분 코드
_ID_MEMBERSHIP, _ID_EMAIL, _ID_PHONE = "1", "2", "3"

_RE_EMAIL = re.compile(r"[^@]+@[^@]+\.[^@]+")
_RE_PHONE = re.compile(r"(\d{3})-(\d{3,4})-(\d{4})")


def station_code(name: str) -> str:
    code = STATIONS.get(name.strip())
    if not code:
        raise ValueError(
            f"SRT 역 이름을 찾을 수 없습니다: {name!r} "
            f"(가능: {', '.join(sorted(set(STATIONS)))})"
        )
    return code


def _id_type(user_id: str) -> str:
    if _RE_EMAIL.match(user_id):
        return _ID_EMAIL
    if _RE_PHONE.match(user_id):
        return _ID_PHONE
    return _ID_MEMBERSHIP


class SRTProvider(Provider):
    name = "srt"

    def __init__(self, user_id: str, password: str, timeout: float = 10.0, session=None):
        if not user_id or not password:
            raise ValueError("SRT 아이디/비밀번호가 필요합니다 (SRT_ID, SRT_PW)")
        self.user_id = user_id
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.logged_in = False

    # --- 내부 유틸 -----------------------------------------------------
    def _post(self, url: str, data: dict) -> requests.Response:
        try:
            resp = self.session.post(url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"SRT 요청 실패: {exc}") from exc
        if resp.status_code in (429, 503):
            raise RateLimited(f"SRT 응답 {resp.status_code} - 조회 간격을 늘리세요")
        if resp.status_code >= 400:
            raise ProviderError(f"SRT 응답 {resp.status_code}")
        return resp

    def _json(self, resp: requests.Response) -> dict:
        try:
            return resp.json()
        except ValueError as exc:
            snippet = resp.text[:200].replace("\n", " ")
            raise ProviderError(f"SRT 응답을 JSON 으로 읽을 수 없습니다: {snippet}") from exc

    @staticmethod
    def _check_result(payload: dict) -> None:
        """공통 결과 코드 확인. SRT 는 상황에 따라 키가 달라 방어적으로 읽는다."""
        status = payload.get("strResult") or payload.get("result")
        message = (
            payload.get("msgTxt")
            or payload.get("MSG")
            or payload.get("resultMessage")
            or ""
        )
        if status and str(status).upper() in ("FAIL", "F", "N"):
            if any(k in message for k in ("로그인", "세션", "다시 로그인")):
                raise AuthError(f"SRT 세션 만료: {message}")
            if any(k in message for k in ("매진", "잔여", "없습니다", "좌석")):
                raise SoldOut(message)
            raise ProviderError(f"SRT: {message or '알 수 없는 오류'}")

    # --- Provider 인터페이스 -------------------------------------------
    def login(self) -> None:
        self.session.cookies.clear()
        data = {
            "auto": "Y",
            "check": "Y",
            "page": "menu",
            "deviceKey": "-",
            "customerYn": "",
            "login_referer": MAIN,
            "srchDvCd": _id_type(self.user_id),
            "srchDvNm": self.user_id,
            "hmpgPwdCphd": self.password,
        }
        resp = self._post(LOGIN, data)
        body = resp.text
        if "존재하지않는 회원입니다" in body or "비밀번호 오류" in body or "로그인 후 이용" in body:
            raise AuthError("SRT 로그인 실패: 아이디 또는 비밀번호를 확인하세요")
        if "userMap" not in body and "결과" not in body:
            log.debug("SRT 로그인 응답: %s", body[:300])
        self.logged_in = True
        log.info("SRT 로그인 완료 (%s)", self.user_id)

    def search(self, request: SearchRequest) -> list[Train]:
        if not self.logged_in:
            self.login()
        dep, arr = station_code(request.departure), station_code(request.arrival)
        data = {
            "chtnDvCd": "1",
            "arriveTime": "N",
            "seatAttCd": "015",
            "psgNum": str(request.passengers),
            "trnGpCd": "109",
            "stlbTrnClsfCd": "05",
            "dptDt": request.date,
            "dptTm": request.time,
            "dptRsStnCd": dep,
            "arvRsStnCd": arr,
            "dptRsStnCdNm": request.departure,
            "arvRsStnCdNm": request.arrival,
        }
        payload = self._json(self._post(SEARCH, data))
        rows = (payload.get("outDataSets") or {}).get("dsOutput1")
        if rows is None:
            message = payload.get("MSG") or payload.get("resultMessage") or ""
            if "로그인" in message:
                self.logged_in = False
                raise AuthError(f"SRT 세션 만료: {message}")
            if "조회 결과가 없" in message or "열차가 없" in message:
                return []
            raise ProviderError(f"SRT 조회 실패: {message or payload}")
        return [self._to_train(row, request) for row in rows]

    def _to_train(self, row: dict, request: SearchRequest) -> Train:
        def flag(value: str) -> bool:
            # "예약가능", "1석", "9석" 등은 가능 / "매진", "-" 는 불가
            text = (value or "").strip()
            return bool(text) and "매진" not in text and text not in ("-", "예약대기")

        return Train(
            provider=self.name,
            train_name="SRT",
            train_no=str(row.get("trnNo", "")).lstrip("0") or str(row.get("trnNo", "")),
            dep_station=row.get("dptRsStnNm") or request.departure,
            arr_station=row.get("arvRsStnNm") or request.arrival,
            dep_date=row.get("dptDt", request.date),
            dep_time=row.get("dptTm", "000000"),
            arr_time=row.get("arvTm", "000000"),
            general_seat=flag(row.get("gnrmRsvPsbStr")),
            special_seat=flag(row.get("sprmRsvPsbStr")),
            waiting_available=str(row.get("rsvWaitPsbCd", "")) not in ("", "-1", "9"),
            raw=row,
        )

    def reserve(
        self,
        train: Train,
        request: SearchRequest,
        seat: SeatClass = SeatClass.ANY,
        waiting: bool = False,
    ) -> Reservation:
        if not self.logged_in:
            self.login()
        row = train.raw
        # 특실 요청이면 좌석 속성 코드를 특실로 바꾼다.
        want_special = seat is SeatClass.SPECIAL or (
            seat is SeatClass.ANY and train.special_seat and not train.general_seat
        )
        data = {
            "reserveType": "11",
            "jobId": "1900" if waiting else "1101",  # 1900: 예약대기
            "jrnyCnt": "1",
            "jrnyTpCd": "11",
            "jrnySqno1": "001",
            "stndFlg": "N",
            "trnGpCd1": str(row.get("trnGpCd", "109")),
            "stlbTrnClsfCd1": str(row.get("stlbTrnClsfCd", "05")),
            "dptDt1": train.dep_date,
            "dptTm1": train.dep_time,
            "runDt1": str(row.get("runDt", train.dep_date)),
            "trnNo1": f"{int(train.train_no):05d}",
            "dptRsStnCd1": str(row.get("dptRsStnCd", station_code(request.departure))),
            "arvRsStnCd1": str(row.get("arvRsStnCd", station_code(request.arrival))),
            "totPrnb": str(request.passengers),
            "psgGridcnt": "1",
            "psgTpCd1": "1",
            "psgInfoPerPrnb1": str(request.passengers),
            "locSeatAttCd1": "000",
            "rqSeatAttCd1": "015",
            "dirSeatAttCd1": "009",
            "smkSeatAttCd1": "000",
            "etcSeatAttCd1": "000",
            "psrmClCd1": "2" if want_special else "1",
            "chtnDvCd": "1",
        }
        payload = self._json(self._post(RESERVE, data))
        self._check_result(payload)
        rows = (payload.get("outDataSets") or {}).get("dsOutput1") or [{}]
        info = rows[0] if isinstance(rows, list) and rows else {}
        return Reservation(
            provider=self.name,
            train=train,
            reservation_no=str(info.get("pnrNo", "")),
            seat_class=SeatClass.SPECIAL if want_special else SeatClass.GENERAL,
            waiting=waiting,
            pay_deadline=str(info.get("tmrLmtDt", "") or info.get("rcvdAmt", "")),
            raw=payload,
        )

    def close(self) -> None:
        if self.logged_in:
            try:
                self._post(LOGOUT, {})
            except ProviderError:
                pass
            self.logged_in = False
        self.session.close()
