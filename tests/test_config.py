import os
import unittest
from datetime import date, timedelta

from trainwatch.config import (
    ConfigError,
    FilterConfig,
    WatchConfig,
    expand_env,
    from_dict,
    normalize_date,
    normalize_time,
)
from trainwatch.models import SeatClass, Train


def make_train(**kwargs):
    base = dict(
        provider="fake",
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


class NormalizeTest(unittest.TestCase):
    def test_date_formats(self):
        self.assertEqual(normalize_date("2026-01-01"), "20260101")
        self.assertEqual(normalize_date("20260101"), "20260101")
        self.assertEqual(normalize_date(date(2026, 1, 1)), "20260101")
        self.assertEqual(normalize_date("오늘"), date.today().strftime("%Y%m%d"))
        self.assertEqual(
            normalize_date("내일"), (date.today() + timedelta(days=1)).strftime("%Y%m%d")
        )

    def test_mmdd_rolls_to_next_year_when_past(self):
        self.assertEqual(normalize_date("1231")[4:], "1231")

    def test_bad_date(self):
        with self.assertRaises(ConfigError):
            normalize_date("언젠가")

    def test_time_formats(self):
        self.assertEqual(normalize_time("06:00"), "060000")
        self.assertEqual(normalize_time("0600"), "060000")
        self.assertEqual(normalize_time("6"), "060000")
        self.assertEqual(normalize_time("063000"), "063000")
        self.assertEqual(normalize_time(None), "000000")

    def test_bad_time(self):
        with self.assertRaises(ConfigError):
            normalize_time("25:00")


class EnvTest(unittest.TestCase):
    def test_expand(self):
        os.environ["TW_TEST_VAR"] = "hello"
        self.addCleanup(os.environ.pop, "TW_TEST_VAR", None)
        self.assertEqual(expand_env("${TW_TEST_VAR}"), "hello")
        self.assertEqual(expand_env("${TW_MISSING:-기본}"), "기본")
        self.assertEqual(expand_env({"a": ["${TW_TEST_VAR}"]}), {"a": ["hello"]})


class WatchConfigTest(unittest.TestCase):
    def test_interval_floor(self):
        with self.assertRaises(ConfigError):
            WatchConfig(interval=0.2)

    def test_negative_jitter(self):
        with self.assertRaises(ConfigError):
            WatchConfig(jitter=-1)


class FilterTest(unittest.TestCase):
    def test_train_number_filter(self):
        f = FilterConfig(train_numbers=["305"])
        self.assertFalse(f.matches(make_train(train_no="301")))
        self.assertTrue(f.matches(make_train(train_no="305")))

    def test_time_window(self):
        f = FilterConfig(after="0700", before="0900")
        self.assertTrue(f.matches(make_train(dep_time="080000")))
        self.assertFalse(f.matches(make_train(dep_time="063000")))
        self.assertFalse(f.matches(make_train(dep_time="093000")))

    def test_seat_matching(self):
        general = make_train(general_seat=True)
        special = make_train(special_seat=True)
        self.assertTrue(general.has_seat(SeatClass.GENERAL))
        self.assertFalse(general.has_seat(SeatClass.SPECIAL))
        self.assertTrue(special.has_seat(SeatClass.ANY))


class FromDictTest(unittest.TestCase):
    def test_full_config(self):
        config = from_dict(
            {
                "provider": "fake",
                "trip": {
                    "departure": "수서",
                    "arrival": "부산",
                    "date": "2026-01-01",
                    "time": "06:00",
                    "adults": 2,
                },
                "filter": {"seat": "특실", "after": "07:00", "train_numbers": [301]},
                "watch": {"interval": 3, "stop_after": 2},
                "notify": {"console": False, "webhook": {"url": "http://x"}},
            }
        )
        config.validate()
        self.assertEqual(config.search.passengers, 2)
        self.assertEqual(config.filters.seat, SeatClass.SPECIAL)
        self.assertEqual(config.filters.train_numbers, ["301"])
        self.assertEqual(config.filters.after, "0700")
        self.assertEqual(config.watch.stop_after, 2)
        self.assertFalse(config.notify.console)
        self.assertEqual(config.notify.webhook_url, "http://x")

    def test_missing_stations(self):
        config = from_dict({"provider": "fake", "trip": {"date": "2026-01-01"}})
        with self.assertRaises(ConfigError):
            config.validate()

    def test_same_station(self):
        config = from_dict(
            {"provider": "fake", "trip": {"departure": "수서", "arrival": "수서", "date": "2026-01-01"}}
        )
        with self.assertRaises(ConfigError):
            config.validate()

    def test_credentials_required_for_real_provider(self):
        for key in ("SRT_ID", "SRT_PW"):
            os.environ.pop(key, None)
        config = from_dict(
            {"provider": "srt", "trip": {"departure": "수서", "arrival": "부산", "date": "2026-01-01"}}
        )
        with self.assertRaises(ConfigError):
            config.validate()


if __name__ == "__main__":
    unittest.main()


class MultiRouteTest(unittest.TestCase):
    def test_trips_inherit_common_trip_defaults(self):
        config = from_dict(
            {
                "provider": "fake",
                "trip": {"date": "2026-09-23", "time": "14:00", "adults": 2},
                "trips": [
                    {"departure": "용산", "arrival": "서대전"},
                    {"departure": "영등포", "arrival": "서대전", "time": "15:00"},
                ],
            }
        )
        config.validate()
        self.assertEqual(len(config.routes), 2)
        self.assertEqual(config.routes[0].departure, "용산")
        self.assertEqual(config.routes[0].time, "140000")
        self.assertEqual(config.routes[0].adults, 2)
        self.assertEqual(config.routes[1].time, "150000")   # 노선별 재정의
        self.assertEqual(config.routes[1].adults, 2)        # 공통값 상속
        self.assertEqual(config.search, config.routes[0])   # 단축 접근자

    def test_duplicate_route_rejected(self):
        config = from_dict(
            {
                "provider": "fake",
                "trip": {"date": "2026-09-23"},
                "trips": [
                    {"departure": "용산", "arrival": "서대전"},
                    {"departure": "용산", "arrival": "서대전"},
                ],
            }
        )
        with self.assertRaises(ConfigError):
            config.validate()

    def test_empty_trips_rejected(self):
        with self.assertRaises(ConfigError):
            from_dict({"provider": "fake", "trips": []})

    def test_each_route_is_validated(self):
        config = from_dict(
            {
                "provider": "fake",
                "trip": {"date": "2026-09-23"},
                "trips": [
                    {"departure": "용산", "arrival": "서대전"},
                    {"departure": "영등포", "arrival": ""},
                ],
            }
        )
        with self.assertRaises(ConfigError):
            config.validate()
