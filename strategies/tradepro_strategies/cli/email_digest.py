"""tradepro-email — pull the latest compare cache and email a digest.

Designed to be invoked by launchd after the daily refresh job. Three
transports:

    smtp     (default) credentials in ~/.tradepro/email-creds.json
    outlook  (macOS)   AppleScript drives Microsoft Outlook — no creds
    mail     (macOS)   AppleScript drives Apple Mail.app — no creds

The osascript transports require macOS + the target app installed and
signed in; first run prompts for permission. They're convenient for
personal daily digests on a single Mac. SMTP is the only choice for
non-Mac deployments (AWS, CI, etc.).

    uv run tradepro-email                                   # SMTP
    uv run tradepro-email --transport outlook               # Outlook on Mac
    uv run tradepro-email --transport mail --to me@x.com    # Apple Mail
    uv run tradepro-email --dry-run                         # preview
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import subprocess
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
    p.add_argument(
        "--transport",
        choices=("smtp", "outlook", "mail"),
        default=os.environ.get("TRADEPRO_EMAIL_TRANSPORT", "smtp"),
        help=(
            "Transport: smtp (default), outlook (macOS Outlook via "
            "AppleScript, no creds needed), mail (macOS Apple Mail "
            "via AppleScript, no creds needed)."
        ),
    )
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


def _applescript_quote(s: str) -> str:
    """Escape a Python string for safe interpolation into an
    AppleScript string literal. AppleScript needs backslash and
    double-quote escaping; tabs/newlines stay as-is inside the
    literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_via_outlook_mac(digest: EmailDigest, recipients: list[str]) -> None:
    """Drive Microsoft Outlook for Mac via AppleScript. Sends the
    PLAIN-TEXT body — the HTML body would arrive as raw markup in
    most Outlook for Mac versions because the AppleScript `content`
    property is interpreted as plain text by the Outlook side
    (verified in the wild). The plain-text body is structured to
    read well without HTML rendering — sections, indentation,
    ASCII rules.

    Outlook must be installed and signed into at least one account.
    macOS prompts for Automation permission on first run."""
    if not recipients:
        raise RuntimeError("Outlook transport: no recipients.")
    if sys.platform != "darwin":
        raise RuntimeError("Outlook transport requires macOS.")
    subject = _applescript_quote(digest.subject)
    body = _applescript_quote(digest.text_body)
    add_recipients = "\n".join(
        f'  make new recipient at theMessage with properties '
        f'{{email address:{{address:"{_applescript_quote(r)}"}}}}'
        for r in recipients
    )
    script = f"""
tell application "Microsoft Outlook"
  set theMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}"}}
{add_recipients}
  send theMessage
end tell
"""
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Outlook AppleScript failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


def send_via_mail_app(digest: EmailDigest, recipients: list[str]) -> None:
    """Drive Apple Mail.app via AppleScript. Same prerequisites as
    Outlook: app installed, an account configured, automation
    permission granted (one-time prompt)."""
    if not recipients:
        raise RuntimeError("Mail transport: no recipients.")
    if sys.platform != "darwin":
        raise RuntimeError("Mail transport requires macOS.")
    subject = _applescript_quote(digest.subject)
    # Mail.app's `content` accepts plain text reliably; HTML works
    # but display varies. Send the text body for simplicity.
    body = _applescript_quote(digest.text_body)
    add_recipients = "\n".join(
        f'  make new to recipient at end of to recipients with properties '
        f'{{address:"{_applescript_quote(r)}"}}'
        for r in recipients
    )
    script = f"""
tell application "Mail"
  set theMessage to make new outgoing message with properties {{subject:"{subject}", content:"{body}", visible:false}}
  tell theMessage
{add_recipients}
  end tell
  send theMessage
end tell
"""
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Mail AppleScript failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


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

    recipients = cfg.get("to") or []
    if args.dry_run:
        print(f"# Subject:   {digest.subject}")
        print(f"# To:        {', '.join(recipients) or '(unset)'}")
        print(f"# Transport: {args.transport}")
        if args.transport == "smtp":
            print(f"# Sender:    {cfg.get('from') or '(unset)'}")
        print()
        print(digest.text_body)
        return

    if args.transport == "outlook":
        send_via_outlook_mac(digest, recipients)
    elif args.transport == "mail":
        send_via_mail_app(digest, recipients)
    else:
        send_email(digest, cfg)
    print(f"sent via {args.transport}: {digest.subject} → {', '.join(recipients)}")


if __name__ == "__main__":
    main()
