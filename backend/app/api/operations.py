"""一键开关机 与 任务中心。

开机顺序：RDS -> ECS（数据库先就绪）
关机顺序：ECS -> RDS（先停计算再停库）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.models import (
    Application, ItemStatus, OperationTask, Resource, TaskItem, TaskStatus,
)
from ..services.executor import create_task, write_audit
from .deps import client_ip, require_operator, require_readonly

router = APIRouter(prefix="/api/operations", tags=["开关机"])


class OperateIn(BaseModel):
    action: str = Field(..., description="start | stop")
    app_id: int | None = Field(None, description="按应用整体操作")
    resource_ids: list[int] | None = Field(None, description="指定资源 ID 列表")
    resource_type: str | None = Field(None, description="仅操作 ECS / RDS，为空表示全部")
    ordered: bool = Field(True, description="是否按依赖顺序执行")


def _resolve_resources(db: Session, body: OperateIn) -> list[Resource]:
    if body.app_id:
        app = db.get(Application, body.app_id)
        if app is None:
            raise HTTPException(404, "应用不存在")
        stmt = select(Resource).where(
            Resource.effective_app_id == body.app_id,
            Resource.managed.is_(True),
            Resource.deleted_on_cloud.is_(False),
        )
    elif body.resource_ids:
        stmt = select(Resource).where(
            Resource.id.in_(body.resource_ids),
            Resource.deleted_on_cloud.is_(False),
        )
    else:
        raise HTTPException(400, "必须指定 app_id 或 resource_ids")

    if body.resource_type:
        stmt = stmt.where(Resource.resource_type == body.resource_type.upper())

    return list(db.scalars(stmt).all())


@router.post("/execute")
def execute(body: OperateIn, request: Request,
            db: Session = Depends(get_db), user=Depends(require_operator)):
    """发起开关机操作（异步执行，返回任务 ID，前端轮询任务状态）。"""
    if body.action not in ("start", "stop"):
        raise HTTPException(400, "action 只能是 start 或 stop")

    resources = _resolve_resources(db, body)
    if not resources:
        raise HTTPException(400, "没有匹配到可操作的资源")

    task = create_task(
        db=db,
        action=body.action,
        resources=resources,
        operator=user.username,
        trigger="manual",
        target_app_id=body.app_id,
        ordered=body.ordered,
    )

    action_cn = "开机" if body.action == "start" else "关机"
    target = f"应用#{body.app_id}" if body.app_id else f"{len(resources)} 个资源"
    write_audit(db, user.username, body.action, target,
                f"{action_cn} {len(resources)} 个资源", client_ip(request))

    return {"task_id": task.id, "total": len(resources),
            "message": f"{action_cn}指令已下发，共 {len(resources)} 个资源"}


def _task_out(db: Session, task: OperationTask, with_items: bool = False) -> dict:
    data = {
        "id": task.id,
        "action": task.action,
        "action_label": "开机" if task.action == "start" else "关机",
        "scope": task.scope,
        "trigger": task.trigger,
        "operator": task.operator,
        "status": task.status,
        "total": task.total,
        "succeed": task.succeed,
        "failed": task.failed,
        "skipped": task.skipped,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at,
        "policy_id": task.policy_id,
    }
    if task.target_app_id:
        app = db.get(Application, task.target_app_id)
        data["app_name"] = app.name if app else f"app#{task.target_app_id}"
    if with_items:
        items = db.scalars(
            select(TaskItem).where(TaskItem.task_id == task.id).order_by(TaskItem.id)
        ).all()
        data["items"] = [{
            "id": i.id,
            "resource_name": i.resource_name,
            "cloud_resource_id": i.cloud_resource_id,
            "resource_type": i.resource_type,
            "account_name": i.account_name,
            "status": i.status,
            "message": i.message,
            "request_id": i.request_id,
            "started_at": i.started_at,
            "finished_at": i.finished_at,
        } for i in items]
    return data


@router.get("")
def list_tasks(
    status: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db), _u=Depends(require_readonly),
):
    stmt = select(OperationTask)
    if status:
        stmt = stmt.where(OperationTask.status == status)
    if action:
        stmt = stmt.where(OperationTask.action == action)
    rows = db.scalars(stmt.order_by(desc(OperationTask.id)).limit(limit)).all()
    return {"items": [_task_out(db, t) for t in rows]}


@router.get("/running")
def running_tasks(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    """正在执行中的任务，供前端轮询。"""
    rows = db.scalars(
        select(OperationTask).where(OperationTask.status.in_(
            [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]))
        .order_by(desc(OperationTask.id))
    ).all()
    return {"items": [_task_out(db, t) for t in rows], "count": len(rows)}


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), _u=Depends(require_readonly)):
    task = db.get(OperationTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return _task_out(db, task, with_items=True)


@router.get("/stats/summary")
def task_stats(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    rows = db.execute(
        select(OperationTask.status, func.count(OperationTask.id))
        .group_by(OperationTask.status)
    ).all()
    return {"items": {r[0]: r[1] for r in rows}}
