"""SQLAlchemy 引擎与会话工厂。

MySQL / SQLite 双支持：连接串由 OPS_DATABASE_URL 决定。
SQLite 需开启 check_same_thread=False 以支持多线程（FastAPI 线程池 + APScheduler）。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
else:
    # MySQL / RDS：开启连接保活，避免长时间空闲被服务端断开
    _connect_args = {"connect_timeout": 10}

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,          # 每次取连接前探活，自动重连
    pool_recycle=3600,           # 1 小时回收连接，规避 MySQL wait_timeout
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db():
    """FastAPI 依赖：每个请求一个会话，结束后自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
