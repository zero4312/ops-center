"""ops-center 数据模型（SQLAlchemy 2.0 声明式）。

设计要点：
- 类型均使用通用类型（String / Text / JSON / DateTime），MySQL 与 SQLite 兼容。
- 资源表以 (account_id, resource_id) 唯一，保证重复同步不会产生脏数据。
- 资源归属应用：auto_app_id（按实例名解析）+ manual_app_id（手工绑定，优先级更高）。
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
class UserRole(str, enum.Enum):
    ADMIN = "admin"        # 管理员：全部权限，含账号与用户管理
    OPERATOR = "operator"  # 运维：可执行开关机、管理定时策略
    READONLY = "readonly"  # 只读：仅查看


class CloudProvider(str, enum.Enum):
    ALIBABA = "alibaba"
    VOLCENGINE = "volcengine"


class ResourceType(str, enum.Enum):
    ECS = "ECS"
    RDS = "RDS"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# 1. 用户
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(20), default=UserRole.READONLY.value, nullable=False)
    email: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value


# ---------------------------------------------------------------------------
# 2. 云账号（AK 加密存储）
# ---------------------------------------------------------------------------
class CloudAccount(Base):
    __tablename__ = "cloud_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="alibaba | volcengine")
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    access_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    access_key_secret_enc: Mapped[str] = mapped_column(Text, nullable=False, comment="Fernet 加密后的 SK")
    # 可选：按 VPC 限定纳管范围（对齐 stg-patrol 的识别方式）
    vpc_ids: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="限定 VPC 列表，为空表示不限")
    remark: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 接入自检结果
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_msg: Mapped[str] = mapped_column(Text, default="")
    # 最近一次同步
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_msg: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    resources: Mapped[list["Resource"]] = relationship(back_populates="account", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 3. 应用（资源分组维度）
# ---------------------------------------------------------------------------
class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名，如 APC-ACE")
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="匹配码，如 ACE")
    owner: Mapped[str] = mapped_column(String(64), default="", comment="应用负责人")
    remark: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 4. 资源
# ---------------------------------------------------------------------------
class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("account_id", "resource_id", name="uq_account_resource"),
        Index("ix_resources_app", "effective_app_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 归属
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id"), nullable=False)
    auto_app_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    manual_app_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    effective_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True,
                                                         comment="冗余列=manual_app_id or auto_app_id，便于查询")

    # 标识
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="云上实例 ID")
    resource_name: Mapped[str] = mapped_column(String(255), default="", comment="实例名称")
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="ECS | RDS")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(64), default="")
    zone: Mapped[str] = mapped_column(String(64), default="")

    # 状态与属性
    status: Mapped[str] = mapped_column(String(32), default="unknown", comment="running | stopped | ...")
    env: Mapped[str] = mapped_column(String(32), default="", comment="STG | DEV | ...")
    spec: Mapped[str] = mapped_column(String(128), default="", comment="云厂商规格代码（如 ecs.c6.large / rds.mysql.1c2g）")
    engine_version: Mapped[str] = mapped_column(String(64), default="", comment="引擎版本（RDS 专用，如 MySQL 8.0）")
    cpu: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="CPU 核数（由规格换算）")
    memory_gb: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="内存 GB（由规格换算）")
    charge_type: Mapped[str] = mapped_column(String(32), default="", comment="PrePaid | PostPaid")
    private_ip: Mapped[str] = mapped_column(String(128), default="")
    vpc_id: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 停机可省标记（成本优化关键）
    stop_saving: Mapped[bool | None] = mapped_column(Boolean, nullable=True,
                                                     comment="True=停机可省 / False=停机不省 / None=未知")
    stop_saving_reason: Mapped[str] = mapped_column(String(255), default="")

    # 纳管控制
    managed: Mapped[bool] = mapped_column(Boolean, default=True, comment="False=排除出开关机范围")
    deleted_on_cloud: Mapped[bool] = mapped_column(Boolean, default=False, comment="云上已释放")

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    account: Mapped["CloudAccount"] = relationship(back_populates="resources")
    auto_app: Mapped["Application | None"] = relationship(foreign_keys=[auto_app_id])
    manual_app: Mapped["Application | None"] = relationship(foreign_keys=[manual_app_id])

    @property
    def power_state(self) -> str:
        """归一化为 running / stopped / other。"""
        s = (self.status or "").lower()
        if s in ("running", "starting", "pending"):
            return "running"
        if s in ("stopped", "stopping", "shutdown"):
            return "stopped"
        return "other"


# ---------------------------------------------------------------------------
# 5. 操作任务 / 6. 任务明细
# ---------------------------------------------------------------------------
class OperationTask(Base):
    __tablename__ = "operation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="start | stop")
    scope: Mapped[str] = mapped_column(String(16), default="resource", comment="app | resource | custom")
    target_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", comment="manual | schedule")
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    operator: Mapped[str] = mapped_column(String(64), default="system")
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING.value)
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    items: Mapped[list["TaskItem"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskItem(Base):
    __tablename__ = "task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("operation_tasks.id"), nullable=False)
    resource_id: Mapped[int | None] = mapped_column(ForeignKey("resources.id"), nullable=True)
    # 冗余快照，避免资源被删后任务历史不可读
    cloud_resource_id: Mapped[str] = mapped_column(String(128), default="")
    resource_name: Mapped[str] = mapped_column(String(255), default="")
    resource_type: Mapped[str] = mapped_column(String(16), default="")
    account_name: Mapped[str] = mapped_column(String(128), default="")

    status: Mapped[str] = mapped_column(String(16), default=ItemStatus.PENDING.value)
    message: Mapped[str] = mapped_column(Text, default="")
    request_id: Mapped[str] = mapped_column(String(128), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["OperationTask"] = relationship(back_populates="items")


# ---------------------------------------------------------------------------
# 7. 定时策略
# ---------------------------------------------------------------------------
class SchedulePolicy(Base):
    __tablename__ = "schedule_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="start | stop")
    scope: Mapped[str] = mapped_column(String(16), default="app", comment="app | resource")
    target_app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_resource_ids: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="scope=resource 时的资源 ID 列表")

    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False, comment="5 段标准 cron")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="策略失效时间，为空表示长期有效")

    # 顺序控制：开机 RDS→ECS；关机 ECS→RDS
    ordered: Mapped[bool] = mapped_column(Boolean, default=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="")
    last_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    remark: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 8. 定时执行日志
# ---------------------------------------------------------------------------
class ScheduleLog(Base):
    __tablename__ = "schedule_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_name: Mapped[str] = mapped_column(String(128), default="")
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# 9. 审计日志
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), default="", comment="login / start / stop / sync / create_policy ...")
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(16), default="success")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=True, index=True)
