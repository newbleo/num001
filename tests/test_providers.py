"""SRT/코레일 클라이언트의 응답 파싱과 오류 처리 검증 (네트워크 없이 스텁 세션 사용)."""

import json
import unittest

from trainwatch.models import SeatClass
from trainwatch.providers import SearchRequest
from trainwatch.providers.base import AuthError, ProviderError, RateLimited, SoldOut
from trainwatch.providers.korail import KorailProvider
from trainwatch.providers.srt import SRTProvider, station_code


class StubResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text else json.dumps(payload or {}, ensure_ascii=False)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class StubSession:
    """정해진 응답을 순서대로 돌려주는 requests.Session 대역."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.cookies = type("C", (), {"clear": lambda self: None})()

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data or {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


REQUEST = SearchRequest(departure="수서", arrival="부산", date="20260101", time="060000")

SRT_ROW = {
    "trnNo": "00301",
    "stlbTrnClsfCd": "17",
    "trnGpCd": "109",
    "dptDt": "20260101",
    "dptTm": "080000",
    "arvTm": "103000",
    "dptRsStnCd": "0551",
    "arvRsStnCd": "0020",
    "dptRsStnNm": "수서",
    "arvRsStnNm": "부산",
    "gnrmRsvPsbStr": "예약가능",
    "sprmRsvPsbStr": "매진",
    "rsvWaitPsbCd": "9",
}


class SRTTest(unittest.TestCase):
    def provider(self, responses):
        return SRTProvider("a@b.com", "pw", session=StubSession(responses))

    def test_station_code_lookup(self):
        self.assertEqual(station_code("수서"), "0551")
        with self.assertRaises(ValueError):
            station_code("없는역")

    def test_login_failure_raises_auth_error(self):
        srt = self.provider([StubResponse(text="존재하지않는 회원입니다")])
        with self.assertRaises(AuthError):
            srt.login()

    def test_search_parses_seat_flags(self):
        srt = self.provider(
            [
                StubResponse(text="userMap"),
                StubResponse({"outDataSets": {"dsOutput1": [SRT_ROW]}}),
            ]
        )
        trains = srt.search(REQUEST)
        self.assertEqual(len(trains), 1)
        train = trains[0]
        self.assertEqual(train.train_no, "301")
        self.assertTrue(train.general_seat)
        self.assertFalse(train.special_seat)
        self.assertFalse(train.waiting_available)
        self.assertTrue(train.has_seat(SeatClass.GENERAL))

    def test_search_session_expiry(self):
        srt = self.provider(
            [StubResponse(text="userMap"), StubResponse({"MSG": "로그인 후 이용해주세요"})]
        )
        with self.assertRaises(AuthError):
            srt.search(REQUEST)
        self.assertFalse(srt.logged_in)

    def test_search_empty_result(self):
        srt = self.provider(
            [StubResponse(text="userMap"), StubResponse({"MSG": "조회 결과가 없습니다"})]
        )
        self.assertEqual(srt.search(REQUEST), [])

    def test_rate_limit(self):
        srt = self.provider([StubResponse(text="", status_code=429)])
        with self.assertRaises(RateLimited):
            srt.login()

    def test_reserve_builds_expected_payload(self):
        session = StubSession(
            [
                StubResponse(text="userMap"),
                StubResponse({"outDataSets": {"dsOutput1": [SRT_ROW]}}),
                StubResponse({"strResult": "SUCC", "outDataSets": {"dsOutput1": [{"pnrNo": "1234"}]}}),
            ]
        )
        srt = SRTProvider("a@b.com", "pw", session=session)
        train = srt.search(REQUEST)[0]
        reservation = srt.reserve(train, REQUEST, seat=SeatClass.GENERAL)
        self.assertEqual(reservation.reservation_no, "1234")
        _, payload = session.calls[-1]
        self.assertEqual(payload["trnNo1"], "00301")
        self.assertEqual(payload["jobId"], "1101")
        self.assertEqual(payload["psrmClCd1"], "1")
        self.assertEqual(payload["dptRsStnCd1"], "0551")

    def test_reserve_sold_out(self):
        session = StubSession(
            [
                StubResponse(text="userMap"),
                StubResponse({"outDataSets": {"dsOutput1": [SRT_ROW]}}),
                StubResponse({"strResult": "FAIL", "msgTxt": "잔여좌석이 없습니다"}),
            ]
        )
        srt = SRTProvider("a@b.com", "pw", session=session)
        train = srt.search(REQUEST)[0]
        with self.assertRaises(SoldOut):
            srt.reserve(train, REQUEST)

    def test_reserve_waiting_uses_waiting_job_id(self):
        session = StubSession(
            [
                StubResponse(text="userMap"),
                StubResponse({"outDataSets": {"dsOutput1": [SRT_ROW]}}),
                StubResponse({"strResult": "SUCC", "outDataSets": {"dsOutput1": [{}]}}),
            ]
        )
        srt = SRTProvider("a@b.com", "pw", session=session)
        train = srt.search(REQUEST)[0]
        srt.reserve(train, REQUEST, waiting=True)
        self.assertEqual(session.calls[-1][1]["jobId"], "1900")

    def test_non_json_response(self):
        srt = self.provider([StubResponse(text="userMap"), StubResponse(text="<html>점검중</html>")])
        with self.assertRaises(ProviderError):
            srt.search(REQUEST)


KORAIL_ROW = {
    "h_trn_no": "00101",
    "h_trn_clsf_cd": "00",
    "h_trn_clsf_nm": "KTX",
    "h_trn_gp_cd": "300",
    "h_dpt_dt": "20260101",
    "h_dpt_tm": "080000",
    "h_arv_tm": "103000",
    "h_dpt_rs_stn_cd": "0001",
    "h_arv_rs_stn_cd": "0020",
    "h_dpt_rs_stn_nm": "서울",
    "h_arv_rs_stn_nm": "부산",
    "h_gen_rs_psb_cd": "11",
    "h_srcar_rs_psb_cd": "00",
    "h_run_dt": "20260101",
}


class KorailTest(unittest.TestCase):
    def provider(self, responses):
        return KorailProvider("a@b.com", "pw", session=StubSession(responses))

    def test_login_failure(self):
        korail = self.provider([StubResponse({"strResult": "FAIL", "h_msg_txt": "비밀번호 오류"})])
        with self.assertRaises(AuthError):
            korail.login()

    def test_search_parses_rows(self):
        korail = self.provider(
            [
                StubResponse({"strResult": "SUCC", "Key": "K1"}),
                StubResponse({"strResult": "SUCC", "trn_infos": {"trn_info": [KORAIL_ROW]}}),
            ]
        )
        trains = korail.search(REQUEST)
        self.assertEqual(len(trains), 1)
        self.assertEqual(trains[0].train_name, "KTX")
        self.assertEqual(trains[0].train_no, "101")
        self.assertTrue(trains[0].general_seat)
        self.assertFalse(trains[0].special_seat)

    def test_no_trains_is_empty_list(self):
        korail = self.provider(
            [
                StubResponse({"strResult": "SUCC", "Key": "K1"}),
                StubResponse({"strResult": "FAIL", "h_msg_cd": "IL00", "h_msg_txt": "조회 결과가 없습니다"}),
            ]
        )
        self.assertEqual(korail.search(REQUEST), [])

    def test_session_expiry(self):
        korail = self.provider(
            [
                StubResponse({"strResult": "SUCC", "Key": "K1"}),
                StubResponse({"strResult": "FAIL", "h_msg_cd": "P058", "h_msg_txt": "로그인이 필요합니다"}),
            ]
        )
        with self.assertRaises(AuthError):
            korail.search(REQUEST)

    def test_reserve_payload_and_result(self):
        session = StubSession(
            [
                StubResponse({"strResult": "SUCC", "Key": "K1"}),
                StubResponse({"strResult": "SUCC", "trn_infos": {"trn_info": [KORAIL_ROW]}}),
                StubResponse({"strResult": "SUCC", "h_pnr_no": "9988", "h_ntisu_lmt_dt": "20260101", "h_ntisu_lmt_tm": "081000"}),
            ]
        )
        korail = KorailProvider("a@b.com", "pw", session=session)
        train = korail.search(REQUEST)[0]
        reservation = korail.reserve(train, REQUEST, seat=SeatClass.GENERAL)
        self.assertEqual(reservation.reservation_no, "9988")
        self.assertEqual(reservation.pay_deadline, "20260101081000")
        _, payload = session.calls[-1]
        self.assertEqual(payload["txtTrnNo1"], "00101")
        self.assertEqual(payload["txtJobId"], "1101")
        self.assertEqual(payload["txtPsrmClCd1"], "1")
        self.assertEqual(payload["txtDptRsStnCd1"], "0001")
        self.assertEqual(payload["Key"], "K1")

    def test_invalid_train_type(self):
        with self.assertRaises(ValueError):
            KorailProvider("a", "b", train_type="ktx-santa")


if __name__ == "__main__":
    unittest.main()
