#!/usr/bin/env bash
# 文件作用：停止后端后备份运行数据，并在结束时恢复服务。

set -euo pipefail

# 项目根目录的绝对路径，用于定位运行数据和 Compose 文件。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 所有时间戳备份目录的父目录。
BACKUP_ROOT="${ROOT_DIR}/backups"
# 本次备份使用的日期时间标识，避免覆盖既有备份。
STAMP="$(date +%Y%m%d_%H%M%S)"
# 本次备份归档文件及其目录的目标路径。
TARGET="${BACKUP_ROOT}/${STAMP}"

mkdir -p "${TARGET}"

cd "${ROOT_DIR}"

docker compose stop backend

# cleanup 在脚本退出时重启后端，避免备份失败后服务持续停止。
cleanup() {
  docker compose start backend >/dev/null 2>&1 || true
}

trap cleanup EXIT

tar -czf "${TARGET}/laborqa-runtime.tar.gz" \
  data \
  uploads

docker compose start backend

trap - EXIT

echo "Backup created: ${TARGET}/laborqa-runtime.tar.gz"
