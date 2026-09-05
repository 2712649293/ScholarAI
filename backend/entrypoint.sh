#!/bin/sh
# 容器启动入口：先迁移建表（新 data 卷为空库时），再 exec 原 CMD。
# alembic 读 backend/.env 的 DATABASE_URL（compose 注入 /app/data 路径）。
set -e
alembic upgrade head
exec "$@"
