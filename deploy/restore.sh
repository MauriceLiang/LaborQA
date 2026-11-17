#!/usr/bin/env bash
# 文件作用：先备份当前数据，再将指定归档恢复到项目目录并启动服务。

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <backup.tar.gz>"
  exit 1
fi

# 项目根目录的绝对路径，作为恢复文件的目标位置。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 用户传入备份归档的规范化绝对路径。
BACKUP_FILE="$(realpath "$1")"
# 本次恢复前保护性备份使用的日期时间标识。
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "${ROOT_DIR}"

docker compose down

mkdir -p "backups/pre-restore-${STAMP}"

if [ -d data ] || [ -d uploads ]; then
  tar -czf \
    "backups/pre-restore-${STAMP}/current-runtime.tar.gz" \
    data \
    uploads
fi

rm -rf data uploads

tar -xzf "${BACKUP_FILE}" -C "${ROOT_DIR}"

docker compose up -d

docker compose ps
