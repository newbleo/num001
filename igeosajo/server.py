"""이거사죠 개발 서버 (표준 라이브러리만 사용)."""
import argparse
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import api, db, tracking

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_BODY = 64 * 1024

ROUTES = [
    ("POST", r"^/api/wishlists$", "create_wishlist"),
    ("GET", r"^/api/wishlists/(?P<slug>[a-z0-9-]{3,32})$", "get_wishlist"),
    ("PATCH", r"^/api/wishlists/(?P<slug>[a-z0-9-]{3,32})$", "update_wishlist"),
    ("GET", r"^/api/wishlists/(?P<slug>[a-z0-9-]{3,32})/thanks$", "thanks_wall"),
    ("GET", r"^/api/wishlists/(?P<slug>[a-z0-9-]{3,32})/ranking$", "ranking"),
    ("POST", r"^/api/wishlists/(?P<slug>[a-z0-9-]{3,32})/items$", "add_item"),
    ("PATCH", r"^/api/items/(?P<item_id>\d+)$", "update_item"),
    ("DELETE", r"^/api/items/(?P<item_id>\d+)$", "delete_item"),
    ("POST", r"^/api/items/(?P<item_id>\d+)/reserve$", "reserve_item"),
    ("POST", r"^/api/items/(?P<item_id>\d+)/cancel$", "cancel_reservation"),
    ("POST", r"^/api/items/(?P<item_id>\d+)/gift$", "gift_item"),
    ("POST", r"^/api/items/(?P<item_id>\d+)/confirm$", "confirm_receipt"),
    ("POST", r"^/api/webhooks/payment$", "payment_webhook"),
]
COMPILED = [(method, re.compile(pattern), handler) for method, pattern, handler in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "igeosajo/0.1"
    protocol_version = "HTTP/1.1"

    # --- 공통 -------------------------------------------------------------
    def log_message(self, fmt, *args):
        if os.environ.get("IGEOSAJO_QUIET"):
            return
        super().log_message(fmt, *args)

    def _send(self, status, body=b"", content_type="application/octet-stream", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_raw(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise api.ApiError(413, "요청이 너무 커요.")
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self, raw=None):
        if raw is None:
            raw = self._read_raw()
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise api.ApiError(400, "JSON 형식이 올바르지 않아요.")
        if not isinstance(payload, dict):
            raise api.ApiError(400, "JSON 객체를 보내주세요.")
        return payload

    def _token(self, query):
        return self.headers.get("X-Edit-Token") or (query.get("token", [None])[0])

    # --- 라우팅 -----------------------------------------------------------
    def _handle(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if not path.startswith("/api/"):
            return self._serve_static(path)

        path_matched = False
        for method, pattern, handler_name in COMPILED:
            match = pattern.match(path)
            if not match:
                continue
            path_matched = True
            if method != self.command:
                continue
            try:
                return self._dispatch(handler_name, match.groupdict(), query)
            except api.ApiError as exc:
                return self._json(exc.status, {"error": exc.message})
            except Exception:  # pragma: no cover - 방어적 처리
                self.log_error("unhandled error on %s", path)
                return self._json(500, {"error": "서버에서 문제가 생겼어요."})
        if path_matched:
            return self._json(405, {"error": "허용되지 않은 메서드예요."})
        self._json(404, {"error": "없는 API 주소예요."})

    def _dispatch(self, name, params, query):
        conn = self.server.conn
        token = self._token(query)
        item_id = int(params["item_id"]) if "item_id" in params else None
        slug = params.get("slug")

        if name == "create_wishlist":
            return self._json(201, api.create_wishlist(conn, self._read_json()))
        if name == "get_wishlist":
            return self._json(200, api.get_wishlist(conn, slug, token))
        if name == "update_wishlist":
            return self._json(200, api.update_wishlist(conn, slug, token, self._read_json()))
        if name == "thanks_wall":
            return self._json(200, api.thanks_wall(conn, slug))
        if name == "ranking":
            return self._json(200, api.ranking(conn, slug, token))
        if name == "add_item":
            return self._json(201, api.add_item(conn, slug, token, self._read_json()))
        if name == "update_item":
            return self._json(200, api.update_item(conn, item_id, token, self._read_json()))
        if name == "delete_item":
            return self._json(200, api.delete_item(conn, item_id, token))
        if name == "reserve_item":
            return self._json(200, api.reserve_item(conn, item_id))
        if name == "cancel_reservation":
            body = self._read_json()
            return self._json(200, api.cancel_reservation(conn, item_id, body.get("reserve_token")))
        if name == "gift_item":
            return self._json(200, api.gift_item(conn, item_id, self._read_json()))
        if name == "confirm_receipt":
            return self._json(200, api.confirm_receipt(conn, item_id, token))
        if name == "payment_webhook":
            raw = self._read_raw()
            if not tracking.verify_signature(raw, self.headers.get("X-Signature")):
                raise api.ApiError(401, "서명이 올바르지 않아요.")
            return self._json(200, api.apply_payment_event(conn, self._read_json(raw)))
        raise api.ApiError(500, "알 수 없는 요청이에요.")

    # --- 정적 파일 --------------------------------------------------------
    def _serve_static(self, path):
        if self.command not in ("GET", "HEAD"):
            return self._json(405, {"error": "허용되지 않은 메서드예요."})
        rel = path.lstrip("/")
        candidate = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not candidate.startswith(STATIC_DIR) or not os.path.isfile(candidate):
            # 해시 라우팅 SPA: 모르는 경로는 전부 index.html
            candidate = os.path.join(STATIC_DIR, "index.html")
        if not os.path.isfile(candidate):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype, _ = mimetypes.guess_type(candidate)
        if ctype and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        with open(candidate, "rb") as fh:
            self._send(200, fh.read(), ctype or "application/octet-stream")

    do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = _handle


def build_server(host="127.0.0.1", port=8000, db_path=None):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.conn = db.connect(db_path)
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="이거사죠 로컬 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="SQLite 파일 경로")
    args = parser.parse_args(argv)

    httpd = build_server(args.host, args.port, args.db)
    print(f"이거사죠 → http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n안녕히 가세요!")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
