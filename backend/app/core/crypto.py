"""云账号 AK/SK 加密存储。

使用 Fernet（AES-128-CBC + HMAC-SHA256）对称加密，主密钥存放于 data/secret.key，
权限 600。若环境变量 OPS_FERNET_KEY 已配置则优先使用，便于多实例共享同一密钥。
"""
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from .config import DATA_DIR, settings

_KEY_FILE: Path = DATA_DIR / "secret.key"


def _load_or_create_key() -> bytes:
    """获取主密钥：环境变量优先，其次密钥文件，都没有则生成并落盘。"""
    if settings.FERNET_KEY:
        return settings.FERNET_KEY.encode("utf-8")

    if _KEY_FILE.exists():
        return _KEY_FILE.read_text(encoding="utf-8").strip().encode("utf-8")

    key = Fernet.generate_key()
    _KEY_FILE.write_text(key.decode("utf-8"), encoding="utf-8")
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    return key


_FERNET = Fernet(_load_or_create_key())


def encrypt_secret(plain: str) -> str:
    """加密字符串，返回 base64 密文。"""
    if plain is None:
        return ""
    return _FERNET.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher: str) -> str:
    """解密字符串；失败返回空串（不抛异常，避免脏数据导致整个页面挂掉）。"""
    if not cipher:
        return ""
    try:
        return _FERNET.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def mask_secret(plain: str, keep: int = 4) -> str:
    """脱敏展示：仅保留末尾 keep 位。"""
    if not plain:
        return ""
    if len(plain) <= keep:
        return "*" * len(plain)
    return "*" * (len(plain) - keep) + plain[-keep:]


def generate_token(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)
