"""쿠팡 파트너스 전환 리포트를 읽어 선물을 검증하는 배치.

쿠팡 리포트는 실시간이 아니라 전일 데이터가 익일 12:30 이후에 나온다.
그래서 하루 한 번 이상 돌려주면 된다.

    python3 -m igeosajo.sync_coupang --days 3

찾는 열쇠는 subId 다. 찜하기(예약) 때 발급한 gift_token 을 상품 링크의 subId 로
심어 보냈기 때문에, 리포트에 같은 subId 가 보이면 그 선물이 실제로 결제된 것이다.
"""
import argparse
from datetime import date, timedelta

from . import api, coupang, db


def _tokens_awaiting_payment(conn):
    """아직 결제 검증이 안 된 gift_token 들."""
    rows = conn.execute(
        "SELECT gift_token FROM items WHERE gift_token != '' AND verification != ?",
        (db.PAYMENT_VERIFIED,),
    )
    return {row["gift_token"] for row in rows}


def _known_tokens(conn):
    rows = conn.execute("SELECT gift_token FROM items WHERE gift_token != ''")
    return {row["gift_token"] for row in rows}


def sync(conn, days=3, today=None, fetch_orders=None, fetch_cancels=None, verbose=True):
    """리포트를 읽어 검증/취소를 반영하고 요약을 돌려준다."""
    fetch_orders = fetch_orders or coupang.orders_report
    fetch_cancels = fetch_cancels or coupang.cancels_report

    end = today or date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    start_date, end_date = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    summary = {
        "window": [start_date, end_date],
        "orders_seen": 0,
        "cancels_seen": 0,
        "verified": [],
        "cancelled": [],
        "unmatched": 0,
    }

    awaiting = _tokens_awaiting_payment(conn)
    for row in fetch_orders(start_date, end_date):
        summary["orders_seen"] += 1
        sub_id = str(row.get("subId") or "").strip()
        if not sub_id or sub_id not in awaiting:
            summary["unmatched"] += 1
            continue
        try:
            item = api.apply_payment_event(conn, {
                "ref": sub_id,
                "status": "paid",
                "external_id": str(row.get("orderId") or ""),
            })
        except api.ApiError:
            summary["unmatched"] += 1
            continue
        awaiting.discard(sub_id)
        summary["verified"].append({"item_id": item["id"], "title": item["title"],
                                    "order_id": str(row.get("orderId") or "")})
        if verbose:
            print(f"  ✅ 검증: {item['title']} (주문 {row.get('orderId')})")

    # 취소·환불은 주문보다 나중에 처리해야 같은 창에서 들어온 취소가 최종 상태가 된다.
    known = _known_tokens(conn)
    for row in fetch_cancels(start_date, end_date):
        summary["cancels_seen"] += 1
        sub_id = str(row.get("subId") or "").strip()
        if not sub_id or sub_id not in known:
            continue
        try:
            item = api.apply_payment_event(conn, {
                "ref": sub_id,
                "status": "cancelled",
                "external_id": str(row.get("orderId") or ""),
            })
        except api.ApiError:
            continue
        summary["cancelled"].append({"item_id": item["id"], "title": item["title"]})
        if verbose:
            print(f"  ↩️  취소: {item['title']} (주문 {row.get('orderId')})")

    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="쿠팡 파트너스 전환 리포트로 선물 검증하기")
    parser.add_argument("--days", type=int, default=3, help="오늘 포함 며칠치를 볼지 (기본 3)")
    parser.add_argument("--db", default=None, help="SQLite 파일 경로")
    args = parser.parse_args(argv)

    if not coupang.configured():
        parser.error("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 를 먼저 설정해주세요.")

    conn = db.connect(args.db)
    try:
        print("쿠팡 리포트를 읽는 중…")
        summary = sync(conn, days=args.days)
    except coupang.CoupangError as err:
        parser.error(str(err))
    finally:
        conn.close()

    print(
        f"[{summary['window'][0]}~{summary['window'][1]}] "
        f"주문 {summary['orders_seen']}건 / 취소 {summary['cancels_seen']}건 읽음 → "
        f"검증 {len(summary['verified'])}건, 취소 반영 {len(summary['cancelled'])}건"
    )


if __name__ == "__main__":
    main()
