#!/usr/bin/env bash
# Reels AI Hunter — secure Docker entrypoint.
set -Eeuo pipefail

DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1280}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1024}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
WEB_PORT="${WEB_PORT:-8080}"
WEB_BIND_HOST="${WEB_BIND_HOST:-0.0.0.0}"
LOG_DIR="${LOG_DIR:-/var/log/reels-hunter}"

mkdir -p "$LOG_DIR" /run/xpra

log()  { printf '[%s] [INFO]  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_DIR/entrypoint.log"; }
warn() { printf '[%s] [WARN]  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_DIR/entrypoint.log"; }
err()  { printf '[%s] [ERROR] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG_DIR/entrypoint.log" >&2; }

cleanup() {
    status=$?
    trap - EXIT SIGINT SIGTERM
    [[ -n "${AGENT_PID:-}" ]] && kill "$AGENT_PID" 2>/dev/null || true
    if [[ -n "${XPRA_PID:-}" ]]; then
        xpra stop "$DISPLAY" >/dev/null 2>&1 || kill "$XPRA_PID" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT SIGINT SIGTERM

# Xpra's env authenticator reads XPRA_PASSWORD. Never expose an unauthenticated
# remote desktop. Generate an ephemeral password when one was not supplied.
if [[ -z "${XPRA_PASSWORD:-}" ]]; then
    err "XPRA_PASSWORD is required. Refusing to expose the HTML5 desktop without an explicit password."
    exit 2
fi
export XPRA_PASSWORD

if [[ -z "${INSTAGRAM_SESSION_COOKIES:-}" ]]; then
    warn "INSTAGRAM_SESSION_COOKIES is not set. The agent may not be able to access authenticated Instagram content."
fi
[[ -z "${GEMINI_API_KEY:-}" ]] && warn "GEMINI_API_KEY is not set; configured vision/text fallbacks will be used."
[[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]] && warn "Telegram notifications are disabled unless both Telegram variables are set."

log "Starting Reels AI Hunter"
log "Desktop: ${WEB_BIND_HOST}:${WEB_PORT} -> ${DISPLAY} (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})"

# Password authentication is mandatory on the TCP/HTML5 endpoint.
xpra start-desktop "$DISPLAY" \
    --bind-tcp="${WEB_BIND_HOST}:${WEB_PORT},auth=env" \
    --html=on \
    --start=fluxbox \
    --daemon=no \
    --pixel-depth="${SCREEN_DEPTH}" \
    --dpi=96 \
    --desktop-scaling=off \
    --opengl=no \
    --mdns=no \
    --notifications=no \
    --bell=no \
    --speaker=off \
    --microphone=off \
    --webcam=no \
    --clipboard=no \
    --file-transfer=no \
    --printing=no \
    >"$LOG_DIR/xpra.log" 2>&1 &
XPRA_PID=$!

log "Waiting for Xpra to become ready..."
for i in $(seq 1 30); do
    if ! kill -0 "$XPRA_PID" 2>/dev/null; then
        err "Xpra exited during startup."
        tail -40 "$LOG_DIR/xpra.log" >&2 || true
        exit 1
    fi
    if xpra info "$DISPLAY" >/dev/null 2>&1; then
        log "Xpra is ready (PID=$XPRA_PID)."
        break
    fi
    if [[ "$i" -eq 30 ]]; then
        err "Xpra did not become ready within 30 seconds."
        tail -40 "$LOG_DIR/xpra.log" >&2 || true
        exit 1
    fi
    sleep 1
done

export DISPLAY
log "Launching agent..."
python /app/agent.py \
    > >(tee -a "$LOG_DIR/agent.log") \
    2> >(tee -a "$LOG_DIR/agent_err.log" >&2) &
AGENT_PID=$!

set +e
wait "$AGENT_PID"
AGENT_EXIT=$?
set -e

if [[ "$AGENT_EXIT" -eq 0 ]]; then
    log "Agent completed successfully."
else
    err "Agent exited with code $AGENT_EXIT."
fi
exit "$AGENT_EXIT"
