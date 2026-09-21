"""쿠팡 파트너스 Open API 클라이언트.

인증은 CEA HMAC-SHA256 방식이다.

    message   = signed-date + METHOD + path + query   (query 는 '?' 없이)
    signature = hex(HMAC_SHA256(secret_key, message))
    header    = CEA algorithm=HmacSHA256, access-key=..., signed-date=..., signature=...

signed-date 는 UTC 기준 `yyMMddTHHmmssZ` 형식이다.

필요한 환경변수:
    COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api-gateway.coupang.com"
DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
ORDERS_PATH = "/v2/providers/affiliate_open_api/apis/openapi/reports/orders"
CANCELS_PATH = "/v2/providers/affiliate_open_api/apis/openapi/reports/cancels"
PAGE_SIZE = 1000  # 쿠팡 리포트 한 페이지 최대 건수
DEFAULT_TIMEOUT = 10
# 딥링크는 '이거 사줄게요'를 누른 사람이 기다리는 동안 불린다.
# 실패해도 subId 링크로 대체되므로 오래 붙잡지 않는다.
DEEPLINK_TIMEOUT = 3


class CoupangError(Exception):
    pass


def credentials():
    return os.environ.get("COUPANG_ACCESS_KEY", ""), os.environ.get("COUPANG_SECRET_KEY", "")


def configured():
    access_key, secret_key = credentials()
    return bool(access_key and secret_key)


def signed_date(moment=None):
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("%y%m%dT%H%M%SZ")


def authorization(method, path, query="", access_key=None, secret_key=None, moment=None):
    if access_key is None or secret_key is None:
        access_key, secret_key = credentials()
    if not access_key or not secret_key:
        raise CoupangError("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 설정되지 않았어요.")
    stamp = signed_date(moment)
    message = stamp + method.upper() + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"CEA algorithm=HmacSHA256, access-key={access_key}, "
        f"signed-date={stamp}, signature={signature}"
    )


def _request(method, path, query="", body=None, timeout=DEFAULT_TIMEOUT):
    url = BASE_URL + path + (("?" + query) if query else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", authorization(method, path, query))
    req.add_header("Content-Type", "application/json;charset=UTF-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise CoupangError(f"쿠팡 API {err.code} 오류: {detail}")
    except (OSError, ValueError) as err:
        # URLError·TimeoutError 는 OSError, JSONDecodeError 는 ValueError 의 하위 클래스다
        raise CoupangError(f"쿠팡 API 호출에 실패했어요: {err}")

    if str(payload.get("rCode", "0")) not in ("0", "200"):
        raise CoupangError(f"쿠팡 API 응답 오류 {payload.get('rCode')}: {payload.get('rMessage')}")
    return payload


def deeplink(urls, sub_id, timeout=DEFAULT_TIMEOUT):
    """쿠팡 상품 URL을 제휴 딥링크로 바꾼다. subId 가 전환 추적 열쇠가 된다."""
    payload = _request(
        "POST", DEEPLINK_PATH,
        body={"coupangUrls": list(urls), "subId": sub_id}, timeout=timeout,
    )
    return payload.get("data") or []


def deeplink_or_none(url, sub_id):
    """실패해도 예외를 올리지 않는다 — 링크는 원본으로 대체하면 되기 때문."""
    if not configured():
        return None
    try:
        links = deeplink([url], sub_id, timeout=DEEPLINK_TIMEOUT)
    except CoupangError:
        return None
    if not links:
        return None
    entry = links[0]
    return entry.get("shortenUrl") or entry.get("landingUrl") or None


def _report(path, start_date, end_date, sub_id=None, max_pages=20):
    """페이지를 끝까지 돌며 리포트 행을 모은다."""
    rows = []
    for page in range(max_pages):
        query = f"startDate={start_date}&endDate={end_date}&page={page}"
        if sub_id:
            query += f"&subId={sub_id}"
        data = _request("GET", path, query).get("data") or []
        rows.extend(data)
        if len(data) < PAGE_SIZE:
            break
        time.sleep(0.2)  # 연속 호출 간격을 조금 둔다
    return rows


def orders_report(start_date, end_date, sub_id=None):
    """결제(주문) 리포트. 날짜는 yyyyMMdd."""
    return _report(ORDERS_PATH, start_date, end_date, sub_id)


def cancels_report(start_date, end_date, sub_id=None):
    """취소 리포트. 날짜는 yyyyMMdd."""
    return _report(CANCELS_PATH, start_date, end_date, sub_id)
