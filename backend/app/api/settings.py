"""系统设置 API（KV 配置 + 自动同步时间点管理）。

「系统设置」页面首个功能：配置每日自动更新资源的时间点（支持多个），
保存后即时重建调度器（无需重启）。后续新功能照此页扩展即可。
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.database import get_db
from ..models.models import SystemSetting, User, utcnow
from ..services import scheduler as scheduler_svc
from .deps import require_operator, require_readonly

router = APIRouter(prefix="/api/settings", tags=["系统设置"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SyncCronIn(BaseModel):
    times: list[str]
    weekdays_only: bool = True


@router.get("/sync-cron")
def get_sync_cron(_u: User = Depends(require_readonly), db=Depends(get_db)):
    """读取当前自动同步配置（DB 优先，回退 env）。所有角色可见。"""
    cfg = dict(scheduler_svc.load_sync_config())
    cfg["next_runs"] = scheduler_svc.preview_next_runs(
        cfg.get("times", []), cfg.get("weekdays_only", True)
    )
    return cfg


@router.put("/sync-cron")
def put_sync_cron(
    payload: SyncCronIn,
    _u: User = Depends(require_operator),
    db=Depends(get_db),
):
    """更新自动同步时间点并即时重建调度（需 operator 及以上）。"""
    times: list[str] = []
    for t in payload.times:
        s = (t or "").strip()
        if not _TIME_RE.match(s):
            raise HTTPException(
                status_code=400,
                detail=f"时间点格式非法：{s}，应为 HH:MM（24 小时制，如 09:00）",
            )
        hh, mm = (int(x) for x in s.split(":"))
        times.append(f"{hh:02d}:{mm:02d}")

    if not times:
        raise HTTPException(status_code=400, detail="请至少配置一个时间点")
    if len(times) > 20:
        raise HTTPException(status_code=400, detail="时间点过多（最多 20 个）")

    row = db.get(SystemSetting, "sync_cron")
    if row is None:
        row = SystemSetting(key="sync_cron")
        db.add(row)
    row.value = json.dumps(
        {"times": times, "weekdays_only": payload.weekdays_only},
        ensure_ascii=False,
    )
    row.updated_at = utcnow()
    row.updated_by = _u.username
    db.commit()

    rescheduled = scheduler_svc.reschedule_auto_sync(times, payload.weekdays_only)
    nxt = scheduler_svc.preview_next_runs(times, payload.weekdays_only)
    return {
        "times": times,
        "weekdays_only": payload.weekdays_only,
        "next_runs": nxt,
        "rescheduled": rescheduled,
    }
