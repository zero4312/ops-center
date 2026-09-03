"""云账号管理：通过接入不同账号的 AK 扩展纳管范围。

安全要求（务必遵守）：
- 每个被管账号应创建 **RAM 子用户** 并授予最小权限（ECS/RDS 只读 + 启停），
  绝不可使用主账号 AK；
- AK/SK 在库中以 Fernet 加密存储，接口永不返回明文 SK；
- 建议叠加来源 IP 白名单与 90 天轮转。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.crypto import decrypt_secret, encrypt_secret, mask_secret
from ..core.database import get_db
from ..models.models import CloudAccount, CloudProvider, Resource
from ..providers import get_provider, supported_providers
from ..providers.base import ProviderError
from ..services.executor import write_audit
from ..services.sync import sync_account, sync_all
from .deps import client_ip, require_admin, require_operator, require_readonly

router = APIRouter(prefix="/api/accounts", tags=["云账号"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class AccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(..., description="alibaba | volcengine")
    region: str = Field(..., min_length=1, max_length=64)
    access_key_id: str = Field(..., min_length=1, max_length=128)
    access_key_secret: str = Field(..., min_length=1, description="明文 SK，仅提交时传入")
    vpc_ids: list[str] | None = Field(default=None, description="限定纳管的 VPC，为空表示不限")
    remark: str = ""
    enabled: bool = True


class AccountUpdate(BaseModel):
    name: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    access_key_secret: str | None = Field(default=None, description="留空表示不修改")
    vpc_ids: list[str] | None = None
    remark: str | None = None
    enabled: bool | None = None


def _account_out(db: Session, acc: CloudAccount) -> dict:
    counts = dict(
        db.execute(
            select(Resource.resource_type, func.count(Resource.id))
            .where(Resource.account_id == acc.id, Resource.deleted_on_cloud.is_(False))
            .group_by(Resource.resource_type)
        ).all()
    )
    return {
        "id": acc.id,
        "name": acc.name,
        "provider": acc.provider,
        "provider_label": "阿里云" if acc.provider == "alibaba" else "火山引擎",
        "region": acc.region,
        "access_key_id": acc.access_key_id,
        "access_key_secret_masked": mask_secret(decrypt_secret(acc.access_key_secret_enc)),
        "vpc_ids": acc.vpc_ids or [],
        "remark": acc.remark,
        "enabled": acc.enabled,
        "last_check_at": acc.last_check_at,
        "last_check_ok": acc.last_check_ok,
        "last_check_msg": acc.last_check_msg,
        "last_sync_at": acc.last_sync_at,
        "last_sync_msg": acc.last_sync_msg,
        "created_at": acc.created_at,
        "resource_counts": counts,
    }


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@router.get("")
def list_accounts(db: Session = Depends(get_db), _u=Depends(require_readonly)):
    rows = db.scalars(select(CloudAccount).order_by(CloudAccount.id)).all()
    return {"items": [_account_out(db, a) for a in rows],
            "providers": supported_providers()}


@router.post("", status_code=201)
def create_account(body: AccountIn, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    if body.provider not in {p.value for p in CloudProvider}:
        raise HTTPException(400, f"不支持的云厂商：{body.provider}")

    if db.scalar(select(CloudAccount).where(CloudAccount.name == body.name)):
        raise HTTPException(400, f"账号名称已存在：{body.name}")

    acc = CloudAccount(
        name=body.name, provider=body.provider, region=body.region,
        access_key_id=body.access_key_id,
        access_key_secret_enc=encrypt_secret(body.access_key_secret),
        vpc_ids=body.vpc_ids or [],
        remark=body.remark, enabled=body.enabled,
    )
    db.add(acc)
    db.commit()
    write_audit(db, user.username, "create_account", acc.name,
                f"{body.provider}/{body.region}", client_ip(request))
    return {"id": acc.id, "message": "云账号已添加，建议立即执行连接测试"}


@router.put("/{account_id}")
def update_account(account_id: int, body: AccountUpdate, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    acc = db.get(CloudAccount, account_id)
    if acc is None:
        raise HTTPException(404, "云账号不存在")

    for field in ("name", "region", "access_key_id", "remark", "enabled"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(acc, field, val)
    if body.vpc_ids is not None:
        acc.vpc_ids = body.vpc_ids
    if body.access_key_secret:
        acc.access_key_secret_enc = encrypt_secret(body.access_key_secret)

    db.commit()
    write_audit(db, user.username, "update_account", acc.name, "", client_ip(request))
    return {"message": "已更新"}


@router.delete("/{account_id}")
def delete_account(account_id: int, request: Request,
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    acc = db.get(CloudAccount, account_id)
    if acc is None:
        raise HTTPException(404, "云账号不存在")
    name = acc.name
    db.delete(acc)  # 级联删除其下资源
    db.commit()
    write_audit(db, user.username, "delete_account", name, "", client_ip(request))
    return {"message": f"已删除云账号 {name} 及其资源记录"}


@router.post("/{account_id}/test")
def test_account(account_id: int, db: Session = Depends(get_db), _u=Depends(require_operator)):
    """接入自检：验证 AK 是否有效、权限是否足够。"""
    acc = db.get(CloudAccount, account_id)
    if acc is None:
        raise HTTPException(404, "云账号不存在")
    try:
        provider = get_provider(acc)
        ok, msg = provider.test_connection()
    except ProviderError as exc:
        ok, msg = False, str(exc)

    acc.last_check_at = datetime.now(timezone.utc).replace(tzinfo=None)
    acc.last_check_ok = ok
    acc.last_check_msg = msg
    db.commit()
    return {"ok": ok, "message": msg}


@router.post("/{account_id}/sync")
def sync_one(account_id: int, request: Request,
             db: Session = Depends(get_db), user=Depends(require_operator)):
    acc = db.get(CloudAccount, account_id)
    if acc is None:
        raise HTTPException(404, "云账号不存在")
    stat = sync_account(db, acc)
    write_audit(db, user.username, "sync_resources", acc.name,
                stat.get("message", ""), client_ip(request),
                result="success" if stat["ok"] else "failed")
    return stat


@router.post("/sync-all")
def sync_all_accounts(request: Request, db: Session = Depends(get_db),
                      user=Depends(require_operator)):
    results = sync_all(db)
    ok = sum(1 for r in results if r["ok"])
    write_audit(db, user.username, "sync_all", f"{len(results)} 个账号",
                f"成功 {ok}", client_ip(request),
                result="success" if ok == len(results) else "partial")
    return {"items": results, "total": len(results), "succeed": ok}
