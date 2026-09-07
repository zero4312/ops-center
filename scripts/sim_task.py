"""模拟一条开机会话记录，用于查看任务中心展示效果。

- 仅插入 action=start 的开机会话（任务中心只展示开机任务）。
- 明细用快照字段（resource_name/type/account_name），resource_id 留空，
  保证「目标」列能显示实例名，但点击「一键关机」不会真正下发到云上，安全。
- closed_at 留空 => 出现在「运行中」区块。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta

# 加载 .env 里的 OPS_DATABASE_URL
from dotenv import load_dotenv
load_dotenv()

DB_URL = os.environ.get("OPS_DATABASE_URL")
if not DB_URL:
    sys.exit("未找到 OPS_DATABASE_URL")

# 让脚本能 import backend 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.core.database import Base, get_db  # noqa: E402
from app.models.models import Application, OperationTask, Resource, TaskItem  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402

engine = create_engine(DB_URL)


def main() -> None:
    with next(get_db()) as db:
        # 选一个真实存在、且有资源的「应用」作为归属
        apps = db.scalars(select(Application).where(Application.enabled.is_(True))).all()
        target_app = None
        sample_resources = []
        for app in apps:
            res = db.scalars(
                select(Resource).where(
                    Resource.effective_app_id == app.id,
                    Resource.managed.is_(True),
                    Resource.deleted_on_cloud.is_(False),
                ).limit(4)
            ).all()
            if res:
                target_app = app
                sample_resources = list(res)
                break

        if target_app is None:
            sys.exit("没有可用的应用/资源，无法构造演示记录")

        now = datetime.now()
        boot_time = now - timedelta(minutes=35)

        task = OperationTask(
            action="start",
            scope="app",
            target_app_id=target_app.id,
            trigger="manual",
            policy_id=None,
            operator="lvpeng",
            status="success",
            total=len(sample_resources),
            succeed=len(sample_resources),
            failed=0,
            skipped=0,
            started_at=boot_time,
            finished_at=boot_time + timedelta(seconds=42),
            created_at=boot_time,
            closed_at=None,        # 运行中
            close_task_id=None,
        )
        db.add(task)
        db.flush()

        # 明细：复制真实实例名做快照（不关联真实 resource_id，安全）
        for res in sample_resources:
            db.add(TaskItem(
                task_id=task.id,
                resource_id=None,   # 不关联真实资源，避免触发真实关机
                cloud_resource_id=res.resource_id,
                resource_name=res.resource_name,
                resource_type=res.resource_type,
                account_name=res.account.name if res.account else "",
                status="success",
                message="指令已下发",
                started_at=boot_time,
                finished_at=boot_time + timedelta(seconds=42),
            ))
        db.commit()

        print("已插入演示开机会话:")
        print(f"  task_id       = {task.id}")
        print(f"  归属应用      = {target_app.name} (id={target_app.id})")
        print(f"  状态          = success（运行中，未关机归档）")
        print(f"  目标实例      = " + ", ".join(r.resource_name for r in sample_resources))
        print(f"  开机时间      = {boot_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        print("前端任务中心 -> 运行中 区块现在会多出这一条；刷新页面即可看到。")


if __name__ == "__main__":
    main()
