#!/usr/bin/env bash
# aidiscuss.sh  AIDiscuss skill 包装器（Git Bash / WSL / Linux）
# 会自动定位项目根与 .venv 里的 Python；start 子命令转交 PowerShell 版。
set -euo pipefail
export NO_COLOR=1

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

to_host_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"
  elif command -v wslpath >/dev/null 2>&1; then wslpath -u "$1"
  else printf '%s\n' "$1"; fi
}

read_conf() {
  local key="$1" file="$SCRIPT_DIR/../runtime.conf"
  [ -f "$file" ] || return 0
  sed -n "s/^[[:space:]]*${key}[[:space:]]*=//p" "$file" | head -n1 | sed 's/[[:space:]]*$//'
}

ROOT=""
if [ -n "${AIDISCUSS_HOME:-}" ] && [ -f "$AIDISCUSS_HOME/app/cli.py" ]; then
  ROOT="$AIDISCUSS_HOME"
fi
if [ -z "$ROOT" ]; then
  cand="$(read_conf project)"
  if [ -n "$cand" ] && [ -f "$(to_host_path "$cand")/app/cli.py" ]; then
    ROOT="$(to_host_path "$cand")"
  fi
fi
if [ -z "$ROOT" ] && [ -f "$SCRIPT_DIR/../../../app/cli.py" ]; then
  ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
fi
if [ -z "$ROOT" ]; then
  echo "aidiscuss: 找不到 AIDiscuss 项目根，请设置 AIDISCUSS_HOME。" >&2
  exit 1
fi

if [ "${1:-}" = "start" ]; then
  PS1="$(to_host_path "$SCRIPT_DIR/aidiscuss.ps1")"
  if command -v pwsh >/dev/null 2>&1; then
    exec pwsh -NoProfile -ExecutionPolicy Bypass -File "$PS1" "$@"
  else
    exec powershell -NoProfile -ExecutionPolicy Bypass -File "$PS1" "$@"
  fi
fi

PY="$(read_conf python)"
if [ -n "$PY" ]; then PY="$(to_host_path "$PY")"; fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
  elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
  else PY="$(command -v python3 || command -v python)"; fi
fi

cd -- "$ROOT"
exec "$PY" -m app.cli "$@"