"""명령행 진입점: python -m trainwatch ..."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import __version__, notify
from .config import (
    AppConfig,
    ConfigError,
    from_dict,
    load,
    load_dotenv,
    make_route,
    normalize_date,
    normalize_time,
)
from .models import SeatClass
from .providers import PROVIDERS, AuthError, ProviderError, build_provider
from .watcher import Watcher

log = logging.getLogger("trainwatch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trainwatch",
        description="기차 취소표(빈자리)를 감시하다가 자리가 나면 바로 예약합니다.",
        epilog=(
            "예) python -m trainwatch --provider srt --from 수서 --to 부산 "
            "--date 2026-01-01 --time 06:00 --seat 일반실 --interval 5\n"
            "    python -m trainwatch --provider korail --route 용산-서대전 "
            "--route 영등포-서대전 --date 2026-09-23 --time 14:00"
        ),
    )
    parser.add_argument("--version", action="version", version=f"trainwatch {__version__}")
    parser.add_argument("-c", "--config", help="YAML 설정 파일 경로")

    trip = parser.add_argument_group("여정")
    trip.add_argument("--provider", choices=PROVIDERS, help="예매 사업자 (기본: srt)")
    trip.add_argument("--from", dest="departure", help="출발역 (예: 수서)")
    trip.add_argument("--to", dest="arrival", help="도착역 (예: 부산)")
    trip.add_argument(
        "--route",
        dest="routes",
        action="append",
        metavar="출발-도착",
        help="여러 노선을 동시에 감시 (예: --route 용산-서대전 --route 영등포-서대전)",
    )
    trip.add_argument("--date", help="출발일 (2026-01-01 / 20260101 / 내일)")
    trip.add_argument("--time", help="이 시각 이후 열차만 조회 (예: 06:00)")
    trip.add_argument("--adults", type=int, help="어른 인원 (기본 1)")
    trip.add_argument("--children", type=int, help="어린이 인원")
    trip.add_argument("--seniors", type=int, help="경로 인원")
    trip.add_argument(
        "--train-type",
        choices=("all", "ktx", "saemaeul", "mugunghwa", "itx"),
        help="코레일 전용: 열차 종류 (기본 all)",
    )

    filt = parser.add_argument_group("조건")
    filt.add_argument("--seat", help="일반실 / 특실 / any (기본 any)")
    filt.add_argument("--trains", help="쉼표로 구분한 열차번호만 노림 (예: 301,305)")
    filt.add_argument("--after", help="이 시각 이후 출발 열차만 (예: 07:00)")
    filt.add_argument("--before", help="이 시각 이전 출발 열차만 (예: 10:00)")
    filt.add_argument(
        "--waiting", action="store_true", help="좌석이 없으면 예약대기라도 신청"
    )

    watch = parser.add_argument_group("감시")
    watch.add_argument("--interval", type=float, help="조회 간격(초), 최소 1.0 (기본 5)")
    watch.add_argument("--jitter", type=float, help="간격에 더할 무작위 편차(초), 기본 1.5")
    watch.add_argument("--duration", type=float, help="몇 분 동안 감시할지 (기본: 무제한)")
    watch.add_argument("--max-attempts", type=int, help="최대 조회 횟수 (기본: 무제한)")
    watch.add_argument("--stop-after", type=int, help="예약 N건 성공 시 종료 (기본 1)")
    watch.add_argument(
        "--dry-run", action="store_true", help="자리를 찾아도 실제 예약은 하지 않음"
    )

    out = parser.add_argument_group("알림/로그")
    out.add_argument("--email", help="알림 받을 메일 주소 (쉼표로 여러 명)")
    out.add_argument(
        "--email-all",
        action="store_true",
        help="예약 성공뿐 아니라 감시 시작/종료도 메일로 받기",
    )
    out.add_argument("--telegram-token", help="텔레그램 봇 토큰")
    out.add_argument("--telegram-chat-id", help="텔레그램 chat id")
    out.add_argument("--webhook", help="슬랙/디스코드 웹훅 URL")
    out.add_argument("--no-bell", action="store_true", help="성공 시 터미널 벨 끄기")
    out.add_argument(
        "--test-notify",
        action="store_true",
        help="설정된 알림 채널로 테스트 메시지를 보내고 종료 (메일 설정 확인용)",
    )
    out.add_argument("--log-file", help="로그를 파일로도 남김")
    out.add_argument("--debug", action="store_true", help="디버그 로그 출력")
    out.add_argument("--quiet", action="store_true", help="경고 이상만 출력")

    parser.add_argument(
        "--env-file",
        default=".env",
        help="계정 정보를 담은 KEY=VALUE 파일 (기본: .env, 없으면 무시)",
    )
    parser.add_argument(
        "--list-stations", action="store_true", help="SRT 정차역 목록을 출력하고 종료"
    )
    return parser


ROUTE_SEPARATORS = ("->", "→", ">", "~", "-", ":")


def parse_route(text: str) -> dict:
    """'용산-서대전' / '용산>서대전' 을 {"departure": ..., "arrival": ...} 로."""

    raw = text.strip()
    for sep in ROUTE_SEPARATORS:
        if sep in raw:
            departure, _, arrival = raw.partition(sep)
            departure, arrival = departure.strip(), arrival.strip()
            if departure and arrival:
                return {"departure": departure, "arrival": arrival}
            break
    raise ConfigError(f"노선 형식이 잘못되었습니다: {text!r} (예: 용산-서대전)")


def setup_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.debug else (logging.WARNING if args.quiet else logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """명령행 인자가 설정 파일 값을 덮어쓴다."""

    if args.provider:
        config.provider = args.provider
    if args.train_type:
        config.train_type = args.train_type

    if args.routes:
        template = {
            "date": config.search.date,
            "time": config.search.time,
            "adults": config.search.adults,
            "children": config.search.children,
            "seniors": config.search.seniors,
        }
        config.routes = [make_route(parse_route(r), template) for r in args.routes]

    if args.departure or args.arrival:
        first = config.routes[0]
        if args.departure:
            first.departure = args.departure
        if args.arrival:
            first.arrival = args.arrival

    # 날짜/시각/인원은 모든 노선에 동일하게 적용한다.
    for route in config.routes:
        if args.date:
            route.date = normalize_date(args.date)
        if args.time:
            route.time = normalize_time(args.time)
        if args.adults is not None:
            route.adults = args.adults
        if args.children is not None:
            route.children = args.children
        if args.seniors is not None:
            route.seniors = args.seniors

    filters = config.filters
    if args.seat:
        filters.seat = SeatClass.parse(args.seat)
    if args.trains:
        filters.train_numbers = [t.strip() for t in args.trains.split(",") if t.strip()]
    if args.after:
        filters.after = normalize_time(args.after)[:4]
    if args.before:
        filters.before = normalize_time(args.before)[:4]
    if args.waiting:
        filters.allow_waiting = True

    watch = config.watch
    if args.interval is not None:
        watch.interval = args.interval
    if args.jitter is not None:
        watch.jitter = args.jitter
    if args.duration is not None:
        watch.duration_minutes = args.duration
    if args.max_attempts is not None:
        watch.max_attempts = args.max_attempts
    if args.stop_after is not None:
        watch.stop_after = args.stop_after
    if args.dry_run:
        watch.dry_run = True
    # WatchConfig 의 검증(최소 간격 등)을 다시 태운다.
    watch.__post_init__()

    if args.telegram_token:
        config.notify.telegram_token = args.telegram_token
    if args.telegram_chat_id:
        config.notify.telegram_chat_id = args.telegram_chat_id
    if args.webhook:
        config.notify.webhook_url = args.webhook
    if args.email:
        config.notify.email_to = args.email
    if args.email_all:
        config.notify.email_only_important = False
    if config.notify.email_to and not config.notify.email_password:
        import os

        config.notify.email_password = os.environ.get("SMTP_PASSWORD", "")
    if args.no_bell:
        config.notify.bell = False

    # 사업자를 바꿨는데 설정 파일에 계정이 없으면 환경변수에서 다시 읽는다.
    if not config.user_id or not config.password:
        import os

        if config.provider == "korail":
            config.user_id = config.user_id or os.environ.get("KORAIL_ID", "")
            config.password = config.password or os.environ.get("KORAIL_PW", "")
        else:
            config.user_id = config.user_id or os.environ.get("SRT_ID", "")
            config.password = config.password or os.environ.get("SRT_PW", "")
    return config


def print_stations() -> None:
    from .providers.srt import STATIONS

    print("SRT 정차역:")
    print("  " + ", ".join(sorted(set(STATIONS))))
    print("\n코레일(KTX/무궁화 등)은 역 이름을 그대로 쓰면 됩니다. 예: 서울, 용산, 동대구")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_stations:
        print_stations()
        return 0

    setup_logging(args)

    # 계정 정보는 설정을 읽기 전에 환경으로 올려둔다.
    try:
        loaded = load_dotenv(args.env_file)
    except ConfigError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover
    if loaded:
        log.info("%s 에서 환경변수 %d개를 읽었습니다", args.env_file, loaded)

    try:
        config = load(args.config) if args.config else from_dict({})
        config = apply_overrides(config, args)
        if not args.test_notify:
            config.validate()
    except ConfigError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - parser.error 가 종료시킴

    notifier = notify.build(config.notify)

    if args.test_notify:
        notifier.send(
            "🔔 trainwatch 테스트 알림\n"
            "이 메시지가 보이면 알림 설정은 정상입니다.",
            important=True,
        )
        log.info("테스트 알림을 보냈습니다")
        return 0

    provider_kwargs = {}
    if config.provider == "korail":
        provider_kwargs["train_type"] = config.train_type
    try:
        provider = build_provider(
            config.provider, config.user_id, config.password, **provider_kwargs
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover
    watcher = Watcher(provider, config, notifier)

    def handle_sigint(signum, frame):  # noqa: ARG001
        if watcher._stopping:
            log.warning("강제 종료합니다")
            raise KeyboardInterrupt
        log.warning("종료 요청을 받았습니다. 진행 중인 시도를 마치고 멈춥니다 (한 번 더 누르면 즉시 종료)")
        watcher.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        reservations = watcher.run()
    except AuthError as exc:
        log.error("%s", exc)
        return 3
    except ProviderError as exc:
        log.error("사업자 오류로 중단되었습니다: %s", exc)
        return 4
    except KeyboardInterrupt:
        log.warning("사용자에 의해 중단되었습니다")
        return 130
    finally:
        provider.close()

    return 0 if reservations else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
