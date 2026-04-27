"""
数据库初始化脚本
兼容本地开发与 Docker 环境。

使用方法：
    python scripts/local_bootstrap.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    print("=" * 50)
    print("ResearchOS 数据库初始化")
    print("=" * 50)

    # 导入数据库引擎
    print("\n[1/4] 导入数据库模块...")
    from packages.config import get_settings
    from packages.storage.bootstrap import bootstrap_local_runtime
    from packages.storage.db import engine

    settings = get_settings()
    print(f"当前数据库: {settings.database_url}")

    print("[2/4] 准备显式 bootstrap...")
    print("[3/4] 创建数据库表并执行迁移...")
    bootstrap_local_runtime()

    # 验证表是否创建成功
    print("[4/4] 验证表...")
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    print(f"\n创建了 {len(tables)} 个表:")
    for t in tables:
        print(f"  - {t}")

    # 检查关键表
    required_tables = ["papers", "topic_subscriptions", "analysis_reports"]
    missing = [t for t in required_tables if t not in tables]

    if missing:
        print(f"\n❌ 错误：缺少必要的表: {missing}")
        sys.exit(1)
    else:
        print("\n[OK] 数据库初始化成功！")

    print("=" * 50)


if __name__ == "__main__":
    main()
