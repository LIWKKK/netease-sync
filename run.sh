#!/bin/bash
# 网易云歌单每日增量同步 —— 定时任务入口
set -uo pipefail
cd "$(dirname "$0")" || exit 1

LOCK="state/.lock"
mkdir -p state logs
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 上一轮还在跑，跳过本次" >> logs/cron.log
  exit 0
fi

PY=/usr/bin/python3
"$PY" sync.py "$@" >> logs/cron.log 2>&1
code=$?
if [ $code -ne 0 ]; then
  echo "[$(date '+%F %T')] 退出码 $code" >> logs/cron.log
fi
# 日志按 5 MB 截断，保留最近一份
if [ -f logs/cron.log ] && [ "$(stat -c%s logs/cron.log)" -gt 5242880 ]; then
  tail -n 20000 logs/cron.log > logs/cron.log.tmp && mv logs/cron.log.tmp logs/cron.log
fi
exit $code
