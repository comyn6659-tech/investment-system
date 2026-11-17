#!/bin/sh
# 卸载每日前向更新定时任务（macOS launchd）。已有账本与日志不删除。
# 用法：
#   scripts/uninstall-daily-update.sh --dry-run
#   scripts/uninstall-daily-update.sh
set -eu

LABEL="com.investment-system.forward-update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "dry-run：将执行 launchctl unload -w \"$PLIST\" 2>/dev/null || true"
  echo "dry-run：将删除 $PLIST"
  exit 0
fi

launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已卸载定时任务：${LABEL}（账本与日志保留）"
