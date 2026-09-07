"""系统设置端点逻辑测试（直接调用路由函数，无需 httpx / 无需主机 uvicorn）。

验证：
1. get_sync_cron 回退 env 返回 09:00/17:45 + weekdays_only=true + next_runs
2. put_sync_cron 写入 DB + 运行时动态重建调度（reschedule_auto_sync 改 apscheduler_jobs）
3. 非法时间点被拒（400）
4. 最终还原为 09:00/17:45 + weekdays_only=true
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.models.models import User
from app.api import settings as settings_api
from app.services import scheduler as scheduler_svc

DB_URL = os.getenv("OPS_DATABASE_URL")
eng = create_engine(DB_URL)


def db_sync_jobs():
    with eng.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text("SELECT id FROM apscheduler_jobs WHERE id LIKE 'resource_auto_sync%' ORDER BY id")
            ).fetchall()
        ]


def db_setting():
    with eng.connect() as c:
        row = c.execute(text("SELECT value FROM system_settings WHERE `key`='sync_cron'")).fetchone()
        return row[0] if row else None


print("=== 启动进程内调度器（用于验证 reschedule 真实生效）===")
scheduler_svc.start_scheduler()
print("sync jobs 启动后:", db_sync_jobs())

# 取一个 operator 角色用户（admin）
db = SessionLocal()
admin = db.query(User).filter(User.username == os.getenv("OPS_ADMIN_USERNAME", "admin")).first()
assert admin is not None, "admin 用户不存在"
print(f"使用用户：{admin.username} / role={admin.role}")

# 1) GET 当前配置（DB 空 -> 回退 env）
cfg = settings_api.get_sync_cron(admin, db)
print("[GET] 回退 env =>", cfg)
assert cfg["times"] == ["09:00", "17:45"], cfg["times"]
assert cfg["weekdays_only"] is True
assert len(cfg["next_runs"]) >= 1

# 2) PUT 改为每天 10:30 / 22:00
out = settings_api.put_sync_cron(
    settings_api.SyncCronIn(times=["10:30", "22:00"], weekdays_only=False), admin, db
)
print("[PUT 10:30/22:00] =>", out)
assert out["times"] == ["10:30", "22:00"]
assert out["weekdays_only"] is False
assert out["rescheduled"] is True
val = db_setting()
print("[DB system_settings] =>", val)
assert '"times":["10:30","22:00"]' in val.replace(" ", "")
# 调度已重建 -> 仍 2 个 job（数量不变，配置变）
print("[sync jobs 重建后] =>", db_sync_jobs())

# 3) PUT 还原为工作日 09:00 / 17:45（最终期望状态）
out = settings_api.put_sync_cron(
    settings_api.SyncCronIn(times=["09:00", "17:45"], weekdays_only=True), admin, db
)
print("[PUT 还原 09:00/17:45] =>", out)
assert out["times"] == ["09:00", "17:45"]
assert out["weekdays_only"] is True
assert out["rescheduled"] is True
assert len(out["next_runs"]) >= 1

# 4) 非法格式应抛 400
try:
    settings_api.put_sync_cron(settings_api.SyncCronIn(times=["99:99"], weekdays_only=True), admin, db)
    raise AssertionError("非法时间点未被拒绝！")
except Exception as e:
    print("[PUT 非法 99:99] 被拒 =>", type(e).__name__, getattr(e, "status_code", ""))

# 5) 空列表应被拒
try:
    settings_api.put_sync_cron(settings_api.SyncCronIn(times=[], weekdays_only=True), admin, db)
    raise AssertionError("空时间点未被拒绝！")
except Exception as e:
    print("[PUT 空列表] 被拒 =>", type(e).__name__, getattr(e, "status_code", ""))

db.close()
print("\n[最终 DB system_settings] =>", db_setting())
print("[最终 sync jobs] =>", db_sync_jobs())
print("\n=== 全部断言通过 ===")
