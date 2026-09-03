"""定时开关机策略。

定时任务存储在平台数据库（schedule_policies），由平台调度器直接调用云 API 执行，
阿里云与火山引擎行为一致，不依赖云厂商的编排产品。

cron 说明：标准 5 段（分 时 日 月 周），默认时区 Asia/Shanghai。
  例：0 9 * * 1-5  表示工作日每天 09:00
      0 20 * * *   表示每天 20:00
"""
from __future__ import annotations

from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.models import Application, Resource, ScheduleLog, SchedulePolicy
from ..services.executor import write_audit
from ..services.scheduler import sync_policy_jobs, trigger_policy_now
from .deps import client_ip, require_operator, require_readonly

router = APIRouter(prefix="/api/schedules", tags=["定时策略"])


class PolicyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., description="start | stop")
    scope: str = Field("app", description="app | resource")
    target_app_id: int | None = None
    target_resource_ids: list[int] | None = None
    cron_expr: str = Field(..., description="5 段 cron：分 时 日 月 周")
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    end_date: datetime | None = None
    ordered: bool = True
    remark: str = ""


class PolicyUpdate(BaseModel):
    name: str | None = None
    action: str | None = None
    scope: str | None = None
    target_app_id: int | None = None
    target_resource_ids: list[int] | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    end_date: datetime | None = None
    ordered: bool | None = None
    remark: str | None = None


def _validate_cron(expr: str, tz: str) -> None:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise HTTPException(400, "cron 表达式必须为 5 段：分 时 日 月 周")
    try:
        CronTrigger(minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4], timezone=tz or "Asia/Shanghai")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"cron 表达式非法：{exc}") from exc


def _policy_out(db: Session, p: SchedulePolicy) -> dict:
    target_desc = ""
    if p.scope == "app" and p.target_app_id:
        app = db.get(Application, p.target_app_id)
        target_desc = app.name if app else f"app#{p.target_app_id}"
    elif p.scope == "resource":
        ids = p.target_resource_ids or []
        target_desc = f"{len(ids)} 个指定资源"

    return {
        "id": p.id,
        "name": p.name,
        "action": p.action,
        "action_label": "开机" if p.action == "start" else "关机",
        "scope": p.scope,
        "target_app_id": p.target_app_id,
        "target_resource_ids": p.target_resource_ids,
        "target_desc": target_desc,
        "cron_expr": p.cron_expr,
        "timezone": p.timezone,
        "enabled": p.enabled,
        "end_date": p.end_date,
        "ordered": p.ordered,
        "last_run_at": p.last_run_at,
        "next_run_at": p.next_run_at,
        "last_status": p.last_status,
        "last_task_id": p.last_task_id,
        "remark": p.remark,
        "created_by": p.created_by,
        "created_at": p.created_at,
    }


@router.get("")
def list_policies(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    rows = db.scalars(select(SchedulePolicy).order_by(desc(SchedulePolicy.id))).all()
    return {"items": [_policy_out(db, p) for p in rows]}


@router.post("", status_code=201)
def create_policy(body: PolicyIn, request: Request,
                  db: Session = Depends(get_db), user=Depends(require_operator)):
    if body.action not in ("start", "stop"):
        raise HTTPException(400, "action 只能是 start 或 stop")
    _validate_cron(body.cron_expr, body.timezone)

    if body.scope == "app" and not body.target_app_id:
        raise HTTPException(400, "scope=app 时必须指定 target_app_id")
    if body.scope == "resource" and not body.target_resource_ids:
        raise HTTPException(400, "scope=resource 时必须指定 target_resource_ids")

    p = SchedulePolicy(**body.model_dump(), created_by=user.username)
    db.add(p)
    db.commit()

    sync_policy_jobs()
    write_audit(db, user.username, "create_policy", p.name,
                f"{body.action} @ {body.cron_expr}", client_ip(request))
    return {"id": p.id, "message": "定时策略已创建"}


@router.put("/{policy_id}")
def update_policy(policy_id: int, body: PolicyUpdate, request: Request,
                  db: Session = Depends(get_db), user=Depends(require_operator)):
    p = db.get(SchedulePolicy, policy_id)
    if p is None:
        raise HTTPException(404, "策略不存在")

    data = body.model_dump(exclude_unset=True)
    if "cron_expr" in data or "timezone" in data:
        _validate_cron(data.get("cron_expr", p.cron_expr),
                       data.get("timezone", p.timezone))

    for k, v in data.items():
        setattr(p, k, v)
    db.commit()

    sync_policy_jobs()
    write_audit(db, user.username, "update_policy", p.name, "", client_ip(request))
    return {"message": "已更新"}


@router.delete("/{policy_id}")
def delete_policy(policy_id: int, request: Request,
                  db: Session = Depends(get_db), user=Depends(require_operator)):
    p = db.get(SchedulePolicy, policy_id)
    if p is None:
        raise HTTPException(404, "策略不存在")
    name = p.name
    db.delete(p)
    db.commit()

    sync_policy_jobs()
    write_audit(db, user.username, "delete_policy", name, "", client_ip(request))
    return {"message": f"已删除策略 {name}"}


@router.post("/{policy_id}/toggle")
def toggle_policy(policy_id: int, request: Request,
                  db: Session = Depends(get_db), user=Depends(require_operator)):
    p = db.get(SchedulePolicy, policy_id)
    if p is None:
        raise HTTPException(404, "策略不存在")
    p.enabled = not p.enabled
    db.commit()

    sync_policy_jobs()
    write_audit(db, user.username, "toggle_policy", p.name,
                "启用" if p.enabled else "停用", client_ip(request))
    return {"enabled": p.enabled, "message": "已" + ("启用" if p.enabled else "停用")}


@router.post("/{policy_id}/run")
def run_policy_now(policy_id: int, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_operator)):
    """立即执行一次（不改变原调度计划）。"""
    p = db.get(SchedulePolicy, policy_id)
    if p is None:
        raise HTTPException(404, "策略不存在")

    task_id = trigger_policy_now(policy_id)
    if task_id is None:
        raise HTTPException(400, "策略未匹配到可操作资源")

    write_audit(db, user.username, "run_policy_now", p.name,
                f"生成任务 #{task_id}", client_ip(request))
    return {"task_id": task_id, "message": "已立即触发"}


@router.get("/logs")
def list_logs(limit: int = Query(100, ge=1, le=1000),
              db: Session = Depends(get_db), _u=Depends(require_readonly)):
    rows = db.scalars(select(ScheduleLog).order_by(desc(ScheduleLog.id)).limit(limit)).all()
    return {"items": [{
        "id": r.id, "policy_id": r.policy_id, "policy_name": r.policy_name,
        "task_id": r.task_id, "fired_at": r.fired_at, "status": r.status,
        "total": r.total, "succeed": r.succeed, "failed": r.failed,
        "skipped": r.skipped, "message": r.message,
    } for r in rows]}
