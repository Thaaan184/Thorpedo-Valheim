import os
import hmac
import hashlib
import secrets
import json
import time
from typing import Optional

CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")

def get_or_create_secret_key() -> str:
    if os.path.exists(SECRET_KEY_FILE):
        try:
            with open(SECRET_KEY_FILE, "r") as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception:
            pass
    key = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, "w") as f:
            f.write(key)
        os.chmod(SECRET_KEY_FILE, 0o600)
    except Exception:
        pass
    return key

SECRET_KEY = os.getenv("PANEL_SECRET_KEY") or get_or_create_secret_key()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    kdf = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"pbkdf2:sha256:100000${salt}${kdf.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        parts = hashed.split("$")
        if len(parts) != 3:
            return False
        algorithm_info, salt, stored_hash = parts
        _, _, iterations = algorithm_info.split(":")
        kdf = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return hmac.compare_digest(kdf.hex(), stored_hash)
    except Exception:
        return False

def init_credentials():
    env_user = os.getenv("PANEL_ADMIN_USER", "admin")
    env_pass = os.getenv("PANEL_ADMIN_PASSWORD", "ThorpedoAdmin2026!")
    if not os.path.exists(CREDENTIALS_FILE):
        data = {
            "username": env_user,
            "password_hash": hash_password(env_pass),
            "updated_at": int(time.time())
        }
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(CREDENTIALS_FILE, 0o600)

def get_admin_user() -> dict:
    init_credentials()
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"username": "admin", "password_hash": hash_password("ThorpedoAdmin2026!")}

def update_admin_credentials(username: str, password: Optional[str] = None):
    curr = get_admin_user()
    curr["username"] = username
    if password:
        curr["password_hash"] = hash_password(password)
    curr["updated_at"] = int(time.time())
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(curr, f, indent=2)

def create_session_token(username: str, max_age_seconds: int = 86400 * 7) -> str:
    exp = int(time.time()) + max_age_seconds
    payload = f"{username}:{exp}"
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def verify_session_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        username, exp_str, signature = parts
        exp = int(exp_str)
        if time.time() > exp:
            return None
        payload = f"{username}:{exp_str}"
        expected = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return username
    except Exception:
        pass
    return None
