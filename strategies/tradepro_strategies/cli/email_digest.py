"""tradepro-email — pull the latest compare cache and email a digest.

Designed to be invoked by launchd after the daily refresh job. The
digest content comes from the API (so this works whether the API
is local or deployed); credentials for SMTP come from
~/.tradepro/email-creds.json or env vars.

    uv run tradepro-email                 # send via configured creds
    uv run tradepro-email --dry-run       # build + print, don't send
    uv run tradepro-email --to me@x.com   # one-off override
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

from ..email_digest import EmailDigest, build_digest


CRED_PATH = Path.home() / ".tradepro" / "email-creds.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--api-base", default=os.environ.get("TRADEPRO_API_URL", "http://localhost:5080"))
    p.add_argument("--api-token", default=os.environ.get("TRADEPRO_API_TOKEN"))
    p.add_argument("--to", action="append", help="Override recipient (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="Build + print to stdout, do not send")
    return p.parse_args()


def load_smtp_creds(args: argparse.Namespace) -> dict:
    """Resolve SMTP credentials. File wins; env vars fill gaps."""
    data: dict = {}
    if CRED_PATH.is_file():
        try:
            data = json.loads(CRED_PATH.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: could not read {CRED_PATH}: {e}", file=sys.stderr)
    cfg = {
        "smtp_host": data.get("smtp_host") or os.environ.get("TRADEPRO_SMTP_HOST"),
        "smtp_port": int(data.get("smtp_port") or os.environ.get("TRADEPRO_SMTP_PORT") or 465),
        "smtp_user": data.get("smtp_user") or os.environ.get("TRADEPRO_SMTP_USER"),
        "smtp_password": data.get("smtp_password") or os.environ.get("TRADEPRO_SMTP_PASSWORD"),
        "from": data.get("from") or os.environ.get("TRADEPRO_EMAIL_FROM"),
        "to": args.to or data.get("to") or [os.environ.get("TRADEPRO_EMAIL_TO")],
    }
    cfg["to"] = [t for t in (cfg["to"] or []) if t]
    return cfg


def fetch_payloads(api_base: str, token: str | None) -> list[dict]:
    """Pull every universe's latest compare envelope from the API."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = api_base.rstrip("/")
    r = requests.get(f"{base}/api/compare/universes", headers=headers, timeout=15)
    r.raise_for_status()
    universes = (r.json() or {}).get("universes") or []
    payloads: list[dict] = []
    for u in universes:
        name = u.get("universe") if isinstance(u, dict) else u
        if not name:
            continue
        try:
            resp = requests.get(
                f"{base}/api/compare/latest",
                params={"universe": name},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            payloads.append(resp.json())
        except requests.RequestException as e:
            print(f"warning: could not fetch {name}: {e}", file=sys.stderr)
    return payloads


def send_email(digest: EmailDigest, cfg: dict) -> None:
    missing = [k for k in ("smtp_host", "smtp_user", "smtp_password", "from") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"SMTP creds missing: {missing}. Configure {CRED_PATH} or "
            f"set TRADEPRO_SMTP_* env vars."
        )
    if not cfg.get("to"):
        raise RuntimeError("No recipient configured (cfg.to is empty).")

    msg = EmailMessage()
    msg["Subject"] = digest.subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(digest.text_body)
    msg.add_alternative(digest.html_body, subtype="html")

    ctx = ssl.create_default_context()
    if cfg["smtp_port"] == 465:
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx, timeout=30) as s:
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
            s.starttls(context=ctx)
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.send_message(msg)


def main() -> None:
    args = parse_args()
    payloads = fetch_payloads(args.api_base, args.api_token)
    digest = build_digest(payloads)
    cfg = load_smtp_creds(args)

    if args.dry_run:
        print(f"# Subject: {digest.subject}")
        print(f"# To:      {', '.join(cfg.get('to') or []) or '(unset)'}")
        print(f"# Sender:  {cfg.get('from') or '(unset)'}")
        print()
        print(digest.text_body)
        return

    send_email(digest, cfg)
    print(f"sent: {digest.subject} → {', '.join(cfg['to'])}")


if __name__ == "__main__":
    main()
