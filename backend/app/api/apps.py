"""应用（资源分组）管理 + 资源归属绑定。

应用来源：
  1. 资源同步时按「实例名称」自动解析生成（主）；
  2. 运维在页面手工创建并绑定资源（覆盖自动解析，优先级更高）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.models import Application, Resource
from ..services.appname import parse_resource_name
from ..services.executor import write_audit
from ..services.sync import recompute_app_binding
from .deps import client_ip, require_operator, require_readonly

router = APIRouter(prefix="/api/apps", tags=["应用"])


class AppIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=1, max_length=128)
    owner: str = ""
    remark: str = ""
    enabled: bool = True


class AppUpdate(BaseModel):
    name: str | None = None
    owner: str | None = None
    remark: str | None = None
    enabled: bool | None = None


class BindIn(BaseModel):
    resource_ids: list[int] = Field(..., description="需要绑定到该应用的资源 ID 列表")


def _app_stats(db: Session, app: Application) -> dict:
    """统计应用下的资源数与状态分布。"""
    rows = db.execute(
        select(Resource.status, func.count(Resource.id))
        .where(Resource.effective_app_id == app.id, Resource.deleted_on_cloud.is_(False))
        .group_by(Resource.status)
    ).all()
    status_map = {r[0]: r[1] for r in rows}
    total = sum(status_map.values())

    running = sum(v for k, v in status_map.items() if (k or "").lower() in ("running",))
    stopped = sum(v for k, v in status_map.items() if (k or "").lower() in ("stopped",))

    types = dict(db.execute(
        select(Resource.resource_type, func.count(Resource.id))
        .where(Resource.effective_app_id == app.id, Resource.deleted_on_cloud.is_(False))
        .group_by(Resource.resource_type)
    ).all())

    return {
        "total": total,
        "running": running,
        "stopped": stopped,
        "other": total - running - stopped,
        "types": types,
    }


def _app_out(db: Session, app: Application) -> dict:
    return {
        "id": app.id,
        "name": app.name,
        "code": app.code,
        "owner": app.owner,
        "remark": app.remark,
        "enabled": app.enabled,
        "created_at": app.created_at,
        "stats": _app_stats(db, app),
    }


@router.get("")
def list_apps(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    """应用列表（供顶部下拉框使用），含资源统计，按资源数降序。"""
    apps = db.scalars(select(Application).order_by(Application.name)).all()
    items = [_app_out(db, a) for a in apps]
    items.sort(key=lambda x: (-x["stats"]["total"], x["name"]))
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
def create_app(body: AppIn, request: Request,
               db: Session = Depends(get_db), user=Depends(require_operator)):
    if db.scalar(select(Application).where(Application.code == body.code)):
        raise HTTPException(400, f"应用编码已存在：{body.code}")
    app = Application(**body.model_dump())
    db.add(app)
    db.commit()
    write_audit(db, user.username, "create_app", app.name, "", client_ip(request))
    return {"id": app.id, "message": "应用已创建"}


@router.put("/{app_id}")
def update_app(app_id: int, body: AppUpdate, request: Request,
               db: Session = Depends(get_db), user=Depends(require_operator)):
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(404, "应用不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(app, k, v)
    db.commit()
    write_audit(db, user.username, "update_app", app.name, "", client_ip(request))
    return {"message": "已更新"}


@router.delete("/{app_id}")
def delete_app(app_id: int, request: Request,
               db: Session = Depends(get_db), user=Depends(require_operator)):
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(404, "应用不存在")

    # 解绑引用，避免外键约束
    db.execute(
        Resource.__table__.update()
        .where(Resource.effective_app_id == app_id)
        .values(effective_app_id=None, manual_app_id=None, auto_app_id=None)
    )
    name = app.name
    db.delete(app)
    db.commit()
    write_audit(db, user.username, "delete_app", name, "", client_ip(request))
    return {"message": f"已删除应用 {name}"}


@router.post("/{app_id}/bind")
def bind_resources(app_id: int, body: BindIn, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_operator)):
    """手工绑定资源到应用（优先级高于按名称自动解析）。"""
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(404, "应用不存在")

    n = 0
    for rid in body.resource_ids:
        res = db.get(Resource, rid)
        if res is None:
            continue
        res.manual_app_id = app.id
        res.effective_app_id = app.id
        n += 1
    db.commit()
    write_audit(db, user.username, "bind_resources", app.name, f"{n} 个资源", client_ip(request))
    return {"message": f"已绑定 {n} 个资源到 {app.name}"}


@router.post("/{app_id}/unbind")
def unbind_resources(app_id: int, body: BindIn, request: Request,
                     db: Session = Depends(get_db), user=Depends(require_operator)):
    """取消手工绑定，恢复为按名称自动解析的归属。"""
    n = 0
    for rid in body.resource_ids:
        res = db.get(Resource, rid)
        if res is None:
            continue
        res.manual_app_id = None
        res.effective_app_id = res.auto_app_id
        n += 1
    db.commit()
    write_audit(db, user.username, "unbind_resources", f"app#{app_id}",
                f"{n} 个资源", client_ip(request))
    return {"message": f"已取消 {n} 个资源的手工绑定"}


@router.post("/recompute")
def recompute(request: Request, db: Session = Depends(get_db),
              user=Depends(require_operator)):
    """按实例名重新解析全部资源的应用归属（不影响手工绑定）。"""
    result = recompute_app_binding(db)
    write_audit(db, user.username, "recompute_apps", "全部资源",
                f"调整 {result['changed']}/{result['total']}", client_ip(request))
    return {"message": f"已重新解析 {result['total']} 个资源，调整 {result['changed']} 个",
            **result}


@router.post("/parse-preview")
def parse_preview(payload: dict, _u=Depends(require_readonly)):
    """调试用：预览实例名会被解析成哪个应用。"""
    names = payload.get("names") or []
    return {"items": [
        {"name": n, **parse_resource_name(n).__dict__} for n in names
    ]}
