import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from fastapi import Request, HTTPException, status

SECRET_FILE = Path(__file__).resolve().parent.parent / ".secret_key"


def get_or_create_secret_key() -> bytes:
    env_key = os.environ.get("SKYPULSE_SECRET_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode("utf-8")).digest()
    if SECRET_FILE.exists():
        raw = SECRET_FILE.read_text(encoding="utf-8").strip()
        if raw:
            return hashlib.sha256(raw.encode("utf-8")).digest()
    generated = secrets.token_urlsafe(48)
    try:
        SECRET_FILE.write_text(generated, encoding="utf-8")
    except Exception:
        pass
    return hashlib.sha256(generated.encode("utf-8")).digest()


MASTER_KEY: bytes = get_or_create_secret_key()


# --- Password Hashing (PBKDF2-HMAC-SHA256, 200k iterations) ---
def hash_access_code(code: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_access_code(code: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
            return False
        salt = parts[1]
        expected = hash_access_code(code, salt)
        return hmac.compare_digest(expected, stored_hash)
    except Exception:
        return False


# --- Signed Session Tokens (HMAC-SHA256) ---
def create_session_token(nick: str, role: str = "member", ttl_seconds: int = 86400 * 14) -> str:
    payload = {
        "nick": nick.strip()[:30] or "Znajomy",
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
        "jti": secrets.token_hex(8),
    }
    raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    sig = hmac.new(MASTER_KEY, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        b64_payload, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(MASTER_KEY, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = "=" * (-len(b64_payload) % 4)
        raw_json = base64.urlsafe_b64decode((b64_payload + padding).encode("ascii"))
        payload = json.loads(raw_json.decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# --- Authenticated Encryption at Rest for Secrets (Webhook URLs, API Tokens) ---
def _keystream(nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            MASTER_KEY,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    if plaintext.startswith("enc$"):
        return plaintext
    data = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(16)
    stream = _keystream(nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    mac = hmac.new(MASTER_KEY, nonce + ciphertext, hashlib.sha256).hexdigest()[:32]
    b64 = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return f"enc${b64}${mac}"


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith("enc$"):
        return stored
    try:
        _, b64, mac = stored.split("$", 2)
        raw = base64.urlsafe_b64decode(b64.encode("ascii"))
        nonce, ciphertext = raw[:16], raw[16:]
        expected_mac = hmac.new(MASTER_KEY, nonce + ciphertext, hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, expected_mac):
            return ""
        stream = _keystream(nonce, len(ciphertext))
        plain = bytes(a ^ b for a, b in zip(ciphertext, stream))
        return plain.decode("utf-8")
    except Exception:
        return ""


def mask_secret(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 12:
        return "••••••••"
    return f"{val[:14]}••••••••{val[-4:]}"


# --- Rate Limiting & Brute-Force Protection ---
class RateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, list[float]] = defaultdict(list)
        self._failed_logins: Dict[str, list[float]] = defaultdict(list)

    def _clean(self, bucket: list[float], window: float) -> None:
        cutoff = time.time() - window
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)

    def check_rate_limit(self, key: str, max_requests: int = 60, window_seconds: float = 60.0) -> Tuple[bool, int]:
        bucket = self._hits[key]
        self._clean(bucket, window_seconds)
        if len(bucket) >= max_requests:
            retry_after = int(window_seconds - (time.time() - bucket[0])) + 1
            return False, max(1, retry_after)
        bucket.append(time.time())
        return True, 0

    def is_login_locked(self, ip: str) -> Tuple[bool, int]:
        bucket = self._failed_logins[ip]
        self._clean(bucket, 300.0)  # 5 minutes lockout window
        if len(bucket) >= 6:
            retry_after = int(300.0 - (time.time() - bucket[0])) + 1
            return True, max(1, retry_after)
        return False, 0

    def record_failed_login(self, ip: str) -> None:
        self._failed_logins[ip].append(time.time())

    def clear_failed_logins(self, ip: str) -> None:
        self._failed_logins.pop(ip, None)


rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"
