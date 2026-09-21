"""이거사죠 JSON API: 위시리스트 만들기, 아이템 관리, 선물 예약/완료."""
import re
import secrets

from . import db

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
MAX_TEXT = 500
MAX_PRICE = 100_000_000
DISPLAYS = ("name", "anon")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def already_gifted():
    return ApiError(409, "앗, 이미 선물이 완료된 아이템이에요.")


def _text(payload, key, *, required=False, limit=MAX_TEXT, default=""):
    value = payload.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ApiError(400, f"'{key}' 값은 문자열이어야 해요.")
    value = value.strip()
    if required and not value:
        raise ApiError(400, f"'{key}' 은(는) 꼭 입력해주세요.")
    if len(value) > limit:
        raise ApiError(400, f"'{key}' 은(는) {limit}자 이내로 입력해주세요.")
    return value


def _price(payload, key="price"):
    value = payload.get(key, 0)
    if value in (None, ""):
        return 0
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        price = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "가격은 숫자로 입력해주세요.")
    if price < 0 or price > MAX_PRICE:
        raise ApiError(400, "가격 범위를 확인해주세요.")
    return price


def _url(payload, key="url"):
    value = _text(payload, key, limit=1000)
    if value and not re.match(r"^https?://", value, re.I):
        raise ApiError(400, "링크는 http:// 또는 https:// 로 시작해야 해요.")
    return value


def _gifter_label(row):
    if row["status"] != "gifted":
        return ""
    if row["gifter_display"] == "anon" or not row["gifter_name"]:
        return "익명의 산타"
    return row["gifter_name"]


def item_public(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "price": row["price"],
        "category": row["category"],
        "note": row["note"],
        "image_url": row["image_url"],
        "status": row["status"],
        "gifter_label": _gifter_label(row),
        "gifter_message": row["gifter_message"] if row["status"] == "gifted" else "",
        "gifted_at": row["gifted_at"],
        "created_at": row["created_at"],
    }


def _get_wishlist(conn, slug):
    row = conn.execute("SELECT * FROM wishlists WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ApiError(404, "그런 위시리스트는 없어요.")
    db.expire_reservations(conn, row["id"])
    return row


def _get_item(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ApiError(404, "그런 아이템은 없어요.")
    return row


def _require_owner(conn, wishlist_row, token):
    if not token or not secrets.compare_digest(str(token), wishlist_row["edit_token"]):
        raise ApiError(403, "주인만 수정할 수 있어요.")


# --- 위시리스트 -------------------------------------------------------------

def create_wishlist(conn, payload):
    owner_name = _text(payload, "owner_name", required=True, limit=40)
    intro = _text(payload, "intro", limit=200)
    slug = _text(payload, "slug", limit=32).lower()
    if slug:
        if not SLUG_RE.match(slug):
            raise ApiError(400, "주소는 영문 소문자/숫자/하이픈 3~32자로 만들어주세요.")
        if conn.execute("SELECT 1 FROM wishlists WHERE slug = ?", (slug,)).fetchone():
            raise ApiError(409, "이미 쓰고 있는 주소예요.")
    else:
        for _ in range(10):
            slug = db.new_slug()
            if not conn.execute("SELECT 1 FROM wishlists WHERE slug = ?", (slug,)).fetchone():
                break
        else:
            raise ApiError(500, "주소를 만들지 못했어요. 다시 시도해주세요.")

    edit_token = db.new_token()
    conn.execute(
        "INSERT INTO wishlists (slug, owner_name, intro, edit_token, created_at) VALUES (?,?,?,?,?)",
        (slug, owner_name, intro, edit_token, db.now()),
    )
    conn.commit()
    return {"slug": slug, "owner_name": owner_name, "intro": intro, "edit_token": edit_token}


def get_wishlist(conn, slug, token=None):
    row = _get_wishlist(conn, slug)
    items = conn.execute(
        "SELECT * FROM items WHERE wishlist_id = ? ORDER BY created_at, id", (row["id"],)
    ).fetchall()
    is_owner = bool(token) and secrets.compare_digest(str(token), row["edit_token"])
    return {
        "slug": row["slug"],
        "owner_name": row["owner_name"],
        "intro": row["intro"],
        "created_at": row["created_at"],
        "is_owner": is_owner,
        "items": [item_public(i) for i in items],
    }


def update_wishlist(conn, slug, token, payload):
    row = _get_wishlist(conn, slug)
    _require_owner(conn, row, token)
    owner_name = _text(payload, "owner_name", required=True, limit=40)
    intro = _text(payload, "intro", limit=200)
    conn.execute("UPDATE wishlists SET owner_name = ?, intro = ? WHERE id = ?", (owner_name, intro, row["id"]))
    conn.commit()
    return get_wishlist(conn, slug, token)


# --- 아이템 -----------------------------------------------------------------

def add_item(conn, slug, token, payload):
    row = _get_wishlist(conn, slug)
    _require_owner(conn, row, token)
    values = (
        row["id"],
        _text(payload, "title", required=True, limit=80),
        _url(payload),
        _price(payload),
        _text(payload, "category", limit=20),
        _text(payload, "note", limit=200),
        _url(payload, "image_url"),
        db.now(),
    )
    cur = conn.execute(
        "INSERT INTO items (wishlist_id, title, url, price, category, note, image_url, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        values,
    )
    conn.commit()
    return item_public(_get_item(conn, cur.lastrowid))


def update_item(conn, item_id, token, payload):
    item = _get_item(conn, item_id)
    wishlist = conn.execute("SELECT * FROM wishlists WHERE id = ?", (item["wishlist_id"],)).fetchone()
    _require_owner(conn, wishlist, token)
    if item["status"] == "gifted":
        raise ApiError(409, "이미 선물 받은 아이템은 고칠 수 없어요.")
    conn.execute(
        "UPDATE items SET title=?, url=?, price=?, category=?, note=?, image_url=? WHERE id=?",
        (
            _text(payload, "title", required=True, limit=80),
            _url(payload),
            _price(payload),
            _text(payload, "category", limit=20),
            _text(payload, "note", limit=200),
            _url(payload, "image_url"),
            item_id,
        ),
    )
    conn.commit()
    return item_public(_get_item(conn, item_id))


def delete_item(conn, item_id, token):
    item = _get_item(conn, item_id)
    wishlist = conn.execute("SELECT * FROM wishlists WHERE id = ?", (item["wishlist_id"],)).fetchone()
    _require_owner(conn, wishlist, token)
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    return {"deleted": item_id}


# --- 선물하기 ---------------------------------------------------------------

def reserve_item(conn, item_id):
    """'제가 살게요' — 중복 선물을 막기 위해 잠깐 찜해둔다."""
    item = _get_item(conn, item_id)
    db.expire_reservations(conn, item["wishlist_id"])
    item = _get_item(conn, item_id)
    if item["status"] == "gifted":
        raise already_gifted()
    if item["status"] == "reserved":
        raise ApiError(409, "방금 다른 분이 이 선물을 준비하고 있어요. 잠시 후 다시 확인해주세요.")
    token = db.new_token()
    conn.execute(
        "UPDATE items SET status='reserved', reserve_token=?, reserved_at=? WHERE id=?",
        (token, db.now(), item_id),
    )
    conn.commit()
    return {"item": item_public(_get_item(conn, item_id)), "reserve_token": token,
            "expires_in": db.RESERVE_TTL_SECONDS}


def cancel_reservation(conn, item_id, reserve_token):
    item = _get_item(conn, item_id)
    if item["status"] != "reserved":
        return item_public(item)
    if not reserve_token or not secrets.compare_digest(str(reserve_token), item["reserve_token"]):
        raise ApiError(403, "이 예약을 취소할 권한이 없어요.")
    conn.execute(
        "UPDATE items SET status='open', reserve_token='', reserved_at=NULL WHERE id=?", (item_id,)
    )
    conn.commit()
    return item_public(_get_item(conn, item_id))


def gift_item(conn, item_id, payload):
    """결제까지 끝났다고 알려주면 감사 이모션이 터진다."""
    item = _get_item(conn, item_id)
    if item["status"] == "gifted":
        raise already_gifted()
    if item["status"] == "reserved":
        token = payload.get("reserve_token")
        if not token or not secrets.compare_digest(str(token), item["reserve_token"]):
            raise ApiError(403, "다른 분이 준비 중인 선물이에요.")

    display = payload.get("display", "name")
    if display not in DISPLAYS:
        raise ApiError(400, "이름 공개 방식이 올바르지 않아요.")
    gifter_name = _text(payload, "gifter_name", limit=40)
    if display == "name" and not gifter_name:
        raise ApiError(400, "이름(또는 닉네임)을 적어주세요. 익명으로 하려면 익명을 선택해주세요.")
    message = _text(payload, "message", limit=200)

    conn.execute(
        "UPDATE items SET status='gifted', reserve_token='', reserved_at=NULL,"
        " gifter_name=?, gifter_display=?, gifter_message=?, gifted_at=? WHERE id=?",
        (gifter_name, display, message, db.now(), item_id),
    )
    conn.commit()
    row = _get_item(conn, item_id)
    wishlist = conn.execute("SELECT owner_name FROM wishlists WHERE id = ?", (row["wishlist_id"],)).fetchone()
    return {"item": item_public(row), "owner_name": wishlist["owner_name"]}


def thanks_wall(conn, slug):
    row = _get_wishlist(conn, slug)
    items = conn.execute(
        "SELECT * FROM items WHERE wishlist_id = ? AND status='gifted' ORDER BY gifted_at DESC",
        (row["id"],),
    ).fetchall()
    return {
        "owner_name": row["owner_name"],
        "total_count": len(items),
        "total_price": sum(i["price"] for i in items),
        "thanks": [
            {
                "item_id": i["id"],
                "title": i["title"],
                "price": i["price"],
                "gifter_label": _gifter_label(i),
                "message": i["gifter_message"],
                "gifted_at": i["gifted_at"],
            }
            for i in items
        ],
    }
