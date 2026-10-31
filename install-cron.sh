#!/bin/bash
# 安装/更新定时任务：每天 05:30 同步新歌
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SCHEDULE="${SCHEDULE:-30 5 * * *}"
MARK="# netease-sync"
LINE="$SCHEDULE /bin/bash $DIR/run.sh $MARK"

current=$(crontab -l 2>/dev/null | grep -v "$MARK" || true)
{ echo "$current"; echo "$LINE"; } | sed '/^$/d' | crontab -

echo "已安装："
crontab -l | grep "$MARK"
echo "日志：$DIR/logs/cron.log"
