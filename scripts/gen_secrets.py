"""Print fresh secrets for .env (stdlib only - no project dependencies needed).

    python3 scripts/gen_secrets.py >> .env   # then remove the empty duplicates above
"""

import base64
import os
import secrets

print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
print(f"ENCRYPTION_KEYS={base64.urlsafe_b64encode(os.urandom(32)).decode()}")  # Fernet key
print(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}")
print(f"N8N_ENCRYPTION_KEY={secrets.token_urlsafe(32)}")
print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}")
print(f"N8N_DB_PASSWORD={secrets.token_urlsafe(24)}")
print(f"BOOTSTRAP_ADMIN_PASSWORD={secrets.token_urlsafe(18)}Aa1!")
