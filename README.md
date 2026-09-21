# num001 — trainwatch

기차 **취소표(빈자리)를 감시하다가 자리가 나면 즉시 예약**을 걸어주는 CLI 도구입니다.
SRT와 코레일(KTX·새마을·무궁화 등)을 지원하고, 자리를 잡으면 콘솔·텔레그램·웹훅으로 알려줍니다.

> 결제까지 자동으로 하지는 않습니다. 예약(10분 내 결제 조건)까지만 잡아두고 알림을 보내므로,
> 알림을 받으면 앱이나 홈페이지에서 결제를 마무리하면 됩니다.

## 설치

```bash
pip install -r requirements.txt
```

파이썬 3.10 이상이 필요합니다. (requests, PyYAML 외 의존성 없음)

## 빠른 시작

계정은 환경변수로 넣습니다.

```bash
export SRT_ID='you@example.com'      # 회원번호 / 이메일 / 휴대폰번호 모두 가능
export SRT_PW='********'
# 코레일이면 KORAIL_ID / KORAIL_PW
```

실행:

```bash
python -m trainwatch --provider srt --from 수서 --to 부산 \
    --date 2026-01-01 --time 06:00 --seat 일반실 --interval 5
```

먼저 감이 안 잡히면 **연습 모드**로 동작을 확인해 보세요. 실제 서버에 접속하지 않습니다.

```bash
python -m trainwatch --provider fake --from 수서 --to 부산 --date 내일 --interval 1
```

실제 계정으로 조회만 해보고 예약은 하지 않으려면 `--dry-run` 을 붙입니다.

## 노선 여러 개 동시에 감시

`--route` 를 여러 번 주면 노선을 번갈아 조회합니다.

```bash
export KORAIL_ID='...'; export KORAIL_PW='...'
python -m trainwatch --provider korail \
    --route 용산-서대전 --route 영등포-서대전 \
    --date 2026-09-23 --time 14:00 --interval 5
```

`--interval` 은 **노선 하나를 다시 보기까지의 간격**입니다. 노선이 2개면 요청은
그 절반 간격으로 고르게 나눠 보내므로, 노선을 늘려도 한 노선의 감시 주기는 그대로입니다.
먼저 자리가 나는 노선을 잡고, 그 노선으로 예약합니다.

설정 파일로는 `trips:` 목록을 씁니다. 바로 쓸 수 있는 예시가
[`configs/seodaejeon-0923.yaml`](configs/seodaejeon-0923.yaml) 에 있습니다.

```yaml
trip:                  # 모든 노선 공통 조건
  date: 2026-09-23
  time: "14:00"
trips:
  - departure: 용산
    arrival: 서대전
  - departure: 영등포
    arrival: 서대전
```

## 설정 파일

반복해서 쓸 조건은 YAML로 두는 편이 편합니다.

```bash
cp config.example.yaml config.yaml
python -m trainwatch -c config.yaml
```

명령행 인자는 설정 파일 값을 덮어씁니다. (`config.yaml` 은 `.gitignore` 에 들어 있습니다.)

## 주요 옵션

| 옵션 | 설명 |
| --- | --- |
| `--provider srt\|korail\|fake` | 예매 사업자 |
| `--from` / `--to` | 출발역 / 도착역 (단일 노선) |
| `--route 용산-서대전` | 노선 추가. 여러 번 쓰면 여러 노선을 번갈아 감시 |
| `--date` | `2026-01-01`, `20260101`, `오늘`, `내일` |
| `--time` | 이 시각 이후 열차만 조회 |
| `--seat 일반실\|특실\|any` | 원하는 좌석 등급 |
| `--trains 301,305` | 특정 열차번호만 노림 |
| `--after` / `--before` | 출발 시각 범위 제한 |
| `--waiting` | 좌석이 없으면 예약대기라도 신청 |
| `--interval` / `--jitter` | 노선당 조회 간격(초)과 무작위 편차 |
| `--duration` / `--max-attempts` | 몇 분 / 몇 회까지 감시할지 |
| `--stop-after` | 예약 N건 성공 시 종료 (기본 1) |
| `--dry-run` | 자리를 찾아도 예약하지 않고 알림만 |
| `--telegram-token`, `--telegram-chat-id`, `--webhook` | 알림 채널 |
| `--list-stations` | SRT 정차역 목록 출력 |

종료 코드: `0` 예약 성공, `1` 못 잡고 종료, `3` 로그인 실패, `4` 사업자 오류, `130` 사용자 중단.

## 동작 방식

```
노선 1 조회 → 조건에 맞는 편성 필터 → 빈자리 있으면 즉시 예약(reserve) → 알림
   ↓                                                   └ 경합에 지면(SoldOut) 계속 감시
(interval/노선수 + jitter 만큼 대기, 오류가 나면 지수 백오프)
   ↓
노선 2 조회 → … → 다시 노선 1
```

- **세션 만료 자동 복구**: 로그인 세션이 끊기면 재로그인 후 감시를 이어갑니다(3회 연속 실패 시 중단).
- **백오프**: 연속 오류마다 대기 시간을 2배씩 늘리고(최대 60초), 429/503 응답이면 더 길게 쉽니다.
- **Ctrl-C**: 한 번 누르면 진행 중인 시도를 마치고 정리 후 종료, 한 번 더 누르면 즉시 종료합니다.

## 알림

- **텔레그램**: [@BotFather](https://t.me/BotFather) 로 봇을 만들고 토큰을, 봇과 대화를 시작한 뒤
  `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat_id` 를 확인해 넣습니다.
- **웹훅**: 슬랙/디스코드 웹훅 URL을 그대로 쓰면 됩니다(`{"text": ...}` 와 `{"content": ...}` 둘 다 보냄).

## 구조

```
trainwatch/
  cli.py            명령행 파싱, 로깅, 시그널 처리
  config.py         YAML + 환경변수 + 검증
  watcher.py        감시 루프 (폴링·백오프·재로그인·예약 시도)
  models.py         Train / Reservation / SeatClass
  notify.py         콘솔 · 텔레그램 · 웹훅
  providers/
    base.py         Provider 인터페이스 + 연습용 FakeProvider
    srt.py          SRT 모바일 API
    korail.py       코레일 모바일 API
```

새 사업자를 붙이려면 `Provider` 의 `login` / `search` / `reserve` 세 가지만 구현하고
`providers/__init__.py` 의 `build_provider` 에 등록하면 됩니다.

## 테스트

```bash
python -m unittest discover -s tests -v
```

네트워크 없이 스텁 세션과 가짜 시계로 돌기 때문에 몇 초면 끝납니다.

## 알아두어야 할 점

- SRT·코레일 모두 **공개된 API가 아니라 모바일 앱이 쓰는 비공개 엔드포인트**입니다.
  파라미터가 예고 없이 바뀔 수 있으니 로그인이나 예약이 실패하면 `--debug` 로 원본 응답을 확인하고
  `providers/srt.py`, `providers/korail.py` 의 payload를 맞춰야 합니다.
- 조회 간격은 최소 1초로 제한되어 있습니다. 실제로는 **5초 내외**를 권장합니다.
  지나치게 잦은 조회는 계정·IP 차단 사유가 되고, 각 사업자 이용약관에서 자동화 도구 사용을 제한할 수 있습니다.
  본인 계정으로 본인이 탈 표를 잡는 용도로만 쓰세요.
- 예약 후 결제 기한(보통 10분)을 넘기면 자동 취소됩니다. 알림을 받으면 바로 결제하세요.
