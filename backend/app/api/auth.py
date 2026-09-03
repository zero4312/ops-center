"""认证接口：登录 / 当前用户 / 修改密码。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.security import (
    create_access_token, hash_password, verify_password,
)
from ..models.models import User, utcnow
from ..services.executor import write_audit
from .deps import client_ip, get_current_user

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class ChangePwdIn(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.enabled or not verify_password(body.password, user.password_hash):
        write_audit(db, body.username, "login", "登录", "用户名或密码错误",
                    client_ip(request), result="failed")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    token = create_access_token(user.username, user.role, {"full_name": user.full_name})
    write_audit(db, user.username, "login", "登录", f"角色 {user.role}", client_ip(request))

    return {
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "email": user.email,
        },
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "email": user.email,
    }


@router.post("/change-password")
def change_password(
    body: ChangePwdIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    write_audit(db, user.username, "change_password", user.username, "", client_ip(request))
    return {"message": "密码已修改"}
