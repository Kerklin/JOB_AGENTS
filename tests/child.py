"""Runs the agents once inside a test sandbox, with a fake Gmail server."""
import json
import os
import pathlib
import smtplib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
MODE = os.environ.get("FAKE_SMTP", "ok")
MAILDIR = pathlib.Path(os.environ["MAILDIR"])
MAILDIR.mkdir(parents=True, exist_ok=True)


class FakeSMTP:
    def __init__(self, host, port, context=None, timeout=None):
        if MODE == "down":
            raise OSError("connection refused")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        if MODE == "authfail":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        if MODE == "sendfail":
            raise smtplib.SMTPException("send failed")
        n = len(list(MAILDIR.glob("*.eml")))
        (MAILDIR / f"{n:03d}.eml").write_bytes(msg.as_bytes())


smtplib.SMTP_SSL = FakeSMTP
from agents import run  # noqa: E402

sys.exit(run.main())
