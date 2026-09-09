"""开关机执行引擎。

流程：预检 -> 排序 -> 下发 -> 汇总 -> 状态回刷

排序规则（避免应用起不来）：
    开机：RDS(10) -> ECS(20)      （数据库先就绪）
    关机：ECS(10) -> RDS(20)      （先停计算，再停库）

跳过规则（不调用云 API，直接标记 skipped）：
    - 资源已在目标状态
    - 资源被人工排除纳管（managed=False）
    - 资源在云上已释放
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.database import SessionLocal
from ..core.crypto import decrypt_secret  # noqa: F401  (保持依赖显式)
from ..models.models import (
    AuditLog, CloudAccount, ItemStatus, OperationTask, Resource,
    ScheduleLog, SchedulePolicy, TaskItem, TaskStatus, utcnow,
)
from ..providers import get_provider
from ..providers.base import ProviderError

# 类型权重：开机 RDS 先、ECS 后；关机反之
_ORDER_WEIGHT = {
    ("start", "RDS"): 10, ("start", "ECS"): 20,
    ("stop", "ECS"): 10, ("stop", "RDS"): 20,
}

# RDS 串行锁：规避云厂商 QPS 限制（如阿里云 RDS 10 QPS）
_rds_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _target_status(action: str) -> str:
    return "running" if action == "start" else "stopped"


def _human_error(exc: Exception) -> str:
    """把云 API 异常翻译成运维能看懂的中文提示。"""
    msg = str(exc)
    code = getattr(exc, "code", "") or ""
    if "InvalidSystemDiskCategory" in code or "NoStock" in msg:
        return "库存不足，节省停机模式回收了计算资源，请稍后重试或改用普通停机"
    if "IncorrectInstanceStatus" in code or "IncorrectDBInstanceState" in code:
        return "实例状态不满足操作条件，可能正在变更中，请稍后重试"
    if "Forbidden" in code or "NoPermission" in code or "Unauthorized" in code:
        return "云账号权限不足，请检查 RAM 子用户的 ECS/RDS 启停权限"
    if "InvalidAccessKeyId" in code or "SignatureDoesNotMatch" in code:
        return "AK/SK 无效或已失效，请在「云账号」页面更新凭证"
    if "Throttling" in code:
        return "云 API 触发限流，请稍后重试"
    return msg[:500]


# ---------------------------------------------------------------------------
# 创建任务
# ---------------------------------------------------------------------------
def create_task(
    db: Session,
    action: str,
    resources: list[Resource],
    operator: str = "system",
    trigger: str = "manual",
    policy_id: int | None = None,
    target_app_id: int | None = None,
    ordered: bool = True,
) -> OperationTask:
    """创建操作任务（含明细），并异步执行。"""
    task = OperationTask(
        action=action,
        scope="app" if target_app_id else "custom",
        target_app_id=target_app_id,
        trigger=trigger,
        policy_id=policy_id,
        operator=operator,
        status=TaskStatus.PENDING.value,
        total=len(resources),
    )
    db.add(task)
    db.flush()

    for res in resources:
        db.add(TaskItem(
            task_id=task.id,
            resource_id=res.id,
            cloud_resource_id=res.resource_id,
            resource_name=res.resource_name,
            resource_type=res.resource_type,
            account_name=res.account.name if res.account else "",
            status=ItemStatus.PENDING.value,
        ))
    db.commit()

    # 后台线程执行（不阻塞 API 响应）
    t = threading.Thread(target=_run_task, args=(task.id, ordered), daemon=True)
    t.start()
    return task


# ---------------------------------------------------------------------------
# 执行任务
# ---------------------------------------------------------------------------
def _run_task(task_id: int, ordered: bool = True) -> None:
    """在独立线程中执行任务（使用独立 DB Session）。"""
    db: Session = SessionLocal()
    try:
        task = db.get(OperationTask, task_id)
        if task is None:
            return

        task.status = TaskStatus.RUNNING.value
        task.started_at = _now()
        db.commit()

        items = db.scalars(
            select(TaskItem).where(TaskItem.task_id == task_id)
        ).all()

        # 排序：按类型权重
        if ordered:
            items.sort(key=lambda i: _ORDER_WEIGHT.get((task.action, i.resource_type), 99))

        max_workers = max(1, min(settings.MAX_WORKERS, len(items) or 1))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_run_item, task.action, item.id) for item in items]
            for fut in futures:
                try:
                    fut.result()
                except Exception:  # noqa: BLE001
                    pass

        # 汇总
        db.expire_all()
        items = db.scalars(select(TaskItem).where(TaskItem.task_id == task_id)).all()
        succeed = sum(1 for i in items if i.status == ItemStatus.SUCCESS.value)
        failed = sum(1 for i in items if i.status == ItemStatus.FAILED.value)
        skipped = sum(1 for i in items if i.status == ItemStatus.SKIPPED.value)

        task.succeed, task.failed, task.skipped = succeed, failed, skipped
        task.finished_at = _now()
        task.status = (
            TaskStatus.SUCCESS.value if failed == 0 and succeed > 0
            else TaskStatus.PARTIAL.value if succeed > 0
            else TaskStatus.SUCCESS.value if failed == 0
            else TaskStatus.FAILED.value
        )
        db.commit()

        # 回刷资源真实状态（异步，失败不影响任务结果）
        try:
            _refresh_status_for_task(db, task.id)
        except Exception:  # noqa: BLE001
            pass

        # 定时触发的任务：写执行日志 + 回写策略
        if task.trigger == "schedule" and task.policy_id:
            _write_schedule_log(db, task)

    finally:
        db.close()


def _run_item(action: str, item_id: int) -> None:
    """执行单条明细（独立 Session，独立事务）。"""
    db: Session = SessionLocal()
    try:
        item = db.get(TaskItem, item_id)
        if item is None:
            return

        res = db.get(Resource, item.resource_id) if item.resource_id else None

        # ---- 预检：跳过 ----
        if res is None:
            item.status = ItemStatus.FAILED.value
            item.message = "资源记录不存在，可能已被删除"
            item.finished_at = _now()
            db.commit()
            return
        if not res.managed:
            item.status, item.message = ItemStatus.SKIPPED.value, "已排除纳管"
            item.finished_at = _now()
            db.commit()
            return
        if res.deleted_on_cloud:
            item.status, item.message = ItemStatus.SKIPPED.value, "云上已释放"
            item.finished_at = _now()
            db.commit()
            return
        if res.power_state == _target_status(action):
            item.status = ItemStatus.SKIPPED.value
            item.message = "已处于目标状态"
            item.finished_at = _now()
            db.commit()
            return

        account = db.get(CloudAccount, res.account_id)
        if account is None or not account.enabled:
            item.status = ItemStatus.FAILED.value
            item.message = "云账号不存在或已停用"
            item.finished_at = _now()
            db.commit()
            return

        item.status = ItemStatus.RUNNING.value
        item.started_at = _now()
        db.commit()

        # ---- 调用云 API ----
        provider = get_provider(account)
        is_rds = res.resource_type.upper() == "RDS"

        def _invoke():
            if is_rds:
                return (provider.start_rds(res.resource_id) if action == "start"
                        else provider.stop_rds(res.resource_id))
            return (provider.start_ecs(res.resource_id) if action == "start"
                    else provider.stop_ecs(res.resource_id, force=False,
                                           stopped_mode="StopCharging"))

        try:
            if is_rds:
                # RDS 串行：规避云厂商 QPS 限制
                with _rds_lock:
                    request_id = _invoke()
            else:
                request_id = _invoke()
        except ProviderError as exc:
            item.status = ItemStatus.FAILED.value
            item.message = _human_error(exc)
        except Exception as exc:  # noqa: BLE001
            item.status = ItemStatus.FAILED.value
            item.message = _human_error(exc)
        else:
            item.status = ItemStatus.SUCCESS.value
            item.message = "指令已下发"
            item.request_id = request_id or ""

        item.finished_at = _now()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 状态回刷
# ---------------------------------------------------------------------------
def _refresh_status_for_task(db: Session, task_id: int) -> None:
    """任务结束后，轮询回刷相关资源的真实状态，直到达到目标态或超时。

    云厂商 start/stop 为异步操作，指令下发后实例处于 Starting/Stopping 过渡态。
    本函数每隔 REFRESH_POLL_INTERVAL 秒回查一次云上真实状态并写回 Resource.status，
    直到全部实例到达目标态（running/stopped）或累计等待超过 OP_TIMEOUT 秒。
    仅处理本次执行成功（指令已下发）的实例；账户不可用或 list 失败则下一轮重试。
    """
    logger = logging.getLogger("opscenter.executor")

    task = db.get(OperationTask, task_id)
    if task is None:
        return
    want = _target_status(task.action)  # running / stopped

    items = db.scalars(
        select(TaskItem).where(
            TaskItem.task_id == task_id,
            TaskItem.status == ItemStatus.SUCCESS.value,
            TaskItem.resource_id.isnot(None),
        )
    ).all()
    pending_ids = [i.resource_id for i in items if i.resource_id]
    if not pending_ids:
        return

    interval = max(5, int(settings.REFRESH_POLL_INTERVAL or 60))
    timeout = max(interval, int(settings.OP_TIMEOUT or 600))
    deadline = time.monotonic() + timeout

    while pending_ids and time.monotonic() < deadline:
        # 按 (账号, 类型) 分组，每组只调一次 list，降低云 API 调用频次
        resources = db.scalars(
            select(Resource).where(Resource.id.in_(pending_ids))
        ).all()
        groups: dict[tuple, list[Resource]] = {}
        for r in resources:
            groups.setdefault((r.account_id, r.resource_type.upper()), []).append(r)

        still_pending: list[int] = []
        for (account_id, rtype), rlist in groups.items():
            account = db.get(CloudAccount, account_id)
            if account is None or not account.enabled:
                still_pending.extend(r.id for r in rlist)
                continue
            try:
                provider = get_provider(account)
                cloud_list = provider.list_ecs() if rtype == "ECS" else provider.list_rds()
            except Exception as exc:  # noqa: BLE001
                logger.warning("回刷状态 list 失败(account=%s): %s", account_id, exc)
                still_pending.extend(r.id for r in rlist)
                continue
            mapping = {c.resource_id: c.status for c in cloud_list}
            for r in rlist:
                if r.resource_id in mapping:
                    r.status = mapping[r.resource_id]
                    r.last_sync_at = _now()
                    if mapping[r.resource_id].lower() == want:
                        continue  # 已落定，移出 pending
                    still_pending.append(r.id)
                else:
                    # 云上已无此实例（已释放），停止轮询该资源
                    r.deleted_on_cloud = True
                    r.released_at = _now()  # 记录云上释放时间
                    r.last_sync_at = _now()
                    continue
        db.commit()

        pending_ids = still_pending
        if not pending_ids:
            break
        if time.monotonic() < deadline:
            time.sleep(interval)

    if pending_ids:
        logger.warning(
            "回刷状态超时（task=%s, 目标=%s, 仍有 %d 个未落定），将于下次同步补齐",
            task_id, want, len(pending_ids),
        )


def refresh_resource_status(db: Session, resource: Resource) -> bool:
    """刷新单个资源状态，返回是否成功。"""
    try:
        provider = get_provider(resource.account)
        cloud_list = (provider.list_ecs() if resource.resource_type.upper() == "ECS"
                      else provider.list_rds())
        for c in cloud_list:
            if c.resource_id == resource.resource_id:
                resource.status = c.status
                resource.charge_type = c.charge_type or resource.charge_type
                resource.spec = c.spec or resource.spec
                resource.engine_version = c.engine_version or resource.engine_version
                resource.cpu = c.cpu if c.cpu is not None else resource.cpu
                resource.memory_gb = c.memory_gb if c.memory_gb is not None else resource.memory_gb
                resource.last_sync_at = _now()
                db.commit()
                return True
        if resource.managed:
            resource.deleted_on_cloud = True
            resource.released_at = _now()  # 记录云上释放时间
        resource.last_sync_at = _now()
        db.commit()
        return False
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise ProviderError(str(exc)) from exc


def _write_schedule_log(db: Session, task: OperationTask) -> None:
    """定时执行的任务写日志并回写策略状态。"""
    policy = db.get(SchedulePolicy, task.policy_id)
    if policy:
        policy.last_run_at = task.started_at or _now()
        policy.last_status = task.status
        policy.last_task_id = task.id
        db.add(ScheduleLog(
            policy_id=policy.id,
            policy_name=policy.name,
            task_id=task.id,
            fired_at=task.started_at or _now(),
            status=task.status,
            total=task.total,
            succeed=task.succeed,
            failed=task.failed,
            skipped=task.skipped,
        ))
    else:
        db.add(ScheduleLog(
            policy_id=task.policy_id,
            policy_name="(已删除策略)",
            task_id=task.id,
            fired_at=task.started_at or _now(),
            status=task.status,
            total=task.total,
            succeed=task.succeed,
            failed=task.failed,
            skipped=task.skipped,
        ))
    db.commit()


def write_audit(db: Session, username: str, action: str, target: str = "",
                detail: str = "", client_ip: str = "", result: str = "success") -> None:
    db.add(AuditLog(
        username=username, action=action, target=target,
        detail=detail, client_ip=client_ip, result=result,
    ))
    db.commit()
