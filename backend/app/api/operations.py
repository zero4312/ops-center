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
from ..services.executor import _now, create_task, write_audit
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

    action_cn = "开机" if body.action == "start" else "节省关机"
    target = f"应用#{body.app_id}" if body.app_id else f"{len(resources)} 个资源"
    write_audit(db, user.username, body.action, target,
                f"{action_cn} {len(resources)} 个资源", client_ip(request))

    return {"task_id": task.id, "total": len(resources),
            "message": f"{action_cn}指令已下发，共 {len(resources)} 个资源"}


def _task_out(db: Session, task: OperationTask, with_items: bool = False) -> dict:
    items = db.scalars(
        select(TaskItem).where(TaskItem.task_id == task.id).order_by(TaskItem.id)
    ).all()
    # 目标实例（快照，来自任务明细，便于资源删除后仍可读）
    target_instances = [{
        "name": i.resource_name,
        "type": i.resource_type,
        "account": i.account_name,
    } for i in items]
    data = {
        "id": task.id,
        "action": task.action,
        "action_label": "开机" if task.action == "start" else "节省关机",
        "scope": task.scope,
        "trigger": task.trigger,
        "operator": task.operator,
        "status": task.status,
        # 会话状态：运行中 / 已关闭（开机会话闭环）
        "session_status": "running" if task.closed_at is None else "closed",
        "total": task.total,
        "succeed": task.succeed,
        "failed": task.failed,
        "skipped": task.skipped,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at,
        "closed_at": task.closed_at,
        "close_task_id": task.close_task_id,
        "policy_id": task.policy_id,
        "target_instances": target_instances,
        "instance_names": ", ".join(i["name"] for i in target_instances),
    }
    if task.target_app_id:
        app = db.get(Application, task.target_app_id)
        data["app_name"] = app.name if app else f"app#{task.target_app_id}"
    if with_items:
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
        # 关联的一键关机任务（归档态下钻看关机结果）
        if task.close_task_id:
            ct = db.get(OperationTask, task.close_task_id)
            if ct:
                citems = db.scalars(
                    select(TaskItem).where(TaskItem.task_id == ct.id).order_by(TaskItem.id)
                ).all()
                data["close_task"] = {
                    "id": ct.id,
                    "status": ct.status,
                    "total": ct.total,
                    "succeed": ct.succeed,
                    "failed": ct.failed,
                    "skipped": ct.skipped,
                    "items": [{
                        "resource_name": ci.resource_name,
                        "resource_type": ci.resource_type,
                        "account_name": ci.account_name,
                        "status": ci.status,
                        "message": ci.message,
                    } for ci in citems],
                }
    return data


@router.get("")
def list_tasks(
    state: str | None = Query(None, description="running=运行中会话 | archived=已归档会话 | 空=全部"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db), _u=Depends(require_readonly),
):
    """任务中心：只展示开机任务（action=start），按 state 拆分运行中/归档。"""
    stmt = select(OperationTask).where(OperationTask.action == "start")
    if state == "running":
        stmt = stmt.where(OperationTask.closed_at.is_(None))
    elif state == "archived":
        stmt = stmt.where(OperationTask.closed_at.isnot(None))
    rows = db.scalars(stmt.order_by(desc(OperationTask.id)).limit(limit)).all()
    return {"items": [_task_out(db, t) for t in rows]}


@router.get("/running")
def running_tasks(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    """正在执行中的任务（status=pending|running），供顶部 banner 轮询。"""
    rows = db.scalars(
        select(OperationTask).where(OperationTask.status.in_(
            [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]))
        .order_by(desc(OperationTask.id))
    ).all()
    return {"items": [_task_out(db, t) for t in rows], "count": len(rows)}


@router.post("/{task_id}/close")
def close_task(
    task_id: int, request: Request,
    db: Session = Depends(get_db), user=Depends(require_operator),
):
    """一键关机：对开机会话下的实例执行节省停机，记录关机时间并归档。

    闭环逻辑：开机 -> 运行中会话 -> 点一键关机 -> 生成 stop 任务(StopCharging)
              -> 写 closed_at/close_task_id -> 移入归档（已关闭）。
    已停止的实例由执行引擎自动 skip，仅对运行中的实例真正下发关机。
    """
    task = db.get(OperationTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.action != "start":
        raise HTTPException(400, "只能对开机任务执行一键关机")
    if task.closed_at is not None:
        raise HTTPException(400, "该开机会话已关机归档")

    # 收集会话下仍存在的资源（资源可能被删除，需跳过）
    item_resource_ids = [i.resource_id for i in task.items if i.resource_id]
    resources = (
        list(db.scalars(select(Resource).where(Resource.id.in_(item_resource_ids))).all())
        if item_resource_ids else []
    )
    if not resources:
        raise HTTPException(400, "该开机会话下无可用资源（可能已被删除），无法关机")

    # 生成节省停机任务（异步执行，留审计）
    stop_task = create_task(
        db=db,
        action="stop",
        resources=resources,
        operator=f"close:#{task.id}",
        trigger="manual",
        target_app_id=task.target_app_id,
        ordered=True,
    )

    # 点击即归档：写关机时间，移入归档块
    task.closed_at = _now()
    task.close_task_id = stop_task.id
    db.commit()

    write_audit(db, user.username, "close", f"开机会话#{task.id}",
                f"对 {len(resources)} 个实例发起节省停机（关机任务#{stop_task.id}）",
                client_ip(request))

    return {
        "message": "已发起一键关机",
        "close_task_id": stop_task.id,
        "closed_at": task.closed_at,
    }


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
