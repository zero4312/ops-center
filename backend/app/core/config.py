"""全局配置：全部从环境变量读取，.env 由 deploy.sh 加载。"""
import os
from pathlib import Path

# 项目根目录（backend 的上一级）
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class Settings:
    # ---------- 服务 ----------
    HOST: str = _env("OPS_HOST", "0.0.0.0")
    PORT: int = int(_env("OPS_PORT", "8000") or 8000)
    DEBUG: bool = _env("OPS_DEBUG", "false").lower() in ("1", "true", "yes")

    # ---------- 数据库（MySQL / 云上 RDS 同一套连接串，换地址即可） ----------
    # 连接串格式：mysql+pymysql://用户:密码@地址:3306/库名?charset=utf8mb4
    # 本机 Docker 测试用 127.0.0.1；部署到云服务器 + 云上 RDS 时换成 RDS 内网/外网地址即可
    DATABASE_URL: str = _env(
        "OPS_DATABASE_URL",
        "mysql+pymysql://opscenter:opscenter123@127.0.0.1:3306/ops_center?charset=utf8mb4",
    )

    # ---------- 安全 ----------
    SECRET_KEY: str = _env("OPS_SECRET_KEY", "please-change-this-to-a-random-32-bytes-string")
    TOKEN_EXPIRE_HOURS: int = int(_env("OPS_TOKEN_EXPIRE_HOURS", "12") or 12)
    FERNET_KEY: str = _env("OPS_FERNET_KEY", "")

    # ---------- 初始管理员 ----------
    ADMIN_USERNAME: str = _env("OPS_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = _env("OPS_ADMIN_PASSWORD", "Admin@12345")

    # ---------- 调度与执行 ----------
    SYNC_CRON: str = _env("OPS_SYNC_CRON", "30 8 * * *")
    OP_TIMEOUT: int = int(_env("OPS_OP_TIMEOUT", "600") or 600)
    MAX_WORKERS: int = int(_env("OPS_MAX_WORKERS", "8") or 8)

    # ---------- 目录 ----------
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = DATA_DIR
    LOG_DIR: Path = BASE_DIR / "logs"
    STATIC_DIR: Path = Path(__file__).resolve().parents[1] / "static"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


settings = Settings()

# 安全告警：使用默认密钥
if settings.SECRET_KEY.startswith("please-change-this"):
    import warnings
    warnings.warn(
        "OPS_SECRET_KEY 仍为默认值，生产环境请务必修改！可在 .env 中设置。",
        stacklevel=2,
    )
