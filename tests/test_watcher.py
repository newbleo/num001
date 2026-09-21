import unittest

from trainwatch.config import from_dict
from trainwatch.models import Reservation, SeatClass, Train
from trainwatch.notify import Notifier
from trainwatch.providers import AuthError, Provider, ProviderError, RateLimited, SoldOut
from trainwatch.watcher import Watcher


class RecordingNotifier(Notifier):
    def __init__(self):
        self.messages = []

    def send(self, message, important=False):
        self.messages.append((message, important))

    def text(self):
        return "\n".join(m for m, _ in self.messages)


class ScriptedProvider(Provider):
    """조회 결과를 미리 정해두고 순서대로 돌려주는 테스트용 사업자."""

    name = "scripted"

    def __init__(self, script, reserve_results=None):
        self.script = list(script)
        self.reserve_results = list(reserve_results or [])
        self.searches = 0
        self.logins = 0
        self.reserve_calls = []
        self.closed = False

    def login(self):
        self.logins += 1

    def search(self, request):
        self.searches += 1
        step = self.script[min(self.searches - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step

    def reserve(self, train, request, seat=SeatClass.ANY, waiting=False):
        self.reserve_calls.append((train, seat, waiting))
        if self.reserve_results:
            result = self.reserve_results.pop(0)
            if isinstance(result, Exception):
                raise result
        return Reservation(
            provider=self.name, train=train, reservation_no="R1", seat_class=seat, waiting=waiting
        )

    def close(self):
        self.closed = True


def train(**kwargs):
    base = dict(
        provider="scripted",
        train_name="SRT",
        train_no="301",
        dep_station="수서",
        arr_station="부산",
        dep_date="20260101",
        dep_time="080000",
        arr_time="103000",
    )
    base.update(kwargs)
    return Train(**base)


def make_config(**overrides):
    data = {
        "provider": "fake",
        "trip": {"departure": "수서", "arrival": "부산", "date": "2026-01-01"},
        "watch": {"interval": 1, "jitter": 0, "max_attempts": 10, "log_every": 1000},
    }
    for key, value in overrides.items():
        section = data.setdefault(key, {})
        section.update(value)
    return from_dict(data)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def run_watcher(provider, config, notifier=None):
    clock = FakeClock()
    notifier = notifier or RecordingNotifier()
    watcher = Watcher(
        provider,
        config,
        notifier,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        jitter_fn=lambda a, b: 0.0,
    )
    return watcher, watcher.run(), clock, notifier


class WatcherTest(unittest.TestCase):
    def test_reserves_as_soon_as_a_seat_appears(self):
        sold_out = [train()]
        open_seat = [train(general_seat=True)]
        provider = ScriptedProvider([sold_out, sold_out, open_seat])
        _, reservations, clock, notifier = run_watcher(provider, make_config())

        self.assertEqual(len(reservations), 1)
        self.assertEqual(provider.searches, 3)
        self.assertEqual(len(clock.slept), 2)  # 성공 후에는 더 자지 않는다
        self.assertIn("예약 성공", notifier.text())

    def test_no_seat_means_no_reserve_call(self):
        provider = ScriptedProvider([[train()]])
        config = make_config(watch={"max_attempts": 3})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(reservations, [])
        self.assertEqual(provider.reserve_calls, [])
        self.assertEqual(provider.searches, 3)

    def test_seat_class_filter_ignores_other_class(self):
        provider = ScriptedProvider([[train(special_seat=True)]])
        config = make_config(filter={"seat": "일반실"}, watch={"max_attempts": 2})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(reservations, [])

    def test_train_number_filter(self):
        provider = ScriptedProvider(
            [[train(train_no="301", general_seat=True), train(train_no="305", general_seat=True)]]
        )
        config = make_config(filter={"train_numbers": ["305"]})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(len(reservations), 1)
        self.assertEqual(provider.reserve_calls[0][0].train_no, "305")

    def test_lost_race_keeps_watching(self):
        provider = ScriptedProvider(
            [[train(general_seat=True)]], reserve_results=[SoldOut("놓침"), None]
        )
        watcher, reservations, _, _ = run_watcher(provider, make_config())
        self.assertEqual(len(reservations), 1)
        self.assertEqual(watcher.stats.sold_out, 1)
        self.assertEqual(len(provider.reserve_calls), 2)

    def test_dry_run_does_not_reserve(self):
        provider = ScriptedProvider([[train(general_seat=True)]])
        config = make_config(watch={"dry_run": True})
        _, reservations, _, notifier = run_watcher(provider, config)
        self.assertEqual(provider.reserve_calls, [])
        self.assertEqual(reservations[0].reservation_no, "DRY-RUN")
        self.assertIn("드라이런", notifier.text())

    def test_waiting_only_when_allowed(self):
        provider = ScriptedProvider([[train(waiting_available=True)]])
        config = make_config(watch={"max_attempts": 1})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(reservations, [])

        provider = ScriptedProvider([[train(waiting_available=True)]])
        config = make_config(filter={"allow_waiting": True})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(len(reservations), 1)
        self.assertTrue(reservations[0].waiting)
        self.assertTrue(provider.reserve_calls[0][2])

    def test_stop_after_two(self):
        provider = ScriptedProvider(
            [[train(train_no="301", general_seat=True), train(train_no="305", general_seat=True)]]
        )
        config = make_config(watch={"stop_after": 2})
        _, reservations, _, _ = run_watcher(provider, config)
        self.assertEqual(len(reservations), 2)

    def test_error_backoff_grows(self):
        provider = ScriptedProvider([ProviderError("서버 오류")])
        config = make_config(watch={"max_attempts": 3, "interval": 2})
        watcher, reservations, clock, _ = run_watcher(provider, config)
        self.assertEqual(reservations, [])
        self.assertEqual(watcher.stats.errors, 3)
        self.assertEqual(clock.slept[:2], [4.0, 8.0])  # 2*2^1, 2*2^2

    def test_rate_limited_waits_longer(self):
        provider = ScriptedProvider([RateLimited("너무 잦음")])
        config = make_config(watch={"max_attempts": 2, "interval": 2})
        _, _, clock, _ = run_watcher(provider, config)
        self.assertEqual(clock.slept[0], 8.0)  # interval * 4 * 1

    def test_relogin_on_session_expiry(self):
        provider = ScriptedProvider([AuthError("세션 만료"), [train(general_seat=True)]])
        _, reservations, _, _ = run_watcher(provider, make_config())
        self.assertEqual(len(reservations), 1)
        self.assertEqual(provider.logins, 2)  # 최초 1회 + 재로그인 1회

    def test_gives_up_after_repeated_auth_failures(self):
        provider = ScriptedProvider([AuthError("세션 만료")])
        config = make_config(watch={"max_attempts": 20})
        watcher, reservations, _, notifier = run_watcher(provider, config)
        self.assertEqual(reservations, [])
        self.assertLessEqual(provider.logins, 5)
        self.assertIn("중단", notifier.text())

    def test_duration_deadline_stops_loop(self):
        provider = ScriptedProvider([[train()]])
        config = make_config(watch={"duration_minutes": 0.05, "max_attempts": 0, "interval": 1})
        _, reservations, clock, _ = run_watcher(provider, config)
        self.assertEqual(reservations, [])
        self.assertLessEqual(clock.now, 4.0)  # 3초 예산 안에서 멈춘다

    def test_stop_requested_externally(self):
        calls = {"n": 0}

        class StoppingProvider(ScriptedProvider):
            def search(self, request):
                calls["n"] += 1
                if calls["n"] == 2:
                    watcher.stop()
                return [train()]

        provider = StoppingProvider([[train()]])
        clock = FakeClock()
        watcher = Watcher(
            provider,
            make_config(watch={"max_attempts": 0}),
            RecordingNotifier(),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            jitter_fn=lambda a, b: 0.0,
        )
        watcher.run()
        self.assertEqual(calls["n"], 2)

    def test_unexpected_exception_does_not_kill_loop(self):
        provider = ScriptedProvider([RuntimeError("이상한 오류"), [train(general_seat=True)]])
        watcher, reservations, _, _ = run_watcher(provider, make_config())
        self.assertEqual(len(reservations), 1)
        self.assertEqual(watcher.stats.errors, 1)


if __name__ == "__main__":
    unittest.main()
