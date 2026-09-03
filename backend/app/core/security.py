"""认证与鉴权：密码哈希（PBKDF2-SHA256，零第三方依赖）+ JWT。"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_ALGO = "HS256"
_PBKDF2_ITERATIONS = 120_000


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        algo, iterations, salt, hex_dk = hashed.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hex_dk)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(subject: str, role: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.TOKEN_EXPIRE_HOURS),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGO)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# 角色权限
# ---------------------------------------------------------------------------
# 权限从大到小：admin > operator > readonly
ROLE_LEVEL = {"admin": 3, "operator": 2, "readonly": 1}


def role_level(role: str) -> int:
    return ROLE_LEVEL.get(role, 0)


def has_permission(user_role: str, required: str) -> bool:
    """判断 user_role 是否满足 required 权限。"""
    return role_level(user_role) >= role_level(required)
