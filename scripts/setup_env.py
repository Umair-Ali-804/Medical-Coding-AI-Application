"""Create a ready-to-run .env for LOCAL use (stdlib only; works on Windows, macOS, Linux).

    python scripts/setup_env.py                      # asks for your OpenRouter key (Enter = run without LLM)
    python scripts/setup_env.py --openrouter-key sk-or-...  --admin-email you@example.com
    python scripts/setup_env.py --force              # overwrite an existing .env

Server (HTTPS via Caddy, secure cookies, production checks on):
    python scripts/setup_env.py --domain coding.example.org --n8n-domain n8n.example.org --admin-email you@org
    # no domain? use the server IP with sslip.io: --domain 203-0-113-7.sslip.io --n8n-domain n8n.203-0-113-7.sslip.io
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--openrouter-key", default=None)
    p.add_argument("--admin-email", default="admin@example.com")
    p.add_argument("--force", action="store_true")
    p.add_argument("--domain", default=None, help="public domain of the app (production mode, HTTPS)")
    p.add_argument("--n8n-domain", default=None, help="public domain for n8n (default: n8n.<domain>)")
    a = p.parse_args()

    target = ROOT / ".env"
    if target.exists() and not a.force:
        sys.exit(".env already exists. Use --force to overwrite it.")

    key = a.openrouter_key
    if key is None:
        try:
            key = input("OpenRouter API key (press Enter to run without an LLM for now): ").strip()
        except EOFError:
            key = ""

    admin_password = secrets.token_urlsafe(12) + "Aa1!"
    prod = bool(a.domain)
    n8n_domain = a.n8n_domain or (f"n8n.{a.domain}" if prod else None)
    values = {
        "ENVIRONMENT": "production" if prod else "development",
        "CORS_ORIGINS": f"https://{a.domain}" if prod else "http://localhost:3000",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "BOOTSTRAP_ADMIN_EMAIL": a.admin_email,
        "BOOTSTRAP_ADMIN_PASSWORD": admin_password,
        "LLM_PROVIDER": "openrouter" if key else "heuristic",
        "OPENROUTER_API_KEY": key,
        "WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "N8N_DB_PASSWORD": secrets.token_urlsafe(24),
        "N8N_ENCRYPTION_KEY": secrets.token_urlsafe(32),
        "N8N_PUBLIC_URL": f"https://{n8n_domain}/" if prod else "http://localhost:5678/",
    }
    if prod:
        values.update({"APP_DOMAIN": a.domain, "N8N_DOMAIN": n8n_domain, "N8N_HOST": n8n_domain,
                       "N8N_PROTOCOL": "https", "N8N_SECURE_COOKIE": "true", "LOG_JSON": "true"})
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    out, seen = [], set()
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    out.append("")
    if not prod:
        out.append("# --- local-only settings (added by scripts/setup_env.py) ---")
        out.append("COOKIE_INSECURE=1")
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    # newline="\n" keeps Unix line endings even on Windows
    target.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")

    print("\nCreated .env")
    print(f"  LLM mode      : {'OpenRouter' if key else 'no LLM (heuristic) - add OPENROUTER_API_KEY later'}")
    print(f"  Admin login   : {a.admin_email}")
    print(f"  Admin password: {admin_password}")
    if prod:
        print(f"  App URL       : https://{a.domain}")
        print("\nSave the password now. Next:")
        print("  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build")
    else:
        print("\nSave the password now. Next: docker compose up -d --build")


if __name__ == "__main__":
    main()
