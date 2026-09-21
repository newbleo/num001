"""이거사죠 JSON API: 위시리스트 만들기, 아이템 관리, 선물 예약/완료/검증."""
import re
import secrets

from . import db, tracking

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
MAX_TEXT = 500
MAX_PRICE = 100_000_000
DISPLAYS = ("name", "anon")
RANKING_VISIBILITY = ("public", "owner", "off")

# 검증된 누적 선물액에 따른 산타 등급
TIERS = (
    (500_000, "legend", "레전드 산타", "🎅"),
    (100_000, "tree", "트리 산타", "🎄"),
    (30_000, "box", "선물상자 산타", "🎁"),
    (0, "ribbon", "리본 산타", "🎀"),
)


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


def tier_for(total):
    for threshold, key, label, emoji in TIERS:
        if total >= threshold:
            return {"key": key, "label": label, "emoji": emoji}
    return {"key": "ribbon", "label": "리본 산타", "emoji": "🎀"}


def gifter_key_for(name, display):
    """같은 사람이 여러 번 선물했을 때 묶어주기 위한 키. 익명은 묶지 않는다."""
    if display != "name":
        return ""
    return re.sub(r"\s+", "", name).lower()


def _gifter_label(row):
    if row["status"] != "gifted":
        return ""
    if row["gifter_display"] == "anon" or not row["gifter_name"]:
        return "익명의 산타"
    return row["gifter_name"]


def item_public(row):
    verified = row["verification"] in db.VERIFIED_STAGES
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "price": row["price"],
        "category": row["category"],
        "note": row["note"],
        "image_url": row["image_url"],
        "status": row["status"],
        "verification": row["verification"],
        "verified": verified,
        "gifter_label": _gifter_label(row),
        "gifter_message": row["gifter_message"] if row["status"] == "gifted" else "",
        "gifted_at": row["gifted_at"],
        "verified_at": row["verified_at"],
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


def _is_owner(wishlist_row, token):
    return bool(token) and secrets.compare_digest(str(token), wishlist_row["edit_token"])


def _require_owner(conn, wishlist_row, token):
    if not _is_owner(wishlist_row, token):
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
    is_owner = _is_owner(row, token)
    return {
        "slug": row["slug"],
        "owner_name": row["owner_name"],
        "intro": row["intro"],
        "ranking_visibility": row["ranking_visibility"],
        "created_at": row["created_at"],
        "is_owner": is_owner,
        "items": [item_public(i) for i in items],
        "ranking": ranking(conn, slug, token),
    }


def update_wishlist(conn, slug, token, payload):
    row = _get_wishlist(conn, slug)
    _require_owner(conn, row, token)
    owner_name = _text(payload, "owner_name", required=True, limit=40)
    intro = _text(payload, "intro", limit=200)
    visibility = _text(payload, "ranking_visibility", default=row["ranking_visibility"])
    if visibility not in RANKING_VISIBILITY:
        raise ApiError(400, "랭킹 공개 설정이 올바르지 않아요.")
    conn.execute(
        "UPDATE wishlists SET owner_name = ?, intro = ?, ranking_visibility = ? WHERE id = ?",
        (owner_name, intro, visibility, row["id"]),
    )
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
    """'제가 살게요' — 중복 선물을 막고, 전환 추적용 gift_token 을 발급한다."""
    item = _get_item(conn, item_id)
    db.expire_reservations(conn, item["wishlist_id"])
    item = _get_item(conn, item_id)
    if item["status"] == "gifted":
        raise already_gifted()
    if item["status"] == "reserved":
        raise ApiError(409, "방금 다른 분이 이 선물을 준비하고 있어요. 잠시 후 다시 확인해주세요.")

    reserve_token = db.new_token()
    gift_token = db.new_token()
    conn.execute(
        "UPDATE items SET status='reserved', reserve_token=?, reserved_at=?, gift_token=? WHERE id=?",
        (reserve_token, db.now(), gift_token, item_id),
    )
    conn.commit()
    row = _get_item(conn, item_id)
    return {
        "item": item_public(row),
        "reserve_token": reserve_token,
        "gift_token": gift_token,
        # 제휴 링크면 subId 가 붙은 주소, 아니면 원래 주소
        "tracking_url": tracking.tracking_url(row["url"], gift_token),
        "tracked": tracking.is_affiliate_link(row["url"]),
        "expires_in": db.RESERVE_TTL_SECONDS,
    }


def cancel_reservation(conn, item_id, reserve_token):
    item = _get_item(conn, item_id)
    if item["status"] != "reserved":
        return item_public(item)
    if not reserve_token or not secrets.compare_digest(str(reserve_token), item["reserve_token"]):
        raise ApiError(403, "이 예약을 취소할 권한이 없어요.")
    conn.execute(
        "UPDATE items SET status='open', reserve_token='', reserved_at=NULL, gift_token='' WHERE id=?",
        (item_id,),
    )
    conn.commit()
    return item_public(_get_item(conn, item_id))


def gift_item(conn, item_id, payload):
    """사주는 사람이 '결제했어요'라고 알려온 상태.

    여기서는 아직 '확인 중'(claimed)이다. 사주는 사람 화면에서는 바로 축하가 터지지만,
    위시리스트의 공식 기록과 랭킹에는 검증된 뒤에야 올라간다.
    """
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
    gift_token = item["gift_token"] or db.new_token()

    conn.execute(
        "UPDATE items SET status='gifted', reserve_token='', reserved_at=NULL,"
        " gifter_name=?, gifter_display=?, gifter_message=?, gifted_at=?,"
        " gift_token=?, gifter_key=?, verification=? WHERE id=?",
        (
            gifter_name,
            display,
            message,
            db.now(),
            gift_token,
            gifter_key_for(gifter_name, display),
            db.CLAIMED,
            item_id,
        ),
    )
    conn.commit()
    row = _get_item(conn, item_id)
    wishlist = conn.execute("SELECT * FROM wishlists WHERE id = ?", (row["wishlist_id"],)).fetchone()
    return {
        "item": item_public(row),
        "owner_name": wishlist["owner_name"],
        "gift_token": gift_token,
        "tracked": tracking.is_affiliate_link(row["url"]),
    }


# --- 검증 -------------------------------------------------------------------

def confirm_receipt(conn, item_id, token):
    """위시리스트 주인이 '진짜 받았어요'를 눌러 검증하는 경로.

    제휴/PG 연동이 없는 링크에서도 쓸 수 있는 가장 넓은 검증 수단이다.
    """
    item = _get_item(conn, item_id)
    wishlist = conn.execute("SELECT * FROM wishlists WHERE id = ?", (item["wishlist_id"],)).fetchone()
    _require_owner(conn, wishlist, token)
    if item["status"] != "gifted":
        raise ApiError(409, "아직 선물이 등록되지 않은 아이템이에요.")
    if item["verification"] in db.VERIFIED_STAGES:
        return item_public(item)
    conn.execute(
        "UPDATE items SET verification=?, verified_at=? WHERE id=?",
        (db.RECIPIENT_CONFIRMED, db.now(), item_id),
    )
    conn.commit()
    return item_public(_get_item(conn, item_id))


def apply_payment_event(conn, payload):
    """결제 웹훅 / 제휴 전환 리포트를 반영한다.

    payload: {"ref": gift_token, "status": "paid"|"cancelled",
              "external_id": 주문번호, "amount": 결제금액}
    """
    ref = _text(payload, "ref", required=True, limit=64)
    status = _text(payload, "status", required=True, limit=20).lower()
    external_id = _text(payload, "external_id", limit=120)

    row = conn.execute("SELECT * FROM items WHERE gift_token = ?", (ref,)).fetchone()
    if row is None:
        raise ApiError(404, "이 결제와 연결된 선물을 찾지 못했어요.")

    if status in ("paid", "converted", "confirmed"):
        conn.execute(
            "UPDATE items SET status='gifted', verification=?, verified_at=?, verify_ref=? WHERE id=?",
            (db.PAYMENT_VERIFIED, db.now(), external_id, row["id"]),
        )
    elif status in ("cancelled", "canceled", "refunded"):
        # 취소·환불이면 랭킹에서 빼고 아이템을 다시 살 수 있게 되돌린다.
        conn.execute(
            "UPDATE items SET status='open', verification=?, verified_at=NULL, verify_ref=?,"
            " gifter_key='', gift_token='' WHERE id=?",
            (db.CANCELLED, external_id, row["id"]),
        )
    else:
        raise ApiError(400, "알 수 없는 결제 상태예요.")
    conn.commit()
    return item_public(_get_item(conn, row["id"]))


# --- 랭킹 / 감사의 벽 --------------------------------------------------------

def ranking(conn, slug, token=None):
    """검증된 선물만 집계한 산타 랭킹."""
    row = _get_wishlist(conn, slug)
    is_owner = _is_owner(row, token)
    visibility = row["ranking_visibility"]
    visible = visibility == "public" or (visibility == "owner" and is_owner)

    gifted = conn.execute(
        "SELECT * FROM items WHERE wishlist_id = ? AND status='gifted'", (row["id"],)
    ).fetchall()
    verified = [i for i in gifted if i["verification"] in db.VERIFIED_STAGES]
    pending = [i for i in gifted if i["verification"] == db.CLAIMED]

    summary = {
        "visibility": visibility,
        "visible": visible,
        "verified_count": len(verified),
        "verified_total": sum(i["price"] for i in verified),
        "pending_count": len(pending),
        "ranks": [],
        "anonymous": {"count": 0, "total": 0},
    }
    if not visible:
        return summary

    buckets = {}
    for item in verified:
        if not item["gifter_key"]:
            summary["anonymous"]["count"] += 1
            summary["anonymous"]["total"] += item["price"]
            continue
        bucket = buckets.setdefault(
            item["gifter_key"], {"label": item["gifter_name"], "total": 0, "count": 0}
        )
        bucket["total"] += item["price"]
        bucket["count"] += 1

    ordered = sorted(buckets.values(), key=lambda b: (-b["total"], -b["count"], b["label"]))
    summary["ranks"] = [
        {
            "rank": index + 1,
            "label": bucket["label"],
            "total": bucket["total"],
            "count": bucket["count"],
            "tier": tier_for(bucket["total"]),
        }
        for index, bucket in enumerate(ordered)
    ]
    return summary


def thanks_wall(conn, slug):
    row = _get_wishlist(conn, slug)
    items = conn.execute(
        "SELECT * FROM items WHERE wishlist_id = ? AND status='gifted' ORDER BY gifted_at DESC",
        (row["id"],),
    ).fetchall()
    verified = [i for i in items if i["verification"] in db.VERIFIED_STAGES]
    return {
        "owner_name": row["owner_name"],
        "total_count": len(items),
        "total_price": sum(i["price"] for i in items),
        "verified_count": len(verified),
        "verified_price": sum(i["price"] for i in verified),
        "thanks": [
            {
                "item_id": i["id"],
                "title": i["title"],
                "price": i["price"],
                "gifter_label": _gifter_label(i),
                "message": i["gifter_message"],
                "gifted_at": i["gifted_at"],
                "verification": i["verification"],
                "verified": i["verification"] in db.VERIFIED_STAGES,
            }
            for i in items
        ],
    }
