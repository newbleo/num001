"""이거사죠 API 통합 테스트 (표준 라이브러리 unittest)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from igeosajo import db, server  # noqa: E402


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["IGEOSAJO_QUIET"] = "1"
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.httpd = server.build_server("127.0.0.1", 0, os.path.join(cls.tmpdir.name, "test.sqlite3"))
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmpdir.cleanup()

    # --- 헬퍼 ---------------------------------------------------------
    def request(self, method, path, body=None, token=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Edit-Token", token)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode())

    def make_wishlist(self, owner="지은", **kwargs):
        status, payload = self.request("POST", "/api/wishlists", {"owner_name": owner, **kwargs})
        self.assertEqual(status, 201, payload)
        return payload

    def add_item(self, wishlist, title="무선 이어폰", price=39000, url="https://shop.example.com/1"):
        status, payload = self.request(
            "POST",
            f"/api/wishlists/{wishlist['slug']}/items",
            {"title": title, "price": price, "url": url},
            token=wishlist["edit_token"],
        )
        self.assertEqual(status, 201, payload)
        return payload

    # --- 위시리스트 ---------------------------------------------------
    def test_create_wishlist_generates_slug_and_token(self):
        wishlist = self.make_wishlist()
        self.assertTrue(wishlist["slug"])
        self.assertEqual(len(wishlist["edit_token"]), 32)

    def test_custom_slug_must_be_unique(self):
        self.make_wishlist(slug="jieun-birthday")
        status, payload = self.request(
            "POST", "/api/wishlists", {"owner_name": "다른사람", "slug": "jieun-birthday"}
        )
        self.assertEqual(status, 409, payload)

    def test_invalid_slug_rejected(self):
        status, _ = self.request("POST", "/api/wishlists", {"owner_name": "지은", "slug": "한글주소"})
        self.assertEqual(status, 400)

    def test_owner_name_required(self):
        status, _ = self.request("POST", "/api/wishlists", {"owner_name": "  "})
        self.assertEqual(status, 400)

    def test_public_view_hides_edit_token(self):
        wishlist = self.make_wishlist()
        status, payload = self.request("GET", f"/api/wishlists/{wishlist['slug']}")
        self.assertEqual(status, 200)
        self.assertNotIn("edit_token", payload)
        self.assertFalse(payload["is_owner"])

    def test_owner_token_unlocks_owner_view(self):
        wishlist = self.make_wishlist()
        status, payload = self.request(
            "GET", f"/api/wishlists/{wishlist['slug']}?token={wishlist['edit_token']}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["is_owner"])

    # --- 아이템 -------------------------------------------------------
    def test_only_owner_can_add_items(self):
        wishlist = self.make_wishlist()
        status, _ = self.request(
            "POST", f"/api/wishlists/{wishlist['slug']}/items", {"title": "몰래 추가"}, token="wrong-token"
        )
        self.assertEqual(status, 403)

    def test_item_url_must_be_http(self):
        wishlist = self.make_wishlist()
        status, _ = self.request(
            "POST",
            f"/api/wishlists/{wishlist['slug']}/items",
            {"title": "이상한 링크", "url": "javascript:alert(1)"},
            token=wishlist["edit_token"],
        )
        self.assertEqual(status, 400)

    def test_price_accepts_comma_string(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price="129,000")
        self.assertEqual(item["price"], 129000)

    def test_owner_can_delete_item(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        status, _ = self.request("DELETE", f"/api/items/{item['id']}", token=wishlist["edit_token"])
        self.assertEqual(status, 200)
        _, payload = self.request("GET", f"/api/wishlists/{wishlist['slug']}")
        self.assertEqual(payload["items"], [])

    # --- 선물 ---------------------------------------------------------
    def test_reserve_blocks_second_gifter(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        status, first = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertEqual(status, 200)
        self.assertEqual(first["item"]["status"], "reserved")
        status, _ = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertEqual(status, 409)

    def test_cancel_releases_reservation(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        status, payload = self.request(
            "POST", f"/api/items/{item['id']}/cancel", {"reserve_token": reserved["reserve_token"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "open")

    def test_cancel_requires_matching_token(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.request("POST", f"/api/items/{item['id']}/reserve")
        status, _ = self.request("POST", f"/api/items/{item['id']}/cancel", {"reserve_token": "nope"})
        self.assertEqual(status, 403)

    def test_gift_records_name_and_message(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        status, payload = self.request(
            "POST",
            f"/api/items/{item['id']}/gift",
            {
                "reserve_token": reserved["reserve_token"],
                "display": "name",
                "gifter_name": "민수",
                "message": "생일 축하해!",
            },
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["item"]["status"], "gifted")
        self.assertEqual(payload["item"]["gifter_label"], "민수")
        self.assertEqual(payload["item"]["gifter_message"], "생일 축하해!")
        self.assertEqual(payload["owner_name"], "지은")

    def test_anonymous_gift_hides_name(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        _, payload = self.request(
            "POST",
            f"/api/items/{item['id']}/gift",
            {"reserve_token": reserved["reserve_token"], "display": "anon", "gifter_name": "민수"},
        )
        self.assertEqual(payload["item"]["gifter_label"], "익명의 산타")

    def test_named_gift_requires_name(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        status, _ = self.request(
            "POST",
            f"/api/items/{item['id']}/gift",
            {"reserve_token": reserved["reserve_token"], "display": "name", "gifter_name": ""},
        )
        self.assertEqual(status, 400)

    def test_gift_without_reservation_is_allowed(self):
        """링크로 바로 사고 와서 '샀어요' 누르는 경우도 받아준다."""
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        status, _ = self.request(
            "POST", f"/api/items/{item['id']}/gift", {"display": "anon"}
        )
        self.assertEqual(status, 200)

    def test_gift_on_someone_elses_reservation_is_blocked(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.request("POST", f"/api/items/{item['id']}/reserve")
        status, _ = self.request(
            "POST", f"/api/items/{item['id']}/gift", {"display": "anon", "reserve_token": "nope"}
        )
        self.assertEqual(status, 403)

    def test_double_gift_is_blocked(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.request("POST", f"/api/items/{item['id']}/gift", {"display": "anon"})
        status, _ = self.request("POST", f"/api/items/{item['id']}/gift", {"display": "anon"})
        self.assertEqual(status, 409)

    def test_gifted_item_cannot_be_edited(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.request("POST", f"/api/items/{item['id']}/gift", {"display": "anon"})
        status, _ = self.request(
            "PATCH", f"/api/items/{item['id']}", {"title": "바꾸기"}, token=wishlist["edit_token"]
        )
        self.assertEqual(status, 409)

    def test_expired_reservation_is_released(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.request("POST", f"/api/items/{item['id']}/reserve")
        stale = db.now() - db.RESERVE_TTL_SECONDS - 1
        self.httpd.conn.execute("UPDATE items SET reserved_at = ? WHERE id = ?", (stale, item["id"]))
        self.httpd.conn.commit()
        status, payload = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertEqual(status, 200, payload)

    # --- 감사의 벽 ----------------------------------------------------
    def test_thanks_wall_sums_gifts(self):
        wishlist = self.make_wishlist()
        first = self.add_item(wishlist, title="가방", price=50000)
        second = self.add_item(wishlist, title="나무 도마", price=20000)
        self.request("POST", f"/api/items/{first['id']}/gift", {"display": "name", "gifter_name": "민수"})
        self.request("POST", f"/api/items/{second['id']}/gift", {"display": "anon"})
        status, payload = self.request("GET", f"/api/wishlists/{wishlist['slug']}/thanks")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["total_price"], 70000)

    # --- 기타 ---------------------------------------------------------
    def test_unknown_api_path_returns_404(self):
        status, _ = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)

    def test_missing_wishlist_returns_404(self):
        status, _ = self.request("GET", "/api/wishlists/no-such-list")
        self.assertEqual(status, 404)

    def test_static_index_is_served(self):
        with urllib.request.urlopen(self.base + "/") as res:
            body = res.read().decode()
        self.assertIn("이거사죠", body)

    def test_spa_fallback_serves_index(self):
        with urllib.request.urlopen(self.base + "/w/anything") as res:
            body = res.read().decode()
        self.assertIn("이거사죠", body)

    def test_static_path_traversal_is_blocked(self):
        with urllib.request.urlopen(self.base + "/../igeosajo/server.py") as res:
            body = res.read().decode()
        self.assertNotIn("ThreadingHTTPServer", body)


if __name__ == "__main__":
    unittest.main()
