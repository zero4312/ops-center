#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库初始化 / 升级脚本（由 deploy.sh db-init 调用）。

用法：
    PYTHONPATH=backend python -m app.scripts.init_db
    PYTHONPATH=backend python -m app.scripts.init_db --reset-admin
    PYTHONPATH=backend python -m app.scripts.init_db --reset-admin --password 'NewPass@123'

说明：
- 使用 SQLAlchemy create_all 建表，已存在的表不会变更（幂等，可重复执行）；
- 首次运行自动创建管理员账号；--reset-admin 可重置管理员密码。
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import inspect, select, text


def main() -> int:
    parser = argparse.ArgumentParser(description="ops-center 数据库初始化")
    parser.add_argument("--reset-admin", action="store_true", help="重置管理员密码")
    parser.add_argument("--password", default="", help="配合 --reset-admin 指定新密码")
    parser.add_argument("--username", default="", help="配合 --reset-admin 指定用户名")
    args = parser.parse_args()

    # 延迟导入，确保 .env 已生效
    from sqlalchemy import func

    from ..core.config import settings
    from ..core.database import Base, SessionLocal, engine
    from ..core.security import hash_password
    from ..models import models  # noqa: F401  注册全部模型
    from ..models.models import Application, CloudAccount, Resource, SchedulePolicy, User

    print("=" * 62)
    print("ops-center 数据库初始化")
    print("=" * 62)
    print(f"数据库：{'SQLite' if settings.is_sqlite else 'MySQL'}")
    # 隐藏密码后打印连接串
    url = settings.DATABASE_URL
    if "@" in url:
        head, tail = url.split("@", 1)
        safe_head = head.rsplit(":", 1)[0] + ":****"
        print(f"连接串：{safe_head}@{tail}")
    else:
        print(f"连接串：{url}")
    print()

    # ---------- 连通性检查 ----------
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 数据库连不上：{exc}")
        print()
        print("排查建议：")
        if settings.is_sqlite:
            print("  1. 检查 data/ 目录是否有写权限")
        else:
            print("  1. 本机可用 './deploy.sh mysql-up' 快速起一个 MySQL 容器")
            print("  2. 确认 MySQL 已启动且端口可达：mysql -h127.0.0.1 -uopscenter -p")
            print("  3. 确认 .env 中 OPS_DATABASE_URL 的账号密码与库名正确")
            print("  4. 确认数据库已创建：CREATE DATABASE ops_center CHARACTER SET utf8mb4;")
        return 1

    # ---------- 建表 ----------
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    after = set(inspect(engine).get_table_names())
    created = sorted(after - before)

    print(f"[OK] 数据表检查完成，共 {len(after)} 张表")
    if created:
        print(f"     新建表：{', '.join(created)}")
    else:
        print("     全部表已存在（本次为幂等校验，未做结构变更）")
    print()

    # ---------- 管理员 ----------
    db = SessionLocal()
    try:
        username = args.username or settings.ADMIN_USERNAME
        password = args.password or settings.ADMIN_PASSWORD

        user = db.scalar(select(User).where(User.username == username))

        if args.reset_admin:
            if user is None:
                user = User(username=username, full_name="系统管理员", role="admin")
                db.add(user)
                action = "创建"
            else:
                action = "重置密码"
            user.password_hash = hash_password(password)
            user.enabled = True
            db.commit()
            print(f"[OK] 管理员账号已{action}：{username} / {password}")
        else:
            if user is None:
                user = User(
                    username=settings.ADMIN_USERNAME,
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                    full_name="系统管理员", role="admin",
                )
                db.add(user)
                db.commit()
                print(f"[OK] 已创建初始管理员：{settings.ADMIN_USERNAME} / {settings.ADMIN_PASSWORD}")
                print("     >>> 请登录后立即修改密码 <<<")
            else:
                print(f"[OK] 管理员已存在：{user.username}（如需重置：--reset-admin）")

        # ---------- 当前数据概览 ----------
        acc_n = db.scalar(select(func.count(CloudAccount.id))) or 0
        res_n = db.scalar(select(func.count(Resource.id))) or 0
        app_n = db.scalar(select(func.count(Application.id))) or 0
        pol_n = db.scalar(select(func.count(SchedulePolicy.id))) or 0
        print()
        print(f"当前数据：云账号 {acc_n} 个 | 资源 {res_n} 个 | 应用 {app_n} 个 | 定时策略 {pol_n} 条")
    finally:
        db.close()

    print()
    print("=" * 62)
    print("初始化完成。下一步：")
    print("  1. ./deploy.sh start     启动服务")
    print("  2. 浏览器访问 http://127.0.0.1:8000")
    print("  3. 进入「云账号」添加 AK，然后「同步资源」")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
