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


def tracking_url(url, gift_token):
    """제휴 링크면 subId 를 붙여서 돌려준다. 아니면 원래 링크 그대로."""
    if not url or not gift_token or not is_affiliate_link(url):
        return url
    parts = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() != subid_param().lower()]
    query.append((subid_param(), gift_token))
    return urlunparse(parts._replace(query=urlencode(query)))


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
