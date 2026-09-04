"""资源清单：多条件查询、状态刷新、纳管与归属维护。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.models import Application, CloudAccount, Resource
from ..providers import get_provider
from ..services.executor import refresh_resource_status, write_audit
from ..services.sync import recompute_app_binding
from .deps import client_ip, require_operator, require_readonly

router = APIRouter(prefix="/api/resources", tags=["资源"])


class ResourceUpdate(BaseModel):
    managed: bool | None = None
    app_id: int | None = None     # 传 null 表示取消手工绑定
    remark: str | None = None


class BatchAssign(BaseModel):
    """批量设置资源归属应用（app_id 为空 = 取消手工绑定，恢复自动解析）。"""
    resource_ids: list[int]
    app_id: int | None = None


def _resource_out(res: Resource) -> dict:
    return {
        "id": res.id,
        "resource_id": res.resource_id,
        "resource_name": res.resource_name,
        "resource_type": res.resource_type,
        "provider": res.provider,
        "provider_label": "阿里云" if res.provider == "alibaba" else "火山引擎",
        "region": res.region,
        "zone": res.zone,
        "status": res.status,
        "power_state": res.power_state,
        "env": res.env,
        "spec": res.spec,
        "engine_version": res.engine_version,
        "cpu": res.cpu,
        "memory_gb": res.memory_gb,
        "charge_type": res.charge_type,
        "charge_label": {"PostPaid": "按量", "PrePaid": "包年包月"}.get(res.charge_type, res.charge_type),
        "private_ip": res.private_ip,
        "vpc_id": res.vpc_id,
        "stop_saving": res.stop_saving,
        "stop_saving_reason": res.stop_saving_reason,
        "managed": res.managed,
        "deleted_on_cloud": res.deleted_on_cloud,
        "last_sync_at": res.last_sync_at,
        "account_id": res.account_id,
        "account_name": res.account.name if res.account else "",
        "app_id": res.effective_app_id,
        "app_name": (res.manual_app.name if res.manual_app else None)
                    or (res.auto_app.name if res.auto_app else None)
                    or "未分类",
        "manual_app_id": res.manual_app_id,
        "is_manual": bool(res.manual_app_id),
    }


@router.get("")
def list_resources(
    app_id: int | None = Query(None, description="按应用过滤"),
    resource_type: str | None = Query(None, description="ECS | RDS"),
    provider: str | None = Query(None, description="alibaba | volcengine"),
    account_id: int | None = Query(None),
    power_state: str | None = Query(None, description="running | stopped | other"),
    env: str | None = Query(None, description="STG | DEV ..."),
    managed: bool | None = Query(None),
    keyword: str | None = Query(None, description="实例名 / 实例ID / IP 模糊搜索"),
    only_deleted: bool = Query(False, description="仅看云上已释放"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _u=Depends(require_readonly),
):
    stmt = select(Resource).outerjoin(CloudAccount, Resource.account_id == CloudAccount.id)

    if app_id is not None:
        stmt = stmt.where(Resource.effective_app_id == app_id)
    if resource_type:
        stmt = stmt.where(Resource.resource_type == resource_type.upper())
    if provider:
        stmt = stmt.where(Resource.provider == provider)
    if account_id:
        stmt = stmt.where(Resource.account_id == account_id)
    if env:
        stmt = stmt.where(Resource.env == env)
    if managed is not None:
        stmt = stmt.where(Resource.managed.is_(managed))
    if only_deleted:
        stmt = stmt.where(Resource.deleted_on_cloud.is_(True))
    else:
        stmt = stmt.where(Resource.deleted_on_cloud.is_(False))
    if keyword:
        kw = f"%{keyword}%"
        stmt = stmt.where(or_(
            Resource.resource_name.like(kw),
            Resource.resource_id.like(kw),
            Resource.private_ip.like(kw),
        ))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Resource.resource_type, Resource.resource_name)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = [_resource_out(r) for r in rows]
    if power_state:
        items = [i for i in items if i["power_state"] == power_state]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/summary")
def summary(
    app_id: int | None = Query(None),
    db: Session = Depends(get_db), _u=Depends(require_readonly),
):
    """概览统计卡片数据。"""
    base = select(Resource).where(Resource.deleted_on_cloud.is_(False))
    if app_id is not None:
        base = base.where(Resource.effective_app_id == app_id)

    rows = db.execute(
        select(Resource.resource_type, Resource.status, func.count(Resource.id))
        .where(Resource.deleted_on_cloud.is_(False))
        .group_by(Resource.resource_type, Resource.status)
    ).all()

    summary_data: dict[str, dict] = {}
    for rtype, status, cnt in rows:
        d = summary_data.setdefault(rtype, {"total": 0, "running": 0, "stopped": 0, "other": 0})
        d["total"] += cnt
        s = (status or "").lower()
        if s == "running":
            d["running"] += cnt
        elif s == "stopped":
            d["stopped"] += cnt
        else:
            d["other"] += cnt

    # 可停机节省统计（注意：stop_saving=True 仅对按量付费资源有意义）
    saving_stmt = base.where(Resource.stop_saving.is_(True))
    saving = db.scalar(select(func.count()).select_from(saving_stmt.subquery())) or 0
    accounts = db.scalar(select(func.count(CloudAccount.id)).where(CloudAccount.enabled.is_(True))) or 0
    apps = db.scalar(select(func.count(Application.id))) or 0

    return {
        "by_type": summary_data,
        "stop_saving_count": saving,
        "account_count": accounts,
        "app_count": apps,
        "total": sum(d["total"] for d in summary_data.values()),
    }


@router.post("/{resource_id}/refresh")
def refresh_one(resource_id: int, db: Session = Depends(get_db),
                _u=Depends(require_operator)):
    """刷新单个资源的实时状态。"""
    res = db.get(Resource, resource_id)
    if res is None:
        raise HTTPException(404, "资源不存在")
    try:
        refresh_resource_status(db, res)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"刷新失败：{exc}") from exc
    return _resource_out(res)


@router.post("/refresh-status")
def refresh_many(payload: dict, db: Session = Depends(get_db),
                 _u=Depends(require_operator)):
    """批量刷新状态（不传 ids 则刷新全部）。"""
    ids = payload.get("ids") or []
    stmt = select(Resource).where(Resource.deleted_on_cloud.is_(False))
    if ids:
        stmt = stmt.where(Resource.id.in_(ids))
    rows = db.scalars(stmt).all()

    ok, failed = 0, 0
    for res in rows:
        try:
            refresh_resource_status(db, res)
            ok += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"succeed": ok, "failed": failed, "total": len(rows)}


@router.put("/{resource_id}")
def update_resource(resource_id: int, body: ResourceUpdate, request: Request,
                    db: Session = Depends(get_db), user=Depends(require_operator)):
    res = db.get(Resource, resource_id)
    if res is None:
        raise HTTPException(404, "资源不存在")

    if body.managed is not None:
        res.managed = body.managed
    if "app_id" in body.model_dump(exclude_unset=True):
        if body.app_id is None:
            res.manual_app_id = None
            res.effective_app_id = res.auto_app_id
        else:
            app = db.get(Application, body.app_id)
            if app is None:
                raise HTTPException(404, "应用不存在")
            res.manual_app_id = app.id
            res.effective_app_id = app.id

    db.commit()
    write_audit(db, user.username, "update_resource", res.resource_name,
                body.model_dump_json(exclude_unset=True), client_ip(request))
    return _resource_out(res)


@router.post("/batch-assign")
def batch_assign(body: BatchAssign, request: Request,
                db: Session = Depends(get_db), user=Depends(require_operator)):
    """批量设置资源归属应用：app_id 传值=手工绑定，传空=恢复按名称自动解析。"""
    if not body.resource_ids:
        raise HTTPException(400, "resource_ids 不能为空")

    app = None
    if body.app_id is not None:
        app = db.get(Application, body.app_id)
        if app is None:
            raise HTTPException(404, "应用不存在")

    rows = db.scalars(select(Resource).where(Resource.id.in_(body.resource_ids))).all()

    n = 0
    for res in rows:
        if app is not None:
            res.manual_app_id = app.id
            res.effective_app_id = app.id
        else:
            res.manual_app_id = None
            res.effective_app_id = res.auto_app_id
        n += 1
    db.commit()

    action = f"绑定到 {app.name}" if app is not None else "恢复自动解析"
    write_audit(db, user.username, "batch_assign", app.name if app else "(自动解析)",
                f"{n} 个资源{action}", client_ip(request))
    return {"message": f"已将 {n} 个资源{action}", "count": n}


@router.get("/environments")
def list_environments(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    """环境枚举，供筛选下拉框使用。"""
    rows = db.scalars(
        select(Resource.env).where(Resource.deleted_on_cloud.is_(False)).distinct()
    ).all()
    return {"items": sorted([r for r in rows if r])}
