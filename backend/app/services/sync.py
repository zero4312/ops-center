"""资源同步服务：从云账号拉取 ECS / RDS 清单，落库并完成应用归属。

设计：
- 以 (account_id, resource_id) 唯一键做 upsert，重复同步不产生脏数据；
- 应用归属采用「按实例名自动解析 + 手工绑定覆盖」双轨，手工绑定优先级更高；
- 云上已释放的资源标记 deleted_on_cloud（不物理删除，保留历史可追溯）；
- 单个账号失败不影响其余账号。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.models import (
    Application, CloudAccount, Resource, utcnow,
)
from ..providers import get_provider
from ..providers.base import CloudResource, ProviderError
from .appname import parse_resource_name, UNCLASSIFIED, UNCLASSIFIED_CODE


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_or_create_app(db: Session, app_code: str, app_name: str) -> Application:
    """按 code 获取应用，不存在则自动创建。"""
    app = db.scalar(select(Application).where(Application.code == app_code))
    if app is None:
        app = Application(code=app_code, name=app_name)
        db.add(app)
        db.flush()
    return app


def _calc_stop_saving(res: CloudResource) -> tuple[bool | None, str]:
    """判断「停机是否省钱」。

    结论（依据各云官方计费规则）：
    - 按量付费（PostPaid）ECS/RDS：停机可省计算/规格费用 -> True
    - 包年包月（PrePaid）：停机期间费用照收，不省 -> False
    - 计费类型未知 -> None（页面显示"未知"）
    """
    if res.charge_type == "PostPaid":
        return True, "按量付费，停机可省计算/规格费"
    if res.charge_type == "PrePaid":
        return False, "包年包月，停机期间费用照收"
    return None, "计费类型未知"


def sync_account(db: Session, account: CloudAccount) -> dict:
    """同步单个云账号，返回统计信息。"""
    stat = {"account": account.name, "added": 0, "updated": 0, "removed": 0,
            "total": 0, "ok": True, "message": ""}

    try:
        provider = get_provider(account)
        cloud_resources = provider.list_all()
    except ProviderError as exc:
        stat.update(ok=False, message=str(exc))
        account.last_sync_at = _now()
        account.last_sync_msg = f"失败：{exc}"
        db.commit()
        return stat
    except Exception as exc:  # noqa: BLE001
        stat.update(ok=False, message=f"同步异常：{exc}")
        account.last_sync_at = _now()
        account.last_sync_msg = f"失败：{exc}"
        db.commit()
        return stat

    seen_ids: set[str] = set()

    for cr in cloud_resources:
        if not cr.resource_id:
            continue
        seen_ids.add(cr.resource_id)

        parsed = parse_resource_name(cr.resource_name)
        app = _get_or_create_app(db, parsed.app_code, parsed.app_name)

        saving, saving_reason = _calc_stop_saving(cr)

        res = db.scalar(
            select(Resource).where(
                Resource.account_id == account.id,
                Resource.resource_id == cr.resource_id,
            )
        )

        if res is None:
            res = Resource(
                account_id=account.id,
                resource_id=cr.resource_id,
                created_at=_now(),
            )
            db.add(res)
            stat["added"] += 1
        else:
            stat["updated"] += 1

        # ---- 字段更新 ----
        res.resource_name = cr.resource_name
        res.resource_type = cr.resource_type
        res.provider = account.provider
        res.region = cr.region or account.region
        res.zone = cr.zone
        res.status = cr.status
        res.env = parsed.env
        res.spec = cr.spec
        res.cpu = cr.cpu
        res.memory_gb = cr.memory_gb
        res.charge_type = cr.charge_type
        res.private_ip = cr.private_ip
        res.vpc_id = cr.vpc_id
        res.tags = cr.tags or {}
        res.stop_saving = saving
        res.stop_saving_reason = saving_reason
        res.deleted_on_cloud = False
        res.last_sync_at = _now()

        # 应用归属：自动解析写入 auto_app_id；手工绑定(manual_app_id)优先级更高
        res.auto_app_id = app.id
        res.effective_app_id = res.manual_app_id if res.manual_app_id else res.auto_app_id

    # ---- 标记云上已释放的资源 ----
    if seen_ids:
        existing = db.scalars(
            select(Resource).where(Resource.account_id == account.id)
        ).all()
        for res in existing:
            if res.resource_id not in seen_ids and not res.deleted_on_cloud:
                res.deleted_on_cloud = True
                res.last_sync_at = _now()
                stat["removed"] += 1

    stat["total"] = len(seen_ids)
    account.last_sync_at = _now()
    account.last_sync_msg = (
        f"成功：新增 {stat['added']}，更新 {stat['updated']}，"
        f"云上已释放 {stat['removed']}，共 {stat['total']} 个"
    )
    db.commit()
    return stat


def sync_all(db: Session) -> list[dict]:
    """同步全部启用中的云账号。"""
    accounts = db.scalars(
        select(CloudAccount).where(CloudAccount.enabled.is_(True))
    ).all()
    return [sync_account(db, acc) for acc in accounts]


def recompute_app_binding(db: Session) -> dict:
    """重新按实例名解析全部资源的应用归属（不影响手工绑定）。"""
    resources = db.scalars(select(Resource)).all()
    changed = 0
    for res in resources:
        parsed = parse_resource_name(res.resource_name)
        app = _get_or_create_app(db, parsed.app_code, parsed.app_name)
        if res.auto_app_id != app.id:
            res.auto_app_id = app.id
            changed += 1
        res.env = parsed.env
        res.effective_app_id = res.manual_app_id if res.manual_app_id else res.auto_app_id
    db.commit()
    return {"total": len(resources), "changed": changed}
