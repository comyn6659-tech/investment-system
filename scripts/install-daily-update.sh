#!/bin/sh
# 安装每日前向更新定时任务（macOS launchd）。
# 必须由股东手动执行一次；本仓库的代码不会自动调用本脚本。
#
# 用法：
#   scripts/install-daily-update.sh --dry-run   # 只打印将写入的 plist 与命令
#   scripts/install-daily-update.sh             # 实际安装
#
# 卸载：
#   scripts/uninstall-daily-update.sh
set -eu

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.investment-system.forward-update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$PROJECT/.venv/bin/python"
LOG_DIR="$PROJECT/data/logs"
DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

if [ ! -x "$PYTHON" ]; then
  echo "找不到可执行解释器：$PYTHON" >&2
  echo "请先在项目根目录创建 .venv 并安装依赖。" >&2
  exit 1
fi

PLIST_CONTENT="$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$PROJECT/tools/update.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>18</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/daily-update.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/daily-update.err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST
)"

if [ "$DRY_RUN" = "1" ]; then
  echo "dry-run：将写入 $PLIST"
  echo "----- plist 内容 -----"
  printf '%s\n' "$PLIST_CONTENT"
  echo "----------------------"
  echo "dry-run：将执行 launchctl unload "$PLIST" 2>/dev/null || true"
  echo "dry-run：将执行 launchctl load -w "$PLIST""
  exit 0
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "本安装脚本只支持 macOS（launchd）。" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
printf '%s\n' "$PLIST_CONTENT" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "已安装每日 18:30 前向更新任务：$LABEL"
echo "查看日志：$LOG_DIR/daily-update.log"
echo "卸载命令：$PROJECT/scripts/uninstall-daily-update.sh"
