"""검증 단계와 산타 랭킹 테스트.

핵심 규칙: 자진 신고(claimed)만으로는 절대 랭킹에 오르지 않는다.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from igeosajo import db, server, tracking  # noqa: E402

SECRET = "test-webhook-secret"


class VerificationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["IGEOSAJO_QUIET"] = "1"
        os.environ["IGEOSAJO_WEBHOOK_SECRET"] = SECRET
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.httpd = server.build_server("127.0.0.1", 0, os.path.join(cls.tmpdir.name, "verify.sqlite3"))
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmpdir.cleanup()
        os.environ.pop("IGEOSAJO_WEBHOOK_SECRET", None)

    # --- 헬퍼 ---------------------------------------------------------
    def request(self, method, path, body=None, token=None, signature=None):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Edit-Token", token)
        if signature is not None:
            req.add_header("X-Signature", signature)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode())

    def signed(self, path, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return self.request("POST", path, body, signature=signature)

    def make_wishlist(self, owner="지은"):
        _, payload = self.request("POST", "/api/wishlists", {"owner_name": owner})
        return payload

    def add_item(self, wishlist, title="무선 이어폰", price=39000, url="https://shop.example.com/1"):
        _, payload = self.request(
            "POST",
            f"/api/wishlists/{wishlist['slug']}/items",
            {"title": title, "price": price, "url": url},
            token=wishlist["edit_token"],
        )
        return payload

    def gift(self, item_id, name="민수", display="name"):
        _, reserved = self.request("POST", f"/api/items/{item_id}/reserve")
        status, payload = self.request(
            "POST",
            f"/api/items/{item_id}/gift",
            {"reserve_token": reserved["reserve_token"], "display": display, "gifter_name": name},
        )
        self.assertEqual(status, 200, payload)
        payload["reserved"] = reserved
        return payload

    def ranking(self, wishlist, token=None):
        _, payload = self.request(
            "GET",
            f"/api/wishlists/{wishlist['slug']}/ranking" + (f"?token={token}" if token else ""),
        )
        return payload

    # --- 자진 신고는 미검증 -------------------------------------------
    def test_gift_starts_unverified(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        result = self.gift(item["id"])
        self.assertEqual(result["item"]["verification"], db.CLAIMED)
        self.assertFalse(result["item"]["verified"])

    def test_unverified_gift_is_not_ranked(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=500000)
        self.gift(item["id"])
        ranks = self.ranking(wishlist)
        self.assertEqual(ranks["ranks"], [])
        self.assertEqual(ranks["verified_total"], 0)
        self.assertEqual(ranks["pending_count"], 1)

    def test_unverified_gift_still_blocks_duplicate_purchase(self):
        """검증 전이라도 아이템은 잠겨 있어야 중복 선물이 안 난다."""
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.gift(item["id"])
        status, _ = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertEqual(status, 409)

    # --- 수령자 확인 ---------------------------------------------------
    def test_owner_confirmation_verifies_gift(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=129000)
        self.gift(item["id"])
        status, payload = self.request(
            "POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"]
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["verification"], db.RECIPIENT_CONFIRMED)
        self.assertTrue(payload["verified"])

        ranks = self.ranking(wishlist)
        self.assertEqual(ranks["verified_total"], 129000)
        self.assertEqual(ranks["ranks"][0]["label"], "민수")
        self.assertEqual(ranks["ranks"][0]["rank"], 1)
        self.assertEqual(ranks["pending_count"], 0)

    def test_stranger_cannot_confirm(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        self.gift(item["id"])
        status, _ = self.request("POST", f"/api/items/{item['id']}/confirm", token="wrong-token")
        self.assertEqual(status, 403)

    def test_cannot_confirm_item_nobody_gifted(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        status, _ = self.request(
            "POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"]
        )
        self.assertEqual(status, 409)

    # --- 결제 웹훅 -----------------------------------------------------
    def test_webhook_requires_signature(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        result = self.gift(item["id"])
        status, _ = self.request(
            "POST", "/api/webhooks/payment",
            {"ref": result["gift_token"], "status": "paid"},
        )
        self.assertEqual(status, 401)

    def test_webhook_rejects_bad_signature(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        result = self.gift(item["id"])
        status, _ = self.request(
            "POST", "/api/webhooks/payment",
            {"ref": result["gift_token"], "status": "paid"},
            signature="deadbeef",
        )
        self.assertEqual(status, 401)

    def test_webhook_verifies_and_ranks(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=450000)
        result = self.gift(item["id"], name="민수")
        status, payload = self.signed(
            "/api/webhooks/payment",
            {"ref": result["gift_token"], "status": "paid", "external_id": "ORDER-1"},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["verification"], db.PAYMENT_VERIFIED)

        ranks = self.ranking(wishlist)
        self.assertEqual(ranks["verified_total"], 450000)
        self.assertEqual(ranks["ranks"][0]["tier"]["key"], "tree")

    def test_webhook_cancel_reopens_item(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=50000)
        result = self.gift(item["id"])
        self.signed("/api/webhooks/payment", {"ref": result["gift_token"], "status": "paid"})
        status, payload = self.signed(
            "/api/webhooks/payment", {"ref": result["gift_token"], "status": "refunded"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "open")
        self.assertEqual(self.ranking(wishlist)["verified_total"], 0)

    def test_webhook_unknown_reference(self):
        status, _ = self.signed("/api/webhooks/payment", {"ref": "없는토큰", "status": "paid"})
        self.assertEqual(status, 404)

    def test_webhook_unknown_status(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        result = self.gift(item["id"])
        status, _ = self.signed(
            "/api/webhooks/payment", {"ref": result["gift_token"], "status": "잘모르겠음"}
        )
        self.assertEqual(status, 400)

    # --- 랭킹 집계 -----------------------------------------------------
    def test_same_gifter_accumulates_across_items(self):
        wishlist = self.make_wishlist()
        first = self.add_item(wishlist, title="가방", price=80000)
        second = self.add_item(wishlist, title="도마", price=40000)
        for item in (first, second):
            self.gift(item["id"], name="민 수")  # 띄어쓰기가 달라도 같은 사람
            self.request("POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"])
        ranks = self.ranking(wishlist)["ranks"]
        self.assertEqual(len(ranks), 1)
        self.assertEqual(ranks[0]["total"], 120000)
        self.assertEqual(ranks[0]["count"], 2)
        self.assertEqual(ranks[0]["tier"]["key"], "tree")

    def test_ranking_is_ordered_by_total_desc(self):
        wishlist = self.make_wishlist()
        small = self.add_item(wishlist, title="책", price=16800)
        big = self.add_item(wishlist, title="청소기", price=450000)
        self.gift(small["id"], name="영희")
        self.gift(big["id"], name="철수")
        for item in (small, big):
            self.request("POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"])
        ranks = self.ranking(wishlist)["ranks"]
        self.assertEqual([r["label"] for r in ranks], ["철수", "영희"])
        self.assertEqual([r["rank"] for r in ranks], [1, 2])

    def test_anonymous_gifts_are_pooled_not_ranked(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=70000)
        self.gift(item["id"], name="", display="anon")
        self.request("POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"])
        ranks = self.ranking(wishlist)
        self.assertEqual(ranks["ranks"], [])
        self.assertEqual(ranks["anonymous"], {"count": 1, "total": 70000})
        self.assertEqual(ranks["verified_total"], 70000)

    def test_tier_thresholds(self):
        from igeosajo.api import tier_for
        self.assertEqual(tier_for(0)["key"], "ribbon")
        self.assertEqual(tier_for(29999)["key"], "ribbon")
        self.assertEqual(tier_for(30000)["key"], "box")
        self.assertEqual(tier_for(99999)["key"], "box")
        self.assertEqual(tier_for(100000)["key"], "tree")
        self.assertEqual(tier_for(499999)["key"], "tree")
        self.assertEqual(tier_for(500000)["key"], "legend")

    # --- 랭킹 공개 범위 ------------------------------------------------
    def test_owner_only_ranking_is_hidden_from_visitors(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, price=50000)
        self.gift(item["id"])
        self.request("POST", f"/api/items/{item['id']}/confirm", token=wishlist["edit_token"])
        self.request(
            "PATCH",
            f"/api/wishlists/{wishlist['slug']}",
            {"owner_name": "지은", "ranking_visibility": "owner"},
            token=wishlist["edit_token"],
        )
        public = self.ranking(wishlist)
        self.assertFalse(public["visible"])
        self.assertEqual(public["ranks"], [])

        owner = self.ranking(wishlist, token=wishlist["edit_token"])
        self.assertTrue(owner["visible"])
        self.assertEqual(len(owner["ranks"]), 1)

    def test_invalid_ranking_visibility_rejected(self):
        wishlist = self.make_wishlist()
        status, _ = self.request(
            "PATCH",
            f"/api/wishlists/{wishlist['slug']}",
            {"owner_name": "지은", "ranking_visibility": "마음대로"},
            token=wishlist["edit_token"],
        )
        self.assertEqual(status, 400)

    # --- 추적 링크 -----------------------------------------------------
    def test_reserve_returns_gift_token(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertEqual(len(reserved["gift_token"]), 32)
        self.assertFalse(reserved["tracked"])
        self.assertEqual(reserved["tracking_url"], "https://shop.example.com/1")

    def test_affiliate_link_gets_subid(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist, url="https://link.coupang.com/a/abcde")
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.assertTrue(reserved["tracked"])
        self.assertIn("subId=" + reserved["gift_token"], reserved["tracking_url"])

    def test_cancelling_reservation_clears_gift_token(self):
        wishlist = self.make_wishlist()
        item = self.add_item(wishlist)
        _, reserved = self.request("POST", f"/api/items/{item['id']}/reserve")
        self.request(
            "POST", f"/api/items/{item['id']}/cancel", {"reserve_token": reserved["reserve_token"]}
        )
        status, _ = self.signed(
            "/api/webhooks/payment", {"ref": reserved["gift_token"], "status": "paid"}
        )
        self.assertEqual(status, 404)


class TrackingUnitTestCase(unittest.TestCase):
    def test_non_affiliate_link_untouched(self):
        url = "https://shop.example.com/item?a=1"
        self.assertEqual(tracking.tracking_url(url, "tok"), url)

    def test_subid_replaces_existing_value(self):
        url = "https://link.coupang.com/a/x?subId=old&q=1"
        result = tracking.tracking_url(url, "new")
        self.assertIn("subId=new", result)
        self.assertNotIn("subId=old", result)
        self.assertIn("q=1", result)

    def test_subdomain_of_affiliate_host_matches(self):
        self.assertTrue(tracking.is_affiliate_link("https://www.coupang.com/vp/products/1"))

    def test_lookalike_host_does_not_match(self):
        self.assertFalse(tracking.is_affiliate_link("https://coupang.com.evil.example/x"))

    def test_signature_rejected_without_secret(self):
        os.environ.pop("IGEOSAJO_WEBHOOK_SECRET", None)
        body = b'{"ref":"x"}'
        signature = hmac.new(b"", body, hashlib.sha256).hexdigest()
        self.assertFalse(tracking.verify_signature(body, signature))

    def test_signature_accepts_sha256_prefix(self):
        os.environ["IGEOSAJO_WEBHOOK_SECRET"] = SECRET
        try:
            body = b'{"ref":"x"}'
            digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            self.assertTrue(tracking.verify_signature(body, "sha256=" + digest))
            self.assertTrue(tracking.verify_signature(body, digest.upper()))
            self.assertFalse(tracking.verify_signature(body, digest[:-1] + "0"))
        finally:
            os.environ.pop("IGEOSAJO_WEBHOOK_SECRET", None)


if __name__ == "__main__":
    unittest.main()
