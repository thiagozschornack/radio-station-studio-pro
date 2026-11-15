"""Senhas, tokens, MFA (TOTP), cifragem de segredos e URLs assinadas."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

# ----------------------------------------------------------------- senhas (scrypt)
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def password_problems(password: str) -> list[str]:
    probs = []
    if len(password) < 10:
        probs.append("mínimo de 10 caracteres")
    if password.lower() == password or password.upper() == password:
        probs.append("misture letras maiúsculas e minúsculas")
    if not any(c.isdigit() for c in password):
        probs.append("inclua ao menos um número")
    return probs


# ----------------------------------------------------------------- JWT de acesso
def create_access_token(user_id: str, mfa: bool) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes),
               "typ": "access", "mfa": mfa}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def create_mfa_challenge(user_id: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(minutes=5), "typ": "mfa"},
                      s.secret_key, algorithm="HS256")


def decode_token(token: str, typ: str) -> dict | None:
    try:
        data = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == typ else None


def new_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, sha256_hex(raw)


def sha256_hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode()
    return hashlib.sha256(s).hexdigest()


# ----------------------------------------------------------------- cifragem em repouso
def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


# ----------------------------------------------------------------- TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((for_time if for_time is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + i * 30), code) for i in range(-window, window + 1))


def totp_uri(secret: str, email: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote('RSSP EmpreenderTHZ')}:{quote(email)}?secret={secret}&issuer={quote('EmpreenderTHZ')}"


# ----------------------------------------------------------------- URLs assinadas (pré-escuta)
def sign_media_url(station_id: str, media_id: str, ttl: int = 600) -> str:
    exp = int(time.time()) + ttl
    msg = f"{station_id}:{media_id}:{exp}".encode()
    sig = hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"/api/media-files/{station_id}/{media_id}?exp={exp}&sig={sig}"


def verify_media_sig(station_id: str, media_id: str, exp: str, sig: str) -> bool:
    try:
        if int(exp) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    msg = f"{station_id}:{media_id}:{exp}".encode()
    good = hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig or "")


# ----------------------------------------------------------------- rate limiting
class RateLimiter:
    """Janela deslizante em memória (por processo). Em múltiplas instâncias usar Redis (Fase 4)."""

    def __init__(self):
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 50_000:
            self._hits.clear()
        return True


rate_limiter = RateLimiter()
