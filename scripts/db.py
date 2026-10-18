"""Shared connection helper.

Reads DATABASE_URL (Supabase *Session pooler* string) from the environment or a
local .env file. SSL is always required unless DB_SSLMODE says otherwise, which
only the local test harness does.
"""
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()  # local runs read .env; in GitHub Actions the secret is already in the environment


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (put it in .env locally, or a GitHub Secret in CI).")
    return psycopg2.connect(url, sslmode=os.environ.get("DB_SSLMODE", "require"), connect_timeout=15)
