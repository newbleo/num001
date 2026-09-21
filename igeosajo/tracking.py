"""상품 링크에 전환 추적용 subId 를 붙이고, 외부 결제/제휴 콜백 서명을 검증한다.

실제 전환을 확인하는 방법은 크게 둘이다.

1. 제휴 링크(쿠팡 파트너스 등) — 링크에 subId 를 심어두면 나중에 리포트에서
   "이 subId 로 결제가 일어났다"를 확인할 수 있다. 다만 리포트는 실시간이 아니라
   하루 정도 지연된다. 따라서 축하는 즉시, 랭킹 반영은 확인된 뒤가 된다.
2. 직접 결제(PG) — 결제 성공 웹훅이 즉시 오므로 그 자리에서 검증된다.

둘 다 "이 결제가 어느 선물이었는지" 되찾을 열쇠가 필요한데, 그 열쇠가 gift_token 이다.
"""
import hashlib
import hmac
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DEFAULT_AFFILIATE_HOSTS = "link.coupang.com,coupang.com"

# 쿠팡 상품 링크에서 살려둬야 하는 쿼리 (옵션·상품 식별에 필요)
COUPANG_KEEP = {"itemid", "vendoritemid", "q"}
# 어느 쇼핑몰이든 지워도 되는 추적용 쿼리
JUNK_PREFIXES = ("utm_", "spec", "addtag", "ctag", "lptag", "pricepattern", "clickbeacon")
JUNK_PARAMS = {
    "searchid", "rank", "isaddedcart", "itemscount", "searchrank", "traceid",
    "requestid", "sourcetype", "clickeventid", "korereferrer", "src", "spec",
    "fbclid", "gclid", "wref", "wtime",
}


def _env(name, default=""):
    return os.environ.get(name, default)


def affiliate_hosts():
    raw = _env("IGEOSAJO_AFFILIATE_HOSTS", DEFAULT_AFFILIATE_HOSTS)
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


def subid_param():
    return _env("IGEOSAJO_SUBID_PARAM", "subId")


def webhook_secret():
    return _env("IGEOSAJO_WEBHOOK_SECRET", "")


def is_affiliate_link(url):
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == known or host.endswith("." + known) for known in affiliate_hosts())


def link_kind(url):
    """붙여넣은 링크가 어떤 종류인지 알아본다."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not is_affiliate_link(url):
        return "other"
    if host == "link.coupang.com":
        return "shortlink"
    if "/vp/products/" in parts.path:
        return "product"
    if "/np/search" in parts.path or parts.path.startswith("/search"):
        return "search"
    return "coupang"


def clean_url(url):
    """검색하다 복사한 링크에 붙어 오는 추적 쿼리를 털어낸다.

    쿠팡 상품 링크는 itemId·vendorItemId 가 옵션을 가리키므로 남기고,
    나머지(searchId, rank, isAddedCart …)는 지운다.
    """
    if not url:
        return url
    parts = urlparse(url)
    if not parts.scheme or not parts.hostname:
        return url

    coupang = is_affiliate_link(url)
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low = key.lower()
        if coupang:
            if low in COUPANG_KEEP:
                kept.append((key, value))
            continue
        if low in JUNK_PARAMS or low.startswith(JUNK_PREFIXES):
            continue
        kept.append((key, value))
    return urlunparse(parts._replace(query=urlencode(kept), fragment=""))


def inspect(url):
    """링크를 등록하기 전에 어떤 링크인지 알려준다 (네트워크 호출 없음)."""
    cleaned = clean_url(url)
    kind = link_kind(cleaned)
    trackable = is_affiliate_link(cleaned)
    labels = {
        "product": "쿠팡 상품 링크예요. 결제가 자동으로 확인됩니다.",
        "shortlink": "쿠팡 파트너스 링크예요. 결제가 자동으로 확인됩니다.",
        "search": "쿠팡 검색 링크예요. 상품 하나를 골라 그 페이지 주소를 넣으면 더 정확해요.",
        "coupang": "쿠팡 링크예요. 결제가 자동으로 확인됩니다.",
        "other": "이 쇼핑몰은 자동 확인이 안 돼요. 받는 분이 확인해주면 랭킹에 올라갑니다.",
    }
    return {
        "url": cleaned,
        "changed": cleaned != (url or ""),
        "kind": kind,
        "trackable": trackable,
        "label": labels.get(kind, labels["other"]),
    }


def tracking_url(url, gift_token):
    """제휴 링크면 subId 를 붙여서 돌려준다. 아니면 원래 링크 그대로."""
    if not url or not gift_token or not is_affiliate_link(url):
        return url
    parts = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() != subid_param().lower()]
    query.append((subid_param(), gift_token))
    return urlunparse(parts._replace(query=urlencode(query)))


def gift_link(url, gift_token):
    """사주는 사람에게 건넬 최종 링크.

    쿠팡 파트너스 자격증명이 있으면 딥링크 API로 제대로 된 제휴 링크를 만들고,
    없거나 실패하면 subId 만 붙인 원래 링크로 조용히 물러난다.
    """
    if not url or not gift_token:
        return url
    if not is_affiliate_link(url):
        return url
    from . import coupang  # 순환 import 를 피하려고 여기서 불러온다

    deep = coupang.deeplink_or_none(url, gift_token)
    return deep or tracking_url(url, gift_token)


def verify_signature(raw_body, signature):
    """웹훅 본문 HMAC-SHA256 서명을 확인한다.

    비밀키가 설정되지 않았으면 웹훅을 아예 받지 않는다 (열어두면 랭킹을 조작당한다).
    """
    secret = webhook_secret()
    if not secret:
        return False
    if not signature:
        return False
    signature = signature.strip()
    if signature.lower().startswith("sha256="):
        signature = signature[7:]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())
