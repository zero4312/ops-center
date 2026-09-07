"""ops-center 运维中台 - 服务入口。

部署注意：uvicorn 必须以 --workers 1 启动。
APScheduler 运行在 FastAPI 进程内，多 worker 会导致定时任务被重复执行。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.database import Base, engine
from .api import accounts, apps, auth, operations, resources, schedules, users, settings as settings_api
from .models import models  # noqa: F401  确保模型被注册
from .services import scheduler as scheduler_svc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("opscenter")


def _apply_timestamp_format() -> None:
    """强制 uvicorn 自身的 access/error 日志也带时间戳。

    uvicorn 在导入本模块后会用自己的默认格式（'INFO: <ip> - ...' / 'ERROR: ...'）
    覆盖 root logger，导致日志没有时间。此处在其配置完成后，把各 handler 的
    formatter 换成带 asctime 的格式，保证所有日志行都有时间戳，便于排查。
    """
    ts_format = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.setFormatter(ts_format)


# ---------------------------------------------------------------------------
# 启动初始化
# ---------------------------------------------------------------------------
def init_db_and_admin() -> None:
    """建表 + 存量表结构升级（幂等）+ 初始化管理员账号。"""
    Base.metadata.create_all(bind=engine)

    # 模型新增列后，create_all 不会变更已存在的表，这里补 ALTER（幂等）。
    # 这样部署/重启时自动修复缺失列，避免线上因漏跑迁移而 500。
    try:
        from .scripts.init_db import run_column_migrations

        added = run_column_migrations(engine)
        for col in added:
            logger.info("已升级表结构：新增列 %s", col)
    except Exception as exc:  # noqa: BLE001
        logger.warning("存量表结构升级失败（可稍后手动执行 db-init）：%s", exc)

    from sqlalchemy.orm import Session

    from .core.database import SessionLocal
    from .core.security import hash_password
    from .models.models import User

    db: Session = SessionLocal()
    try:
        from sqlalchemy import select
        exists = db.scalar(select(User).where(User.username == settings.ADMIN_USERNAME))
        if exists is None:
            admin = User(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                full_name="系统管理员",
                role="admin",
            )
            db.add(admin)
            db.commit()
            logger.info("已初始化管理员账号：%s / %s",
                        settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    except Exception as exc:  # noqa: BLE001
        logger.warning("初始化管理员失败（数据库可能未就绪）：%s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn 此时已完成自身日志配置，覆盖其 handler 的 formatter 以带上时间戳
    _apply_timestamp_format()
    logger.info("ops-center 启动中...（数据库：%s）",
                "SQLite" if settings.is_sqlite else "MySQL")
    init_db_and_admin()

    # 启动调度器（定时开关机 + 资源自动同步）
    try:
        scheduler_svc.start_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("调度器启动失败：%s", exc)

    logger.info("ops-center 已就绪：http://%s:%s", settings.HOST, settings.PORT)
    yield

    scheduler_svc.shutdown_scheduler()
    logger.info("ops-center 已停止")


app = FastAPI(
    title="ops-center 运维中台",
    description="多雲账号 ECS/RDS 统一纳管、一键开关机与定时开关机",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS：便于前后端分离部署与本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ops-center"}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(apps.router)
app.include_router(resources.router)
app.include_router(operations.router)
app.include_router(schedules.router)
app.include_router(users.router)
app.include_router(settings_api.router)


# ---------------------------------------------------------------------------
# 统一异常处理
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("未捕获异常：%s %s", request.url, exc)
    return JSONResponse(status_code=500, content={"detail": f"服务内部错误：{exc}"})


# ---------------------------------------------------------------------------
# 前端静态资源（构建产物由后端统一托管，部署只需启一个服务）
# ---------------------------------------------------------------------------
STATIC_DIR = settings.STATIC_DIR
INDEX_FILE = STATIC_DIR / "index.html"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(str(INDEX_FILE))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """SPA 路由回退：非 /api、非 /static 的路径一律返回 index.html。"""
        target = STATIC_DIR / full_path
        if full_path.startswith("api/") or full_path.startswith("static/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if target.exists() and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(INDEX_FILE))
else:
    logger.warning("静态目录不存在：%s（前端未构建时仅提供 API）", STATIC_DIR)
