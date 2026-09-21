# 🎁 이거사죠

> “뭐 갖고 싶어?” 라고 물어볼 때, 링크 하나만 딱.

생일선물 뭐 갖고 싶냐고 물어보면 막상 말하기가 어렵습니다. 얼마짜리를 말해야 할지도 모르겠고,
콕 집어 말하기도 민망하고요. **이거사죠**는 그 민망함을 없애는 개인 위시리스트 링크입니다.

소액부터 고액까지 갖고 싶은 걸 쭉 적어두고 링크를 건네면, 사주는 사람은 자기 예산에 맞춰
마음 편히 고르면 됩니다. 결제를 마치고 “샀어요”를 누르면 페이지에서 감사 인사가 터지고,
원하면 자기 이름(실명/닉네임/익명)을 그 아이템에 남길 수 있습니다.

<sub>(저장소 <code>num001</code> 의 첫 프로젝트입니다.)</sub>

## 무엇이 되나요

| 기능 | 설명 |
| --- | --- |
| 위시리스트 만들기 | 이름만 넣으면 `/#/w/짧은주소` 링크가 바로 생깁니다. 주소는 직접 정할 수도 있어요. |
| 아이템 등록 | 상품명, 가격, 상품 링크, 카테고리, 한마디를 적습니다. |
| 정렬 | 가격 낮은순 / 높은순 / **가나다순**(한글 정렬) / 최근 추가순 |
| 가격대 필터 | 소액(3만 원 미만) · 중액(3~10만 원) · 고액(10만 원 이상) |
| 상품 링크 연결 | 아이템의 “상품 보기”를 누르면 실제 판매 페이지로 바로 이동합니다. |
| 중복 선물 방지 | “이거 사줄게요”를 누르면 30분 동안 그 아이템이 “준비 중”으로 잠깁니다. |
| 감사 이모션 | 결제 완료를 누르면 컨페티 + “너무 감사합니다!!” 축하 화면이 뜹니다. |
| 이름 남기기 | 실명·닉네임으로 남기거나 “익명의 산타”로 숨길 수 있습니다. 한마디도 함께요. |
| 감사의 벽 | 누가 무엇을 선물해 줬는지 위시리스트 아래에 모여서 보입니다. |
| **산타 랭킹** | 누적 금액순 랭킹과 등급(🎀🎁🎄🎅). **검증된 선물만 집계합니다.** |

## 결제를 어떻게 확인하나요 (핵심)

랭킹이 붙는 순간 “안 샀는데 샀다고 누르기”의 동기가 생깁니다. 그래서 이 앱은
**자진 신고를 랭킹에 반영하지 않습니다.** 선물은 네 단계 중 하나를 갖습니다.

| 단계 | 뜻 | 랭킹 반영 |
| --- | --- | --- |
| `claimed` | 사주는 사람이 “결제했어요”를 누른 상태 | ❌ (확인 중) |
| `recipient_confirmed` | 위시리스트 주인이 “받았어요”로 확인 | ✅ |
| `payment_verified` | 결제/제휴 전환 콜백으로 확인 | ✅ |
| `cancelled` | 결제 취소·환불 — 아이템이 다시 열립니다 | ❌ |

그래서 축하는 **두 번** 터집니다.

1. **즉시** — “결제했어요”를 누른 사람 화면에서 바로 컨페티가 터집니다. 거짓 신고를 해도
   본인 화면에서만 터지고 공개 기록에는 “확인 중 ⏳”으로만 남습니다.
2. **검증되면** — 위시리스트에 “선물 완료 🎁”로 올라가고 산타 랭킹에 반영됩니다.

검증 경로는 두 갈래이고 둘 다 `gift_token` 하나로 이어집니다.

- **제휴 링크(쿠팡 파트너스 등)** — 예약 시 발급된 `gift_token` 을 상품 링크에 `subId` 로
  심어 보냅니다. 나중에 전환 리포트에서 같은 `subId` 를 찾아 웹훅으로 쏘면 검증됩니다.
  다만 [쿠팡 파트너스 리포트 API는 전일 데이터를 익일 12:30 이후에 제공](https://adpopcornssp.gitbook.io/ssp-sdk/extra-guide/coupang/api/api)하므로
  **실시간이 아닙니다.**
- **직접 결제(PG)** — 결제 성공 웹훅이 즉시 오므로 그 자리에서 검증됩니다. 즉시성과 검증을
  동시에 가지려면 이 길뿐입니다.
- **수령자 확인** — 제휴도 PG도 없는 아무 링크에나 쓸 수 있는 폭넓은 대비책입니다.

### 웹훅 붙이기

```bash
export IGEOSAJO_WEBHOOK_SECRET=여기에_공유비밀키    # 없으면 웹훅을 아예 받지 않습니다
export IGEOSAJO_AFFILIATE_HOSTS=link.coupang.com,coupang.com
export IGEOSAJO_SUBID_PARAM=subId
```

```bash
BODY='{"ref":"<gift_token>","status":"paid","external_id":"ORDER-1"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$IGEOSAJO_WEBHOOK_SECRET" -hex | awk '{print $2}')

curl -X POST http://localhost:8000/api/webhooks/payment \
     -H "Content-Type: application/json" \
     -H "X-Signature: $SIG" \
     --data-binary "$BODY"
```

`status` 는 `paid`/`converted`/`confirmed` 면 검증, `cancelled`/`refunded` 면 무효 처리됩니다.

서명(`X-Signature`, HMAC-SHA256)이 맞지 않으면 401 입니다. 비밀키를 설정하지 않으면
모든 웹훅이 거부됩니다 — 열어두면 랭킹을 조작당하기 때문입니다.

### 랭킹이 민망함을 되살리지 않도록

금액 랭킹은 원래 취지(“민망하지 않게”)와 충돌할 수 있습니다. 그래서 위시리스트마다
공개 범위를 고를 수 있습니다: **모두에게 공개 / 나만 보기 / 랭킹 끄기**.

주인과 방문자는 **수정 토큰**으로 구분합니다. 위시리스트를 만든 브라우저에만 토큰이 저장되고,
공유 링크에는 토큰이 들어가지 않으므로 링크를 받은 사람은 아이템을 고치거나 지울 수 없습니다.

## 실행하기

설치할 패키지가 없습니다. Python 3.9 이상이면 바로 돌아갑니다.

```bash
python3 -m igeosajo                      # http://127.0.0.1:8000
python3 -m igeosajo --port 3000          # 포트 바꾸기
python3 -m igeosajo --db /tmp/wish.db    # 데이터베이스 파일 지정
```

데이터는 SQLite 파일 하나(`igeosajo.sqlite3`, 환경변수 `IGEOSAJO_DB`로 변경 가능)에 저장됩니다.

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

## 구조

```
igeosajo/
├── __main__.py        # python -m igeosajo 진입점
├── server.py          # 라우팅 + 정적 파일 (표준 라이브러리 HTTP 서버)
├── api.py             # 위시리스트/아이템/선물/검증/랭킹 로직과 입력 검증
├── tracking.py        # subId 링크 태깅, 웹훅 HMAC 서명 검증
├── db.py              # SQLite 스키마, 마이그레이션, 예약 만료 처리
└── static/
    ├── index.html
    ├── styles.css     # 라이트/다크 모드, 모바일 대응
    └── app.js         # 해시 라우팅 SPA (의존성 없음)
tests/
├── test_api.py          # API 통합 테스트
└── test_verification.py # 검증 단계·랭킹·웹훅 테스트
```

## API 요약

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| `POST` | `/api/wishlists` | 위시리스트 생성 → `slug`, `edit_token` 반환 |
| `GET` | `/api/wishlists/{slug}` | 공개 조회 (`?token=`을 주면 주인 모드) |
| `PATCH` | `/api/wishlists/{slug}` | 이름·소개 수정 (주인) |
| `GET` | `/api/wishlists/{slug}/thanks` | 감사의 벽 + 합계 |
| `GET` | `/api/wishlists/{slug}/ranking` | 산타 랭킹 (검증된 선물만) |
| `POST` | `/api/wishlists/{slug}/items` | 아이템 추가 (주인) |
| `PATCH` / `DELETE` | `/api/items/{id}` | 아이템 수정·삭제 (주인) |
| `POST` | `/api/items/{id}/reserve` | 찜하기 → `reserve_token` 반환 |
| `POST` | `/api/items/{id}/cancel` | 찜 취소 |
| `POST` | `/api/items/{id}/gift` | 결제 완료 신고 → `claimed`, `gift_token` 반환 |
| `POST` | `/api/items/{id}/confirm` | 주인이 "받았어요" 확인 → `recipient_confirmed` |
| `POST` | `/api/webhooks/payment` | 결제/전환 콜백 → `payment_verified` (HMAC 서명 필요) |

주인 인증은 `X-Edit-Token` 헤더 또는 `?token=` 쿼리로 보냅니다.

## 아직 없는 것

프로토타입이라 다음은 의도적으로 빠져 있습니다.

- **실제 제휴·PG 계정 연동** — 검증 뼈대(subId 발급, 서명 검증 웹훅, 검증 단계)는 다 있지만,
  쿠팡 파트너스 전환 리포트를 긁어와 웹훅으로 쏘는 배치나 PG 결제창은 아직 없습니다.
  계정이 생기면 그 자리에 끼우면 됩니다.
- **돈을 직접 받는 구조의 법적 검토** — 선물 대금을 대신 받아 나중에 전달하는 형태는
  전자금융거래법상 등록 이슈가 생길 수 있어 별도 확인이 필요합니다.
- **로그인·계정** — 수정 권한은 브라우저에 저장된 토큰뿐이라, 브라우저를 바꾸면 링크에
  `?token=...`을 붙여 옮겨야 합니다.
- 상품 링크에서 이미지·가격 자동 가져오기(OG 태그 파싱), 알림, 같은 아이템 여러 개 선물.
