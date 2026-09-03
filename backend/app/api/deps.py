"""API 公共依赖：获取当前用户、权限校验。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.security import decode_access_token, has_permission
from ..models.models import User

security = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """解析 JWT -> 用户对象。"""
    token = ""
    if credentials and credentials.credentials:
        token = credentials.credentials
    else:
        # 兼容前端把 token 放在 query / header 的场景
        token = request.query_params.get("token", "") or request.headers.get("X-Token", "")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")

    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if user is None or not user.enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")

    request.state.username = user.username
    request.state.role = user.role
    return user


def require_role(required: str):
    """角色权限依赖：admin > operator > readonly。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，需要 {required} 及以上角色",
            )
        return user

    return _dep


# 常用快捷依赖
require_admin = require_role("admin")
require_operator = require_role("operator")
require_readonly = require_role("readonly")


def client_ip(request: Request) -> str:
    """获取客户端 IP（兼容反向代理）。"""
    for header in ("X-Forwarded-For", "X-Real-IP"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else ""
