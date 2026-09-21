"""쿠팡 파트너스 서명 생성과 전환 리포트 동기화 테스트."""
import hashlib
import hmac
import os
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from igeosajo import api, coupang, db, sync_coupang, tracking  # noqa: E402

ACCESS_KEY = "test-access-key"
SECRET_KEY = "test-secret-key"
MOMENT = datetime(2026, 9, 21, 3, 4, 5, tzinfo=timezone.utc)


class SignatureTestCase(unittest.TestCase):
    def test_signed_date_format(self):
        self.assertEqual(coupang.signed_date(MOMENT), "260921T030405Z")

    def test_authorization_header_shape(self):
        header = coupang.authorization(
            "GET", coupang.ORDERS_PATH, "startDate=20260920&endDate=20260921",
            ACCESS_KEY, SECRET_KEY, MOMENT,
        )
        message = "260921T030405Z" + "GET" + coupang.ORDERS_PATH + "startDate=20260920&endDate=20260921"
        expected = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(
            header,
            "CEA algorithm=HmacSHA256, access-key=test-access-key, "
            "signed-date=260921T030405Z, signature=" + expected,
        )

    def test_query_is_signed_without_question_mark(self):
        """'?' 가 섞여 들어가면 서명이 틀어진다."""
        header = coupang.authorization("GET", "/p", "a=1", ACCESS_KEY, SECRET_KEY, MOMENT)
        wrong = hmac.new(SECRET_KEY.encode(), b"260921T030405ZGET/p?a=1", hashlib.sha256).hexdigest()
        self.assertNotIn(wrong, header)

    def test_missing_credentials_raises(self):
        with self.assertRaises(coupang.CoupangError):
            coupang.authorization("GET", "/p", "", "", "", MOMENT)

    def test_not_configured_without_env(self):
        for key in ("COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY"):
            os.environ.pop(key, None)
        self.assertFalse(coupang.configured())

    def test_deeplink_falls_back_silently_without_credentials(self):
        for key in ("COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY"):
            os.environ.pop(key, None)
        self.assertIsNone(coupang.deeplink_or_none("https://www.coupang.com/vp/products/1", "tok"))
        # 자격증명이 없어도 링크는 subId 를 붙여서 나가야 한다
        link = tracking.gift_link("https://www.coupang.com/vp/products/1", "tok")
        self.assertIn("subId=tok", link)


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.wishlist = api.create_wishlist(self.conn, {"owner_name": "지은"})
        self.slug = self.wishlist["slug"]
        self.token = self.wishlist["edit_token"]

    def tearDown(self):
        self.conn.close()

    def add_and_reserve(self, title, price, url="https://link.coupang.com/a/x"):
        item = api.add_item(self.conn, self.slug, self.token,
                            {"title": title, "price": price, "url": url})
        reserved = api.reserve_item(self.conn, item["id"])
        return item, reserved["gift_token"]

    def run_sync(self, orders=(), cancels=()):
        return sync_coupang.sync(
            self.conn,
            days=3,
            today=date(2026, 9, 21),
            fetch_orders=lambda s, e: list(orders),
            fetch_cancels=lambda s, e: list(cancels),
            verbose=False,
        )

    def item_row(self, item_id):
        return self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def test_window_covers_requested_days(self):
        summary = self.run_sync()
        self.assertEqual(summary["window"], ["20260919", "20260921"])

    def test_matching_subid_verifies_gift(self):
        item, token = self.add_and_reserve("무선 이어폰", 129000)
        api.gift_item(self.conn, item["id"], {"display": "name", "gifter_name": "민수",
                                              "reserve_token": self._reserve_token(item["id"])})
        summary = self.run_sync(orders=[{"subId": token, "orderId": 555, "gmv": 129000}])
        self.assertEqual(len(summary["verified"]), 1)
        row = self.item_row(item["id"])
        self.assertEqual(row["verification"], db.PAYMENT_VERIFIED)
        self.assertEqual(row["verify_ref"], "555")
        self.assertEqual(api.ranking(self.conn, self.slug)["verified_total"], 129000)

    def test_purchase_without_pressing_the_button_still_counts(self):
        """링크로 사놓고 '결제했어요'를 안 누른 경우에도 리포트로 잡힌다."""
        item, token = self.add_and_reserve("로봇청소기", 450000)
        summary = self.run_sync(orders=[{"subId": token, "orderId": 777}])
        self.assertEqual(len(summary["verified"]), 1)
        row = self.item_row(item["id"])
        self.assertEqual(row["status"], "gifted")
        self.assertEqual(row["verification"], db.PAYMENT_VERIFIED)
        self.assertIsNotNone(row["gifted_at"])
        # 이름을 안 남겼으니 익명으로 집계된다
        ranks = api.ranking(self.conn, self.slug)
        self.assertEqual(ranks["ranks"], [])
        self.assertEqual(ranks["anonymous"]["total"], 450000)

    def test_unknown_subid_is_ignored(self):
        self.add_and_reserve("핸드크림", 18000)
        summary = self.run_sync(orders=[{"subId": "남의채널", "orderId": 1}])
        self.assertEqual(summary["verified"], [])
        self.assertEqual(summary["unmatched"], 1)

    def test_rows_without_subid_are_ignored(self):
        self.add_and_reserve("핸드크림", 18000)
        summary = self.run_sync(orders=[{"orderId": 1}, {"subId": "", "orderId": 2}])
        self.assertEqual(summary["verified"], [])
        self.assertEqual(summary["unmatched"], 2)

    def test_cancel_in_same_window_wins(self):
        item, token = self.add_and_reserve("가방", 80000)
        summary = self.run_sync(
            orders=[{"subId": token, "orderId": 9}],
            cancels=[{"subId": token, "orderId": 9}],
        )
        self.assertEqual(len(summary["verified"]), 1)
        self.assertEqual(len(summary["cancelled"]), 1)
        row = self.item_row(item["id"])
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["verification"], db.CANCELLED)
        self.assertEqual(api.ranking(self.conn, self.slug)["verified_total"], 0)

    def test_already_verified_is_not_reprocessed(self):
        item, token = self.add_and_reserve("도마", 20000)
        self.run_sync(orders=[{"subId": token, "orderId": 3}])
        again = self.run_sync(orders=[{"subId": token, "orderId": 3}])
        self.assertEqual(again["verified"], [])
        self.assertEqual(again["unmatched"], 1)

    def test_recipient_confirmed_gift_can_still_be_payment_verified(self):
        item, token = self.add_and_reserve("의자", 58000)
        api.gift_item(self.conn, item["id"], {"display": "name", "gifter_name": "민수",
                                              "reserve_token": self._reserve_token(item["id"])})
        api.confirm_receipt(self.conn, item["id"], self.token)
        summary = self.run_sync(orders=[{"subId": token, "orderId": 11}])
        self.assertEqual(len(summary["verified"]), 1)
        self.assertEqual(self.item_row(item["id"])["verification"], db.PAYMENT_VERIFIED)

    def _reserve_token(self, item_id):
        return self.item_row(item_id)["reserve_token"]


if __name__ == "__main__":
    unittest.main()


class FakeGatewayTestCase(unittest.TestCase):
    """실제 쿠팡 서버 대신 로컬 가짜 게이트웨이로 요청 구성을 검증한다."""

    @classmethod
    def setUpClass(cls):
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        cls.seen = []
        cls.responses = []

        outer = cls

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.seen.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": _json.loads(raw.decode()) if raw else None,
                })
                status, payload = outer.responses.pop(0) if outer.responses else (200, {"rCode": "0", "data": []})
                body = _json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.original_base = coupang.BASE_URL
        coupang.BASE_URL = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        coupang.BASE_URL = cls.original_base
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        type(self).seen = []
        type(self).responses = []
        os.environ["COUPANG_ACCESS_KEY"] = ACCESS_KEY
        os.environ["COUPANG_SECRET_KEY"] = SECRET_KEY

    def tearDown(self):
        for key in ("COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY"):
            os.environ.pop(key, None)

    def test_orders_report_signs_path_and_query(self):
        type(self).responses = [(200, {"rCode": "0", "data": [{"subId": "tok", "orderId": 1}]})]
        rows = coupang.orders_report("20260920", "20260921")
        self.assertEqual(rows, [{"subId": "tok", "orderId": 1}])

        call = self.seen[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["path"], coupang.ORDERS_PATH + "?startDate=20260920&endDate=20260921&page=0")
        self.assertTrue(call["authorization"].startswith(
            "CEA algorithm=HmacSHA256, access-key=test-access-key, signed-date="))

        # 서명 대상 문자열은 '?' 없이 path + query 다
        stamp = call["authorization"].split("signed-date=")[1].split(",")[0]
        signature = call["authorization"].split("signature=")[1]
        message = stamp + "GET" + coupang.ORDERS_PATH + "startDate=20260920&endDate=20260921&page=0"
        expected = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)

    def test_deeplink_posts_urls_and_subid(self):
        type(self).responses = [(200, {"rCode": "0", "data": [
            {"originalUrl": "https://www.coupang.com/vp/products/1",
             "shortenUrl": "https://link.coupang.com/a/SHORT"}]})]
        link = coupang.deeplink_or_none("https://www.coupang.com/vp/products/1", "gift-token-1")
        self.assertEqual(link, "https://link.coupang.com/a/SHORT")
        call = self.seen[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], coupang.DEEPLINK_PATH)
        self.assertEqual(call["body"], {
            "coupangUrls": ["https://www.coupang.com/vp/products/1"], "subId": "gift-token-1"})

    def test_gift_link_prefers_deeplink(self):
        type(self).responses = [(200, {"rCode": "0", "data": [
            {"shortenUrl": "https://link.coupang.com/a/SHORT"}]})]
        link = tracking.gift_link("https://www.coupang.com/vp/products/1", "tok")
        self.assertEqual(link, "https://link.coupang.com/a/SHORT")

    def test_pagination_follows_full_pages(self):
        full = [{"subId": "t%d" % i} for i in range(coupang.PAGE_SIZE)]
        type(self).responses = [
            (200, {"rCode": "0", "data": full}),
            (200, {"rCode": "0", "data": [{"subId": "last"}]}),
        ]
        rows = coupang.orders_report("20260920", "20260921")
        self.assertEqual(len(rows), coupang.PAGE_SIZE + 1)
        self.assertIn("page=0", self.seen[0]["path"])
        self.assertIn("page=1", self.seen[1]["path"])

    def test_error_rcode_raises(self):
        type(self).responses = [(200, {"rCode": "ERROR", "rMessage": "권한 없음"})]
        with self.assertRaises(coupang.CoupangError):
            coupang.orders_report("20260920", "20260921")

    def test_http_error_raises(self):
        type(self).responses = [(401, {"rCode": "401", "rMessage": "unauthorized"})]
        with self.assertRaises(coupang.CoupangError):
            coupang.orders_report("20260920", "20260921")

    def test_deeplink_failure_falls_back_to_subid_link(self):
        type(self).responses = [(500, {"rCode": "500", "rMessage": "boom"})]
        link = tracking.gift_link("https://www.coupang.com/vp/products/1", "tok")
        self.assertIn("subId=tok", link)
