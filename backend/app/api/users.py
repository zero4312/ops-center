"""用户与权限管理（管理员专用）+ 审计日志。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.security import hash_password
from ..models.models import AuditLog, User, UserRole
from ..services.executor import write_audit
from .deps import client_ip, require_admin, require_readonly

router = APIRouter(prefix="/api/users", tags=["用户"])

ROLE_LABELS = {
    "admin": "管理员",
    "operator": "运维",
    "readonly": "只读",
}


class UserIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8)
    full_name: str = ""
    role: str = "readonly"
    email: str = ""
    enabled: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    email: str | None = None
    enabled: bool | None = None


class ResetPwdIn(BaseModel):
    new_password: str = Field(..., min_length=8)


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "email": u.email,
        "enabled": u.enabled,
        "last_login_at": u.last_login_at,
        "created_at": u.created_at,
    }


@router.get("")
def list_users(db: Session = Depends(get_db), _u=Depends(require_admin)):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return {"items": [_user_out(u) for u in rows],
            "roles": [{"value": r.value, "label": ROLE_LABELS[r.value]} for r in UserRole]}


@router.post("", status_code=201)
def create_user(body: UserIn, request: Request,
                db: Session = Depends(get_db), user=Depends(require_admin)):
    if body.role not in {r.value for r in UserRole}:
        raise HTTPException(400, "角色非法")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, "用户名已存在")

    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name, role=body.role,
        email=body.email, enabled=body.enabled,
    )
    db.add(u)
    db.commit()
    write_audit(db, user.username, "create_user", u.username,
                f"角色 {body.role}", client_ip(request))
    return {"id": u.id, "message": "用户已创建"}


@router.put("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request,
                db: Session = Depends(get_db), user=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if body.role and body.role not in {r.value for r in UserRole}:
        raise HTTPException(400, "角色非法")

    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    db.commit()
    write_audit(db, user.username, "update_user", u.username, "", client_ip(request))
    return {"message": "已更新"}


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request,
                db: Session = Depends(get_db), user=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if u.username == user.username:
        raise HTTPException(400, "不能删除当前登录用户")
    name = u.username
    db.delete(u)
    db.commit()
    write_audit(db, user.username, "delete_user", name, "", client_ip(request))
    return {"message": f"已删除用户 {name}"}


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPwdIn, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    u.password_hash = hash_password(body.new_password)
    db.commit()
    write_audit(db, user.username, "reset_password", u.username, "", client_ip(request))
    return {"message": "密码已重置"}


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------
@router.get("/audit-logs")
def list_audit_logs(
    action: str | None = Query(None),
    username: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db), _u=Depends(require_readonly),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if username:
        stmt = stmt.where(AuditLog.username == username)
    rows = db.scalars(stmt.order_by(desc(AuditLog.id)).limit(limit)).all()
    return {"items": [{
        "id": r.id, "username": r.username, "action": r.action,
        "target": r.target, "detail": r.detail, "client_ip": r.client_ip,
        "result": r.result, "created_at": r.created_at,
    } for r in rows]}
