"""One-time OAuth2 setup: store Gmail or Calendar refresh_token in Supabase."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

from src.database import DatabaseManager

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TASKS_SCOPES = ["https://www.googleapis.com/auth/tasks"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OAuth for Gmail, Calendar, or Tasks refresh tokens in Supabase."
    )
    p.add_argument(
        "--type",
        choices=("gmail", "calendar", "tasks"),
        required=True,
        help="gmail: inbox; calendar: ferturferturovich Calendar; tasks: Google Tasks (fertur).",
    )
    p.add_argument(
        "--telegram-id",
        type=int,
        required=True,
        help="user_config.telegram_id — row where the refresh token is stored",
    )
    p.add_argument(
        "--credentials",
        type=Path,
        default=Path("credentials.json"),
        help="Google OAuth client JSON (Desktop app)",
    )
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = _parse_args()
    if not args.credentials.is_file():
        print(f"Missing {args.credentials}", file=sys.stderr)
        sys.exit(1)

    if args.type == "gmail":
        scopes = GMAIL_SCOPES
    elif args.type == "calendar":
        scopes = CALENDAR_SCOPES
    else:
        scopes = TASKS_SCOPES
    flow = InstalledAppFlow.from_client_secrets_file(
        str(args.credentials),
        scopes=scopes,
    )
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )
    if not creds.refresh_token:
        print(
            "No refresh_token returned. Revoke app access in Google Account settings "
            "and retry so consent can issue offline access.",
            file=sys.stderr,
        )
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Set SUPABASE_URL and SUPABASE_KEY (e.g. in .env)", file=sys.stderr)
        sys.exit(1)

    db = DatabaseManager(url=url, key=key)
    if args.type == "gmail":
        db.save_user_config(
            args.telegram_id,
            gmail_refresh_token=creds.refresh_token,
        )
        print(f"Saved gmail_refresh_token for telegram_id={args.telegram_id}")
    elif args.type == "calendar":
        db.save_user_config(
            args.telegram_id,
            calendar_refresh_token=creds.refresh_token,
        )
        print(f"Saved calendar_refresh_token for telegram_id={args.telegram_id}")
    else:
        db.save_user_config(
            args.telegram_id,
            tasks_refresh_token=creds.refresh_token,
        )
        print(f"Saved tasks_refresh_token for telegram_id={args.telegram_id}")


if __name__ == "__main__":
    main()
