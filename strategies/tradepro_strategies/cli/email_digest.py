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
        "--save-html",
        metavar="PATH",
        help=(
            "Write the rendered HTML body to PATH (e.g. ~/digest.html). "
            "Opens cleanly in any browser with the full table + colour "
            "treatment that AppleScript transports can't render. "
            "Combines with --dry-run to skip the send step entirely."
        ),
    )
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
    p.add_argument(
        "--setup-gmail",
        action="store_true",
        help=(
            "Interactive Gmail App Password setup wizard. Walks you "
            "through generating a 16-char app password, prompts for "
            f"the values, and writes them to {CRED_PATH} with "
            "permissions 0600. Skips the digest send."
        ),
    )
    p.add_argument(
        "--check-creds",
        action="store_true",
        help=(
            "Verify SMTP login works against the host:port:user combo "
            "in ~/.tradepro/email-creds.json without sending mail. "
            "Exits 0 on success, non-zero with the SMTP error otherwise."
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


def fetch_holdings(api_base: str, token: str | None) -> tuple[list[dict], str | None]:
    """Pull the T212 positions list + broker mode from the API.
    Returns (positions, mode). mode is "demo" / "live" / None.
    Never raises — empty list + None when T212 isn't configured or
    the API is unreachable."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = api_base.rstrip("/")
    try:
        resp = requests.get(
            f"{base}/api/integrations/trading212/positions",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except requests.RequestException as e:
        print(f"warning: could not fetch T212 positions: {e}", file=sys.stderr)
        return [], None
    if not data.get("enabled"):
        return [], data.get("mode")
    return (data.get("positions") or []), data.get("mode")


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


def run_setup_gmail() -> None:
    """Walk the user through generating a Gmail App Password and write
    ~/.tradepro/email-creds.json with the answers. App Passwords are
    16 random characters Google issues for "less-trusted apps" once
    the account has 2-Step Verification on. They're the only way to
    SMTP-login to Gmail since Google killed "less secure apps" access.

    The wizard does NOT navigate to Google for you — that has to
    happen in a browser where the user is already signed in. We
    print the URL + steps and then capture the result."""
    print("Gmail App Password setup")
    print("=" * 60)
    print(
        "Gmail SMTP requires a 16-character App Password — NOT your\n"
        "regular Google password. To generate one:\n"
        "\n"
        "  1. Sign in to your Google account in a browser.\n"
        "  2. Visit:  https://myaccount.google.com/apppasswords\n"
        "  3. (If the page is missing, you first need 2-Step\n"
        "      Verification on at https://myaccount.google.com/security)\n"
        "  4. App name → 'TradePro' (or anything memorable).\n"
        "  5. Click 'Create'. Google shows a 16-char code with spaces\n"
        "     (e.g. 'abcd efgh ijkl mnop'). Copy it. The spaces don't\n"
        "     matter — strip them or keep them, both work.\n"
        "\n"
        f"This wizard writes the creds to {CRED_PATH}\n"
        "with file permissions 0600 (owner-only).\n"
    )
    if input("Continue? [Y/n] ").strip().lower() in {"n", "no"}:
        print("Aborted.")
        return

    gmail_user = input("Gmail address (e.g. you@gmail.com): ").strip()
    if "@" not in gmail_user:
        print(f"That doesn't look like an email address: {gmail_user!r}")
        sys.exit(1)
    app_password = input("16-char App Password (spaces ok): ").strip()
    if len(app_password.replace(" ", "")) < 12:
        print("App password looks too short — should be 16 chars.")
        sys.exit(1)
    app_password = app_password.replace(" ", "")
    to_addr = input(
        f"Send digests TO (default = same as From, {gmail_user}): "
    ).strip() or gmail_user

    creds = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_user": gmail_user,
        "smtp_password": app_password,
        "from": gmail_user,
        "to": [to_addr],
    }
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_text(json.dumps(creds, indent=2) + "\n")
    try:
        os.chmod(CRED_PATH, 0o600)
    except OSError:
        pass  # Windows / non-POSIX — chmod isn't critical, just nice
    print(f"\nWrote {CRED_PATH} (permissions 0600)")
    print(
        "Verify it works without sending mail:\n"
        "    uv run tradepro-email --check-creds\n"
        "Then to send a real digest:\n"
        "    uv run tradepro-email\n"
    )


def check_smtp_creds(cfg: dict) -> int:
    """Login to SMTP, immediately QUIT, return exit code. Does NOT
    send mail. Surfaces the precise SMTP error (e.g. 535 Username and
    Password not accepted) instead of letting the user wonder why a
    digest never arrived."""
    missing = [k for k in ("smtp_host", "smtp_user", "smtp_password") if not cfg.get(k)]
    if missing:
        print(f"creds missing: {missing}", file=sys.stderr)
        print(f"  expected file: {CRED_PATH}", file=sys.stderr)
        print(
            "  or set: TRADEPRO_SMTP_HOST / TRADEPRO_SMTP_USER / "
            "TRADEPRO_SMTP_PASSWORD",
            file=sys.stderr,
        )
        return 2
    print(
        f"probing {cfg['smtp_host']}:{cfg['smtp_port']} "
        f"as {cfg['smtp_user']}…"
    )
    ctx = ssl.create_default_context()
    try:
        if cfg["smtp_port"] == 465:
            with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx, timeout=15) as s:
                s.login(cfg["smtp_user"], cfg["smtp_password"])
        else:
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as s:
                s.starttls(context=ctx)
                s.login(cfg["smtp_user"], cfg["smtp_password"])
    except smtplib.SMTPAuthenticationError as e:
        print(f"AUTH FAILED: {e}", file=sys.stderr)
        if cfg["smtp_host"].endswith("gmail.com"):
            print(
                "  Gmail rejects regular passwords on SMTP. Did you use\n"
                "  a 16-char App Password? Run with --setup-gmail to\n"
                "  regenerate, or visit\n"
                "  https://myaccount.google.com/apppasswords",
                file=sys.stderr,
            )
        return 1
    except (OSError, smtplib.SMTPException) as e:
        print(f"SMTP error: {e}", file=sys.stderr)
        return 1
    print("OK — SMTP login succeeded.")
    return 0


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

    # Attach the PDF deep-dive when build_digest produced one.
    # Lazy attribute — older callers that built the digest before the
    # PDF feature shipped won't have `pdf_bytes` and we just skip.
    pdf_bytes = getattr(digest, "pdf_bytes", None)
    if pdf_bytes:
        from datetime import datetime as _dt
        date_iso = _dt.utcnow().date().isoformat()
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"tradepro-digest-{date_iso}.pdf",
        )

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

    # Setup wizard — skip everything below; just write creds and exit.
    if args.setup_gmail:
        run_setup_gmail()
        return

    # Cred probe — short-circuit before hitting the API. Useful right
    # after running --setup-gmail to confirm the password Google
    # showed you actually works against smtp.gmail.com.
    if args.check_creds:
        cfg = load_smtp_creds(args)
        sys.exit(check_smtp_creds(cfg))

    payloads = fetch_payloads(args.api_base, args.api_token)
    holdings, portfolio_mode = fetch_holdings(args.api_base, args.api_token)
    digest = build_digest(
        payloads, holdings=holdings, portfolio_mode=portfolio_mode,
    )
    cfg = load_smtp_creds(args)

    recipients = cfg.get("to") or []

    # Save the HTML body to disk on demand. Useful when the active
    # transport (Outlook AppleScript) only carries plain text but
    # the user wants the full coloured/tabled rendering — open the
    # saved file in a browser for the rich view.
    if args.save_html:
        html_path = os.path.expanduser(args.save_html)
        os.makedirs(os.path.dirname(html_path) or ".", exist_ok=True)
        with open(html_path, "w") as f:
            f.write(digest.html_body)
        print(f"saved HTML: file://{html_path}")

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
