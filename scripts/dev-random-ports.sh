#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
PORT_FILE="$RUNTIME_DIR/ports.env"
BACK_LOG="$RUNTIME_DIR/backend.log"
FRONT_LOG="$RUNTIME_DIR/frontend.log"
BACK_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONT_PID_FILE="$RUNTIME_DIR/frontend.pid"

pick_port() {
  python3 - <<'PY'
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

ensure_unique_ports() {
  BACKEND_PORT="${BACKEND_PORT:-$(pick_port)}"
  FRONTEND_PORT="${FRONTEND_PORT:-$(pick_port)}"
  while [ "$FRONTEND_PORT" = "$BACKEND_PORT" ]; do
    FRONTEND_PORT="$(pick_port)"
  done
}

ensure_runtime_dirs() {
  mkdir -p "$RUNTIME_DIR"
}

write_runtime_config() {
  cat > "$PORT_FILE" <<EOF
BACKEND_PORT=$BACKEND_PORT
FRONTEND_PORT=$FRONTEND_PORT
API_BASE=http://127.0.0.1:$BACKEND_PORT
EOF

  cat > "$ROOT_DIR/frontend/.env.local" <<EOF
NEXT_PUBLIC_API_BASE=http://127.0.0.1:$BACKEND_PORT
EOF
}

start_backend() {
  if [ ! -d "$ROOT_DIR/backend/.venv" ]; then
    echo "backend/.venv 不存在，请先在 backend 目录创建虚拟环境并安装依赖。" >&2
    exit 1
  fi

  (
    cd "$ROOT_DIR/backend"
    EXTERNAL_GEMINI_API_KEY="${GEMINI_API_KEY:-}"
    if [ -z "${EXTERNAL_GEMINI_API_KEY:-}" ] && command -v security >/dev/null 2>&1; then
      EXTERNAL_GEMINI_API_KEY="$(security find-generic-password -a "$USER" -s "study-assistant-gemini-api-key" -w 2>/dev/null || true)"
    fi
    if [ -f .env ]; then
      set -a
      source .env
      set +a
    fi
    if [ -z "${GEMINI_API_KEY:-}" ] && [ -n "${EXTERNAL_GEMINI_API_KEY:-}" ]; then
      export GEMINI_API_KEY="$EXTERNAL_GEMINI_API_KEY"
    fi
    source .venv/bin/activate
    exec uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT"
  ) > "$BACK_LOG" 2>&1 &

  echo $! > "$BACK_PID_FILE"
}

start_frontend() {
  (
    cd "$ROOT_DIR/frontend"
    exec npm run dev -- --port "$FRONTEND_PORT"
  ) > "$FRONT_LOG" 2>&1 &

  echo $! > "$FRONT_PID_FILE"
}

cleanup() {
  if [ -f "$BACK_PID_FILE" ]; then
    kill "$(cat "$BACK_PID_FILE")" >/dev/null 2>&1 || true
    rm -f "$BACK_PID_FILE"
  fi
  if [ -f "$FRONT_PID_FILE" ]; then
    kill "$(cat "$FRONT_PID_FILE")" >/dev/null 2>&1 || true
    rm -f "$FRONT_PID_FILE"
  fi
}

main() {
  ensure_runtime_dirs
  ensure_unique_ports
  write_runtime_config

  start_backend
  start_frontend

  echo "随机端口已分配: backend=$BACKEND_PORT frontend=$FRONTEND_PORT"
  echo "学习助手地址: http://127.0.0.1:$FRONTEND_PORT"
  echo "后端 API 地址: http://127.0.0.1:$BACKEND_PORT"
  echo "日志文件: $BACK_LOG 和 $FRONT_LOG"

  trap cleanup INT TERM EXIT

  wait "$(cat "$BACK_PID_FILE")" "$(cat "$FRONT_PID_FILE")"
}

main "$@"
