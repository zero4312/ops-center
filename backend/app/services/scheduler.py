"""定时调度服务（APScheduler）。

设计要点：
- 定时任务存储在平台数据库（schedule_policies 表 + APScheduler 的 job 表）；
- 阿里云与火山引擎统一由平台直接调用云 API 执行，不依赖云厂商编排产品，
  两个云行为完全一致，运维无感知；
- 单实例部署，APScheduler 与 FastAPI 同进程。**uvicorn --workers 必须为 1**，
  否则每个 worker 各起一份调度器，任务会被重复执行；
- misfire_grace_time=3600：服务重启期间错过的任务，在 1 小时内补执行一次。
"""
from __future__ import annotations

import atexit
import logging
from datetime import datetime, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from ..core.config import settings
from ..core.database import SessionLocal, engine
from ..models.models import (
    Application, OperationTask, Resource, SchedulePolicy, ScheduleLog, utcnow,
)

logger = logging.getLogger("opscenter.scheduler")

_scheduler: BackgroundScheduler | None = None

JOB_PREFIX = "policy_"
SYNC_JOB_ID = "resource_auto_sync"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 任务执行体
# ---------------------------------------------------------------------------
def _run_policy(policy_id: int) -> None:
    """定时策略触发：组装资源清单并执行开关机。"""
    from .executor import create_task  # 延迟导入，避免循环依赖

    db: Session = SessionLocal()
    try:
        policy = db.get(SchedulePolicy, policy_id)
        if policy is None or not policy.enabled:
            logger.warning("策略 %s 不存在或已禁用，跳过", policy_id)
            return

        # 策略失效时间检查
        if policy.end_date and datetime.utcnow() > policy.end_date:
            logger.warning("策略 %s 已过失效时间，跳过并自动停用", policy.name)
            policy.enabled = False
            db.commit()
            return

        resources = _resolve_policy_resources(db, policy)
        if not resources:
            logger.info("策略 %s 命中 0 个资源，跳过", policy.name)
            return

        create_task(
            db=db,
            action=policy.action,
            resources=resources,
            operator=f"schedule:{policy.name}",
            trigger="schedule",
            policy_id=policy.id,
            target_app_id=policy.target_app_id,
            ordered=policy.ordered,
        )
        logger.info("策略 %s 已触发：%s %d 个资源", policy.name, policy.action, len(resources))
    except Exception as exc:  # noqa: BLE001
        logger.exception("定时策略 %s 执行异常：%s", policy_id, exc)
    finally:
        db.close()


def _resolve_policy_resources(db, policy: SchedulePolicy) -> list[Resource]:
    """按策略范围解析出资源清单。"""
    if policy.scope == "app" and policy.target_app_id:
        rows = db.scalars(
            select(Resource).where(
                Resource.effective_app_id == policy.target_app_id,
                Resource.managed.is_(True),
                Resource.deleted_on_cloud.is_(False),
            )
        ).all()
    elif policy.scope == "resource" and policy.target_resource_ids:
        rows = db.scalars(
            select(Resource).where(
                Resource.id.in_(policy.target_resource_ids),
                Resource.managed.is_(True),
                Resource.deleted_on_cloud.is_(False),
            )
        ).all()
    else:
        rows = []
    return list(rows)


def _run_auto_sync() -> None:
    """资源自动同步。"""
    from .sync import sync_all

    db: Session = SessionLocal()
    try:
        results = sync_all(db)
        ok = sum(1 for r in results if r["ok"])
        logger.info("自动同步完成：%d/%d 个账号成功", ok, len(results))
    except Exception as exc:  # noqa: BLE001
        logger.exception("自动同步异常：%s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 调度器生命周期
# ---------------------------------------------------------------------------
def _parse_cron(expr: str, tz: str) -> CronTrigger:
    """解析 5 段 cron（分 时 日 月 周）。"""
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式必须是 5 段（分 时 日 月 周），当前为：{expr}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute, hour=hour, day=day, month=month,
        day_of_week=day_of_week, timezone=tz or "Asia/Shanghai",
    )


def sync_policy_jobs() -> None:
    """把数据库中的启用策略同步到调度器（新增/更新/删除）。"""
    sched = _scheduler
    if sched is None:
        return

    db: Session = SessionLocal()
    try:
        policies = db.scalars(select(SchedulePolicy)).all()
        desired: dict[str, SchedulePolicy] = {}
        for p in policies:
            if p.enabled:
                desired[f"{JOB_PREFIX}{p.id}"] = p

        existing = {job.id for job in sched.get_jobs() if str(job.id).startswith(JOB_PREFIX)}

        # 删除多余
        for job_id in existing - set(desired):
            sched.remove_job(job_id)

        # 新增 / 更新
        for job_id, p in desired.items():
            try:
                trigger = _parse_cron(p.cron_expr, p.timezone)
            except Exception as exc:  # noqa: BLE001
                logger.error("策略 %s 的 cron 表达式非法（%s）：%s", p.name, p.cron_expr, exc)
                continue

            sched.add_job(
                _run_policy,
                trigger=trigger,
                id=job_id,
                args=[p.id],
                name=f"[{p.action}] {p.name}",
                replace_existing=True,
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
                end_date=p.end_date,
            )

            # 回写下次执行时间（统一转为 UTC naive 落库）
            job = sched.get_job(job_id)
            if job and job.next_run_time:
                p.next_run_at = job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None)
        # 停用的策略清空 next_run_at
        for p in policies:
            if not p.enabled:
                p.next_run_at = None
        db.commit()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    """启动调度器（幂等）。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    jobstores = {"default": SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs")}
    _scheduler = BackgroundScheduler(
        jobstores=jobstores,
        timezone="Asia/Shanghai",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )

    try:
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001
        logger.error("调度器启动失败：%s", exc)
        return None

    # 注册资源自动同步
    try:
        trigger = _parse_cron(settings.SYNC_CRON, "Asia/Shanghai")
        _scheduler.add_job(
            _run_auto_sync, trigger=trigger, id=SYNC_JOB_ID,
            name="资源自动同步", replace_existing=True,
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("注册自动同步任务失败（cron=%s）：%s", settings.SYNC_CRON, exc)

    # 加载定时策略
    sync_policy_jobs()

    atexit.register(shutdown_scheduler)
    logger.info("调度器已启动（时区 Asia/Shanghai）")
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
    _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def trigger_policy_now(policy_id: int) -> int | None:
    """立即执行一次策略，返回新建的 task_id。"""
    from .executor import create_task

    db: Session = SessionLocal()
    try:
        policy = db.get(SchedulePolicy, policy_id)
        if policy is None:
            return None
        resources = _resolve_policy_resources(db, policy)
        if not resources:
            return None
        task = create_task(
            db=db, action=policy.action, resources=resources,
            operator=f"manual:{policy.name}", trigger="schedule",
            policy_id=policy.id, target_app_id=policy.target_app_id,
            ordered=policy.ordered,
        )
        return task.id
    finally:
        db.close()
