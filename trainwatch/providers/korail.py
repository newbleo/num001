"""코레일(레츠코레일) 모바일 API 클라이언트. KTX/새마을/무궁화 등을 다룬다.

SRT 와 마찬가지로 비공개 모바일 API이므로 파라미터가 바뀔 수 있다.
코레일은 조회 시 역 이름을 그대로 쓰고, 예약에 필요한 역 코드는 조회 응답에서 받아 쓴다.
"""

from __future__ import annotations

import logging

import requests

from ..models import Reservation, SeatClass, Train
from .base import AuthError, Provider, ProviderError, RateLimited, SearchRequest, SoldOut

log = logging.getLogger(__name__)

BASE = "https://smart.letskorail.com:443"
LOGIN = f"{BASE}/classes/com.korail.mobile.login.Login"
LOGOUT = f"{BASE}/classes/com.korail.mobile.common.logout"
SCHEDULE = f"{BASE}/classes/com.korail.mobile.seatMovie.ScheduleView"
RESERVE = f"{BASE}/classes/com.korail.mobile.certification.TicketReservation"

DEVICE = "AD"
APP_VERSION = "231101001"
HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel Build/TQ3A)",
    "Accept": "application/json",
    "Accept-Language": "ko-KR",
}

# 열차 종류 코드 (selGoTrain / txtTrnGpCd)
TRAIN_TYPES = {
    "all": ("05", "109"),        # 전체
    "ktx": ("100", "300"),       # KTX/KTX-산천
    "saemaeul": ("101", "300"),  # 새마을호(ITX-새마을 포함)
    "mugunghwa": ("102", "300"), # 무궁화호
    "itx": ("104", "300"),       # ITX-청춘
}

# 좌석 가능 코드: '11' 예약가능, '00'/'13' 매진
_SEAT_OK = "11"


class KorailProvider(Provider):
    name = "korail"

    def __init__(
        self,
        user_id: str,
        password: str,
        train_type: str = "all",
        timeout: float = 10.0,
        session=None,
    ):
        if not user_id or not password:
            raise ValueError("코레일 아이디/비밀번호가 필요합니다 (KORAIL_ID, KORAIL_PW)")
        self.user_id = user_id
        self.password = password
        if train_type not in TRAIN_TYPES:
            raise ValueError(
                f"train_type 은 {', '.join(TRAIN_TYPES)} 중 하나여야 합니다: {train_type!r}"
            )
        self.train_type = train_type
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.logged_in = False
        self.key = ""       # 로그인 후 받는 세션 키
        self.member_no = ""

    # --- 내부 유틸 -----------------------------------------------------
    def _post(self, url: str, data: dict) -> dict:
        try:
            resp = self.session.post(url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"코레일 요청 실패: {exc}") from exc
        if resp.status_code in (429, 503):
            raise RateLimited(f"코레일 응답 {resp.status_code} - 조회 간격을 늘리세요")
        if resp.status_code >= 400:
            raise ProviderError(f"코레일 응답 {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            snippet = resp.text[:200].replace("\n", " ")
            raise ProviderError(f"코레일 응답을 JSON 으로 읽을 수 없습니다: {snippet}") from exc

    def _check(self, payload: dict) -> dict:
        if payload.get("strResult") == "SUCC":
            return payload
        code = str(payload.get("h_msg_cd", ""))
        message = payload.get("h_msg_txt", "") or "알 수 없는 오류"
        # P058: 로그인 필요 / P100: 세션 만료 계열
        if code.startswith("P05") or code in ("P100", "WRG000000") or "로그인" in message:
            self.logged_in = False
            raise AuthError(f"코레일 세션 만료: {message}")
        if "잔여" in message or "매진" in message or "좌석" in message:
            raise SoldOut(message)
        if "조회 결과" in message or "열차가 없" in message:
            return {"trn_infos": {"trn_info": []}}
        raise ProviderError(f"코레일: {message} ({code})")

    # --- Provider 인터페이스 -------------------------------------------
    def login(self) -> None:
        self.session.cookies.clear()
        if "@" in self.user_id:
            input_flag = "2"        # 이메일
        elif self.user_id.replace("-", "").isdigit() and len(self.user_id.replace("-", "")) >= 10:
            input_flag = "3"        # 휴대폰 번호
        else:
            input_flag = "1"        # 멤버십 번호
        data = {
            "Device": DEVICE,
            "Version": APP_VERSION,
            "txtInputFlg": input_flag,
            "txtMemberNo": self.user_id,
            "txtPwd": self.password,
        }
        payload = self._post(LOGIN, data)
        if payload.get("strResult") != "SUCC":
            raise AuthError(
                f"코레일 로그인 실패: {payload.get('h_msg_txt', '아이디/비밀번호를 확인하세요')}"
            )
        self.key = payload.get("Key", "")
        self.member_no = (payload.get("strMbCrdNo") or "")
        self.logged_in = True
        log.info("코레일 로그인 완료 (%s)", self.user_id)

    def search(self, request: SearchRequest) -> list[Train]:
        if not self.logged_in:
            self.login()
        sel_train, trn_gp = TRAIN_TYPES[self.train_type]
        data = {
            "Device": DEVICE,
            "Version": APP_VERSION,
            "Key": self.key,
            "radJobId": "1",
            "selGoTrain": sel_train,
            "txtGoAbrdDt": request.date,
            "txtGoHour": request.time,
            "txtGoStart": request.departure,
            "txtGoEnd": request.arrival,
            "txtPsgFlg_1": str(request.adults),
            "txtPsgFlg_2": str(request.children),
            "txtPsgFlg_3": str(request.seniors),
            "txtPsgFlg_4": "0",
            "txtPsgFlg_5": "0",
            "txtCardPsgCnt": "0",
            "txtSeatAttCd_2": "000",
            "txtSeatAttCd_3": "000",
            "txtSeatAttCd_4": "015",
            "txtTrnGpCd": trn_gp,
            "txtMenuId": "11",
            "txtJobDv": "",
            "txtGdNo": "",
        }
        payload = self._check(self._post(SCHEDULE, data))
        rows = (payload.get("trn_infos") or {}).get("trn_info") or []
        return [self._to_train(row) for row in rows]

    def _to_train(self, row: dict) -> Train:
        return Train(
            provider=self.name,
            train_name=row.get("h_trn_clsf_nm", "열차"),
            train_no=str(row.get("h_trn_no", "")).lstrip("0"),
            dep_station=row.get("h_dpt_rs_stn_nm", ""),
            arr_station=row.get("h_arv_rs_stn_nm", ""),
            dep_date=row.get("h_dpt_dt", ""),
            dep_time=row.get("h_dpt_tm", "000000"),
            arr_time=row.get("h_arv_tm", "000000"),
            general_seat=row.get("h_gen_rs_psb_cd") == _SEAT_OK,
            special_seat=row.get("h_srcar_rs_psb_cd") == _SEAT_OK,
            waiting_available=row.get("h_wait_rs_flg") == "Y",
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
        want_special = seat is SeatClass.SPECIAL or (
            seat is SeatClass.ANY and train.special_seat and not train.general_seat
        )
        psrm_cl_cd = "2" if want_special else "1"   # 1: 일반실, 2: 특실
        data = {
            "Device": DEVICE,
            "Version": APP_VERSION,
            "Key": self.key,
            "txtGdNo": "",
            "txtJobId": "1900" if waiting else "1101",  # 1900: 예약대기
            "txtTotPsgCnt": str(request.passengers),
            "txtSeatAttCd1": "000",
            "txtSeatAttCd2": "000",
            "txtSeatAttCd3": "000",
            "txtSeatAttCd4": "015",
            "txtSeatAttCd5": "000",
            "hidFreeFlg": "N",
            "txtStndFlg": "N",
            "txtMenuId": "11",
            "txtSrcarCnt": "0",
            "txtJrnyCnt": "1",
            # 여정 1
            "txtJrnySqno1": "001",
            "txtJrnyTpCd1": "11",
            "txtDptDt1": train.dep_date,
            "txtDptRsStnCd1": row.get("h_dpt_rs_stn_cd", ""),
            "txtDptTm1": train.dep_time,
            "txtArvRsStnCd1": row.get("h_arv_rs_stn_cd", ""),
            "txtTrnNo1": f"{int(train.train_no):05d}" if train.train_no.isdigit() else train.train_no,
            "txtRunDt1": row.get("h_run_dt", train.dep_date),
            "txtTrnClsfCd1": row.get("h_trn_clsf_cd", ""),
            "txtPsrmClCd1": psrm_cl_cd,
            "txtTrnGpCd1": row.get("h_trn_gp_cd", "300"),
            "txtChgFlg1": "",
            # 승객 1그룹 (어른 기준)
            "txtPsgTpCd1": "1",
            "txtDiscKndCd1": "000",
            "txtCompaPsgCnt1": str(request.passengers),
            "txtCardCode_1": "",
            "txtCardNo_1": "",
            "txtCardPw_1": "",
        }
        payload = self._check(self._post(RESERVE, data))
        return Reservation(
            provider=self.name,
            train=train,
            reservation_no=str(payload.get("h_pnr_no", "")),
            seat_class=SeatClass.SPECIAL if want_special else SeatClass.GENERAL,
            waiting=waiting,
            pay_deadline=str(payload.get("h_ntisu_lmt_dt", "") + payload.get("h_ntisu_lmt_tm", "")),
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
