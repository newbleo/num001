"""알림 채널 테스트 (실제로 메일을 보내거나 네트워크를 쓰지 않는다)."""

import io
import os
import tempfile
import unittest

from trainwatch import notify
from trainwatch.config import ConfigError, NotifyConfig, from_dict, load_dotenv


class FakeSMTP:
    """smtplib.SMTP_SSL 대역. with 문과 login/send_message 만 흉내낸다."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logins = []
        self.sent = []
        self.started_tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, message):
        self.sent.append(message)


class FailingSMTP(FakeSMTP):
    def send_message(self, message):
        raise OSError("메일 서버에 연결할 수 없습니다")


class EmailNotifierTest(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def notifier(self, factory=FakeSMTP, **kwargs):
        options = dict(
            to="alstn950619@gmail.com",
            password="app-password",
            smtp_factory=factory,
        )
        options.update(kwargs)
        return notify.EmailNotifier(**options)

    def test_sends_important_message(self):
        self.notifier().send("🎫 [korail] 예약 성공: KTX 1503\n결제하세요", important=True)

        self.assertEqual(len(FakeSMTP.instances), 1)
        smtp = FakeSMTP.instances[0]
        self.assertEqual((smtp.host, smtp.port), ("smtp.gmail.com", 465))
        self.assertEqual(smtp.logins, [("alstn950619@gmail.com", "app-password")])

        mail = smtp.sent[0]
        self.assertEqual(mail["To"], "alstn950619@gmail.com")
        self.assertEqual(mail["From"], "alstn950619@gmail.com")
        self.assertIn("예약 성공", mail["Subject"])
        self.assertTrue(mail["Subject"].startswith("[trainwatch]"))
        self.assertIn("결제하세요", mail.get_content())

    def test_skips_unimportant_by_default(self):
        self.notifier().send("감시 시작", important=False)
        self.assertEqual(FakeSMTP.instances, [])

    def test_only_important_false_sends_everything(self):
        self.notifier(only_important=False).send("감시 시작", important=False)
        self.assertEqual(len(FakeSMTP.instances), 1)

    def test_multiple_recipients(self):
        notifier = self.notifier(to="a@example.com, b@example.com")
        notifier.send("알림", important=True)
        self.assertEqual(FakeSMTP.instances[0].sent[0]["To"], "a@example.com, b@example.com")

    def test_explicit_user_and_sender(self):
        notifier = self.notifier(
            to="alstn950619@gmail.com", user="bot@example.com", sender="예매봇 <bot@example.com>"
        )
        notifier.send("알림", important=True)
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.logins[0][0], "bot@example.com")
        self.assertEqual(smtp.sent[0]["From"], "예매봇 <bot@example.com>")

    def test_failure_is_swallowed(self):
        with self.assertLogs("trainwatch.notify", level="WARNING") as captured:
            self.notifier(factory=FailingSMTP).send("알림", important=True)
        self.assertIn("메일 알림 실패", "\n".join(captured.output))

    def test_no_recipient_is_noop(self):
        self.notifier(to="").send("알림", important=True)
        self.assertEqual(FakeSMTP.instances, [])


class BuildNotifierTest(unittest.TestCase):
    def test_email_channel_added_when_password_present(self):
        config = NotifyConfig(
            console=False, email_to="alstn950619@gmail.com", email_password="app-password"
        )
        channels = notify.build(config).notifiers
        self.assertEqual(len(channels), 1)
        self.assertIsInstance(channels[0], notify.EmailNotifier)
        self.assertEqual(channels[0].to, ["alstn950619@gmail.com"])

    def test_email_skipped_with_warning_when_password_missing(self):
        config = NotifyConfig(console=False, email_to="alstn950619@gmail.com")
        with self.assertLogs("trainwatch.notify", level="WARNING") as captured:
            channels = notify.build(config).notifiers
        self.assertEqual(channels, [])
        self.assertIn("앱 비밀번호", "\n".join(captured.output))

    def test_all_channels(self):
        config = NotifyConfig(
            telegram_token="t",
            telegram_chat_id="c",
            webhook_url="http://hook",
            email_to="alstn950619@gmail.com",
            email_password="pw",
        )
        kinds = [type(c).__name__ for c in notify.build(config).notifiers]
        self.assertEqual(
            kinds,
            ["ConsoleNotifier", "TelegramNotifier", "WebhookNotifier", "EmailNotifier"],
        )

    def test_config_reads_email_section_and_env_password(self):
        os.environ["SMTP_PASSWORD"] = "from-env"
        self.addCleanup(os.environ.pop, "SMTP_PASSWORD", None)
        config = from_dict(
            {
                "provider": "fake",
                "trip": {"departure": "용산", "arrival": "서대전", "date": "2026-09-23"},
                "notify": {"email": {"to": "alstn950619@gmail.com", "only_important": False}},
            }
        )
        self.assertEqual(config.notify.email_to, "alstn950619@gmail.com")
        self.assertEqual(config.notify.email_password, "from-env")
        self.assertFalse(config.notify.email_only_important)


class ConsoleNotifierTest(unittest.TestCase):
    def test_bell_only_on_important(self):
        stream = io.StringIO()
        notifier = notify.ConsoleNotifier(bell=True, stream=stream)
        notifier.send("보통", important=False)
        notifier.send("중요", important=True)
        self.assertEqual(stream.getvalue(), "보통\n\a중요\n")


class DotenvTest(unittest.TestCase):
    def write_env(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_loads_values(self):
        path = self.write_env(
            "# 코레일 계정\n"
            "KORAIL_ID=alstn950619@gmail.com\n"
            "export KORAIL_PW='비밀번호'\n"
            'SMTP_PASSWORD="app pw"\n'
            "\n"
        )
        for key in ("KORAIL_ID", "KORAIL_PW", "SMTP_PASSWORD"):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

        self.assertEqual(load_dotenv(path), 3)
        self.assertEqual(os.environ["KORAIL_ID"], "alstn950619@gmail.com")
        self.assertEqual(os.environ["KORAIL_PW"], "비밀번호")
        self.assertEqual(os.environ["SMTP_PASSWORD"], "app pw")

    def test_does_not_override_existing_env(self):
        path = self.write_env("KORAIL_ID=from-file\n")
        os.environ["KORAIL_ID"] = "from-shell"
        self.addCleanup(os.environ.pop, "KORAIL_ID", None)
        self.assertEqual(load_dotenv(path), 0)
        self.assertEqual(os.environ["KORAIL_ID"], "from-shell")

    def test_missing_file_is_fine(self):
        self.assertEqual(load_dotenv("/tmp/definitely-not-here.env"), 0)

    def test_directory_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_dotenv("/tmp")


if __name__ == "__main__":
    unittest.main()


class AppPasswordTest(unittest.TestCase):
    def test_gmail_style_spaces_are_removed(self):
        self.assertEqual(
            notify.normalize_app_password("ppnx bnuu szde vuvh"), "ppnxbnuuszdevuvh"
        )

    def test_already_joined_password_untouched(self):
        self.assertEqual(
            notify.normalize_app_password("ppnxbnuuszdevuvh"), "ppnxbnuuszdevuvh"
        )

    def test_ordinary_password_with_spaces_is_kept(self):
        # 일반 비밀번호의 공백은 의미가 있을 수 있으므로 건드리지 않는다.
        self.assertEqual(notify.normalize_app_password("my secret pw"), "my secret pw")
        self.assertEqual(notify.normalize_app_password("cambodia 18@"), "cambodia 18@")

    def test_surrounding_whitespace_trimmed(self):
        self.assertEqual(notify.normalize_app_password("  swordfish \n"), "swordfish")

    def test_notifier_logs_in_with_normalized_password(self):
        FakeSMTP.instances = []
        notify.EmailNotifier(
            to="alstn950619@gmail.com",
            password="ppnx bnuu szde vuvh",
            smtp_factory=FakeSMTP,
        ).send("알림", important=True)
        self.assertEqual(
            FakeSMTP.instances[0].logins,
            [("alstn950619@gmail.com", "ppnxbnuuszdevuvh")],
        )
