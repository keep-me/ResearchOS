#!/bin/bash
# ResearchOS 数据备份脚本
#
# 用法: 添加到 crontab:
#   0 3 * * * /opt/researchos/backup.sh >> /opt/researchos/backups/backup.log 2>&1

set -euo pipefail

DEPLOY_DIR="${RESEARCHOS_DEPLOY_DIR:-/opt/researchos/deploy}"
BACKUP_DIR="${RESEARCHOS_BACKUP_DIR:-/opt/researchos/backups}"
DATA_DIR="$DEPLOY_DIR/data"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

# SQLite 在线备份（不锁库）
DB_FILE="$DATA_DIR/researchos.db"
if [ -f "$DB_FILE" ]; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/researchos_$DATE.db'"
    echo "[$(date)] DB backup: researchos_$DATE.db"
else
    echo "[$(date)] WARNING: DB file not found at $DB_FILE"
fi

# PDF 和 Briefs 增量打包
if [ -d "$DATA_DIR/papers" ] || [ -d "$DATA_DIR/briefs" ]; then
    tar -czf "$BACKUP_DIR/papers_$DATE.tar.gz" \
        -C "$DATA_DIR" papers/ briefs/ 2>/dev/null || true
    echo "[$(date)] Files backup: papers_$DATE.tar.gz"
fi

# 清理过期备份
find "$BACKUP_DIR" -name "researchos_*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
find "$BACKUP_DIR" -name "papers_*.tar.gz" -mtime +$KEEP_DAYS -delete 2>/dev/null || true

echo "[$(date)] Backup completed. Retained last $KEEP_DAYS days."
