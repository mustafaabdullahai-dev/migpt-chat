#!/usr/bin/env bash
# Start the full stack + a public tunnel (ngrok by default, cloudflared as fallback).
# Avoids port conflicts: backend on 8100, frontend on 5200.
#
# Usage:
#   ./scripts/serve.sh                     # auto tunnel (cloudflared first, ngrok fallback)
#   ./scripts/serve.sh --tunnel=ngrok      # force ngrok (sole agent on the account URL)
#   ./scripts/serve.sh --tunnel=cloudflare # cloudflared quick tunnel (unique URL)
#   ./scripts/serve.sh --no-demo           # require a real GROQ_API_KEY
#
# TUNNEL CONFLICT NOTES
#   ngrok free  = ONE URL per account. If 2 agents run, the second steals the URL and
#                 stops the first (ERR_NGROK_334 / "received stop request"). So ngrok
#                 mode refuses to start if another local ngrok agent is already running.
#   cloudflared = unique trycloudflare URL per launch, no URL sharing conflict. Falls
#                 back if trycloudflare throttles (timeouts / "Unauthorized: Tunnel not found").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8100}"
FRONTEND_PORT="${FRONTEND_PORT:-5200}"
TUNNEL="auto"
DEMO="1"

for arg in "$@"; do
  case "$arg" in
    --tunnel=*) TUNNEL="${arg#*=}" ;;
    --no-demo)  DEMO="0" ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

stop_all() {
  [[ -n "${BE_PID:-}" ]] && kill "$BE_PID" 2>/dev/null || true
  [[ -n "${FE_PID:-}" ]] && kill "$FE_PID" 2>/dev/null || true
  [[ -n "${TUN_PID:-}" ]] && kill "$TUN_PID" 2>/dev/null || true
}
trap stop_all EXIT INT TERM

port_free() {
  ! (command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$1 ")
}

# --- backend ---
if ! port_free "$BACKEND_PORT"; then
  echo "Port $BACKEND_PORT is busy; set BACKEND_PORT=<free port> ${0##*/} --no-demo" >&2
  exit 1
fi
export DEMO_MODE="$DEMO"
(cd "$ROOT/backend" && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT") > /tmp/chatgpt-backend.log 2>&1 &
BE_PID=$!

# --- frontend ---
if ! port_free "$FRONTEND_PORT"; then
  echo "Port $FRONTEND_PORT is busy; set FRONTEND_PORT=<free port>" >&2
  exit 1
fi
(cd "$ROOT/frontend" && VITE_API_TARGET="http://localhost:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" --host 0.0.0.0) > /tmp/chatgpt-frontend.log 2>&1 &
FE_PID=$!

echo "Booting backend :$BACKEND_PORT and frontend :$FRONTEND_PORT …"
for i in $(seq 1 30); do
  curl -sf "http://localhost:$BACKEND_PORT/api/health" >/dev/null && break
  sleep 1
done
for i in $(seq 1 30); do
  curl -sf "http://localhost:$FRONTEND_PORT" >/dev/null && break
  sleep 1
done

echo "Backend :$BACKEND_PORT  Frontend :$FRONTEND_PORT  (backend already running?) -> check ports above"

start_ngrok() {
  if pgrep -f "ngrok http" >/dev/null 2>&1; then
    echo "ERROR: another ngrok agent is already running on this account." >&2
    echo "  ngrok free = ONE public URL per account; two agents will conflict." >&2
    echo "  Use --tunnel=cloudflare (unique URL, no conflict) instead." >&2
    exit 1
  fi
  ngrok http "$FRONTEND_PORT" --name=chatgpt-live --log=stdout --log-format=json > /tmp/ngrok-chatgpt.log 2>&1 &
  TUN_PID=$!
  for i in $(seq 1 20); do
    URL="$(grep -ao '"public_url":"[^"]*"' /tmp/ngrok-chatgpt.log 2>/dev/null | tail -1 | sed 's/.*"\(.*\)"/\1/')"
    [[ -n "$URL" ]] && break
    sleep 1
  done
}

start_cloudflare() {
  cloudflared tunnel --url "http://localhost:$FRONTEND_PORT" --no-autoupdate > /tmp/cloudflare-chatgpt.log 2>&1 &
  TUN_PID=$!
  for i in $(seq 1 30); do
    URL="$(grep -aoE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflare-chatgpt.log 2>/dev/null | head -1)"
    # a fresh URL is only real once the edge accepts it; check for failure markers too
    [[ -n "$URL" ]] && break
    grep -aqE 'Unauthorized|failed to request quick Tunnel|FATAL' /tmp/cloudflare-chatgpt.log 2>/dev/null \
      && echo "cloudflared refused the tunnel; falling back to ngrok" >&2 && return 1
    sleep 1
  done
  [[ -n "$URL" ]]
}

case "$TUNNEL" in
  auto)
    start_cloudflare || { TUN_PID=""; start_ngrok; }
    ;;
  ngrok)     start_ngrok ;;
  cloudflare) start_cloudflare || true ;;
  *)
    echo "unknown tunnel: $TUNNEL" >&2; exit 1
    ;;
esac

if [[ -z "${URL:-}" ]]; then
  echo "Tunnel failed to start — see /tmp/${TUNNEL}-chatgpt.log" >&2
  exit 1
fi

echo
echo "══════════════════════════════════════════════════"
echo "  Public UI :  $URL"
echo "  API       :  $URL/api/health"
echo "  (Ctrl+C to stop everything)"
echo "══════════════════════════════════════════════════"

wait