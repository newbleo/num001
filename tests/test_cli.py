import unittest

from trainwatch import cli


class CliTest(unittest.TestCase):
    def parse(self, argv):
        return cli.build_parser().parse_args(argv)

    def test_overrides_applied(self):
        args = self.parse(
            [
                "--provider", "fake", "--from", "수서", "--to", "부산",
                "--date", "2026-01-01", "--time", "06:00", "--seat", "특실",
                "--trains", "301, 305", "--after", "07:00", "--before", "10:00",
                "--waiting", "--interval", "3", "--jitter", "0", "--stop-after", "2",
                "--dry-run",
            ]
        )
        config = cli.apply_overrides(cli.from_dict({}), args)
        config.validate()
        self.assertEqual(config.provider, "fake")
        self.assertEqual(config.search.date, "20260101")
        self.assertEqual(config.search.time, "060000")
        self.assertEqual(config.filters.seat.value, "special")
        self.assertEqual(config.filters.train_numbers, ["301", "305"])
        self.assertEqual(config.filters.after, "0700")
        self.assertEqual(config.filters.before, "1000")
        self.assertTrue(config.filters.allow_waiting)
        self.assertEqual(config.watch.interval, 3)
        self.assertEqual(config.watch.stop_after, 2)
        self.assertTrue(config.watch.dry_run)

    def test_interval_floor_is_enforced_from_cli(self):
        args = self.parse(["--interval", "0.1"])
        with self.assertRaises(cli.ConfigError):
            cli.apply_overrides(cli.from_dict({}), args)

    def test_end_to_end_with_fake_provider(self):
        exit_code = cli.main(
            [
                "--provider", "fake", "--from", "수서", "--to", "부산",
                "--date", "내일", "--interval", "1", "--jitter", "0",
                "--max-attempts", "5", "--dry-run", "--quiet", "--no-bell",
            ]
        )
        self.assertEqual(exit_code, 0)

    def test_exit_code_when_nothing_found(self):
        exit_code = cli.main(
            [
                "--provider", "fake", "--from", "수서", "--to", "부산",
                "--date", "내일", "--interval", "1", "--jitter", "0",
                "--max-attempts", "1", "--seat", "특실", "--quiet", "--no-bell",
            ]
        )
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()


class RouteOptionTest(unittest.TestCase):
    def parse(self, argv):
        return cli.build_parser().parse_args(argv)

    def test_parse_route_separators(self):
        for text in ("용산-서대전", "용산>서대전", "용산->서대전", "용산 → 서대전"):
            self.assertEqual(
                cli.parse_route(text), {"departure": "용산", "arrival": "서대전"}
            )

    def test_parse_route_rejects_garbage(self):
        with self.assertRaises(cli.ConfigError):
            cli.parse_route("용산서대전")

    def test_multiple_routes_share_date_and_time(self):
        args = self.parse(
            [
                "--provider", "korail",
                "--route", "용산-서대전", "--route", "영등포-서대전",
                "--date", "2026-09-23", "--time", "14:00", "--adults", "2",
            ]
        )
        config = cli.apply_overrides(cli.from_dict({}), args)
        self.assertEqual(len(config.routes), 2)
        self.assertEqual(
            [(r.departure, r.arrival) for r in config.routes],
            [("용산", "서대전"), ("영등포", "서대전")],
        )
        for route in config.routes:
            self.assertEqual(route.date, "20260923")
            self.assertEqual(route.time, "140000")
            self.assertEqual(route.adults, 2)

    def test_cli_routes_override_config_file_trips(self):
        base = cli.from_dict(
            {
                "provider": "fake",
                "trip": {"date": "2026-09-23", "time": "14:00"},
                "trips": [{"departure": "용산", "arrival": "서대전"}],
            }
        )
        args = self.parse(["--route", "영등포-서대전"])
        config = cli.apply_overrides(base, args)
        self.assertEqual(len(config.routes), 1)
        self.assertEqual(config.routes[0].departure, "영등포")
        self.assertEqual(config.routes[0].time, "140000")  # 파일의 시각은 유지
