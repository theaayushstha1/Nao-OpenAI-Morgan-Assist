#!/usr/bin/env bash
#
# One-shot dev launcher for Nao-OpenAI-Morgan-Assist.
#
#   1. Validates .env (no PASTE_* placeholders left)
#   2. Detects this Mac's IP on the same subnet as the NAO
#   3. Rsyncs nao/ to the robot
#   4. Kills any stale main.py on the robot (prevents the multi-session bug)
#   5. Starts the Flask server locally on $SERVER_PORT
#   6. Waits for /health to respond
#   7. Launches a single main.py on the robot with the right env vars
#   8. Tails both logs (Ctrl-C to stop)
#
# Usage:  ./run.sh
#         ./run.sh deploy-only      # just rsync, don't launch
#         ./run.sh server-only      # just start the local server
#         ./run.sh stop             # kill local server + remote main.py

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# ─────────── helpers ───────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { printf "${CYAN}▸${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
die()  { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }

[ -f .env ] || die ".env not found at $PROJECT_ROOT/.env"

# Read .env into the current shell. set -a auto-exports each var.
set -a
# shellcheck disable=SC1091
source .env
set +a

# ─────────── validate keys ───────────
[ -n "${OPENAI_API_KEY:-}" ] && [[ "${OPENAI_API_KEY}" != PASTE_* ]] \
    || die "OPENAI_API_KEY is not set in .env (still PASTE_OPENAI_KEY_HERE?)"
[ -n "${DEEPGRAM_API_KEY:-}" ] && [[ "${DEEPGRAM_API_KEY}" != PASTE_* ]] \
    || warn "DEEPGRAM_API_KEY missing; server will fall back to Whisper."
[ -n "${NAO_IP:-}" ]            || die "NAO_IP not set in .env"
[ -n "${NAO_PASSWORD:-}" ]      || die "NAO_PASSWORD not set in .env"
[ -n "${SERVER_PORT:-}" ]       || SERVER_PORT=5050
[ -n "${NAO_SHARED_SECRET:-}" ] || warn "NAO_SHARED_SECRET empty (open mode)."

# ─────────── detect this Mac's IP that the robot can reach ───────────
# Pick the first non-loopback IPv4 on the same /24 as NAO_IP, otherwise
# fall back to the default-route interface address.
detect_local_ip() {
    local subnet ip
    subnet="$(echo "$NAO_IP" | awk -F. '{print $1"."$2"."$3"."}')"
    ip="$(ifconfig | awk '/inet /{print $2}' | grep "^$subnet" | head -n1 || true)"
    if [ -z "$ip" ]; then
        ip="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}' \
              | xargs -I{} ipconfig getifaddr {} 2>/dev/null || true)"
    fi
    [ -n "$ip" ] || die "could not detect local IP on subnet $subnet"
    echo "$ip"
}
LOCAL_IP="$(detect_local_ip)"
ok "local IP for NAO callback: $LOCAL_IP"

# ─────────── stop everything cleanly ───────────
do_stop() {
    log "stopping local Flask on :$SERVER_PORT"
    lsof -ti ":$SERVER_PORT" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    log "stopping main.py on robot"
    sshpass -p "$NAO_PASSWORD" ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        "nao@$NAO_IP" 'pkill -f "python.*main.py" 2>/dev/null; true' || true
    ok "stopped"
}

# ─────────── deploy NAO files to robot ───────────
do_deploy() {
    log "deploying nao/ to nao@$NAO_IP:/home/nao/nao_assist/"
    sshpass -p "$NAO_PASSWORD" rsync -az --delete \
        -e "ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no" \
        "$PROJECT_ROOT/nao/" "nao@$NAO_IP:/home/nao/nao_assist/"
    ok "deploy complete"
}

# ─────────── start Flask server ───────────
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
SERVER_LOG="$LOG_DIR/server.log"
ROBOT_LOG_REMOTE="/home/nao/nao_assist/nao.log"

start_server() {
    if lsof -ti ":$SERVER_PORT" >/dev/null 2>&1; then
        warn "port $SERVER_PORT already in use, killing"
        lsof -ti ":$SERVER_PORT" | xargs -r kill -9 2>/dev/null || true
        sleep 1
    fi
    log "starting Flask on 0.0.0.0:$SERVER_PORT (log: $SERVER_LOG)"
    nohup python -m flask --app server.server run \
        --host 0.0.0.0 --port "$SERVER_PORT" \
        > "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$LOG_DIR/server.pid"
    # wait for /health
    for i in $(seq 1 25); do
        if curl -sf "http://localhost:$SERVER_PORT/health" >/dev/null 2>&1; then
            ok "server healthy on :$SERVER_PORT (pid $SERVER_PID)"
            return 0
        fi
        sleep 0.4
    done
    die "server failed to come up — see $SERVER_LOG"
}

# ─────────── launch main.py on robot ───────────
start_robot() {
    log "killing any stale main.py on robot"
    sshpass -p "$NAO_PASSWORD" ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        "nao@$NAO_IP" 'pkill -f "python.*main.py" 2>/dev/null; sleep 1; true' || true

    log "launching main.py on $NAO_IP (server callback: $LOCAL_IP:$SERVER_PORT)"
    # naoqi bindings live at /opt/aldebaran on this NAO image; without
    # this PYTHONPATH the `import qi` at the top of main.py fails.
    sshpass -p "$NAO_PASSWORD" ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        "nao@$NAO_IP" "PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages \
LD_LIBRARY_PATH=/opt/aldebaran/lib:/opt/aldebaran/lib/naoqi \
SERVER_IP='$LOCAL_IP' SERVER_PORT='$SERVER_PORT' \
NAO_SHARED_SECRET='$NAO_SHARED_SECRET' \
nohup python -u /home/nao/nao_assist/main.py \
> $ROBOT_LOG_REMOTE 2>&1 </dev/null &"
    sleep 2
    # confirm exactly one process is running
    local count
    count="$(sshpass -p "$NAO_PASSWORD" ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        "nao@$NAO_IP" 'ps aux | grep -v grep | grep -c "python.*main.py"' || echo 0)"
    if [ "$count" = "1" ]; then
        ok "main.py running on robot (1 process)"
    else
        warn "expected 1 main.py on robot, found $count — check $ROBOT_LOG_REMOTE"
    fi
}

# ─────────── tail both logs side by side ───────────
do_tail() {
    log "tailing logs (Ctrl-C to stop, server keeps running)"
    echo
    # local server log + remote robot log via SSH
    (tail -f "$SERVER_LOG" | sed -e "s/^/${CYAN}[server]${NC} /") &
    TAIL_PID=$!
    trap "kill $TAIL_PID 2>/dev/null; exit 0" INT
    sshpass -p "$NAO_PASSWORD" ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        "nao@$NAO_IP" "tail -f $ROBOT_LOG_REMOTE" 2>/dev/null \
        | sed -e "s/^/${YELLOW}[robot]${NC}  /"
}

# ─────────── dispatch ───────────
case "${1:-all}" in
    stop)         do_stop ;;
    deploy-only)  do_deploy ;;
    server-only)  start_server; do_tail ;;
    all|"")       do_deploy; start_server; start_robot; do_tail ;;
    *)            die "unknown command: $1 (use: all | deploy-only | server-only | stop)" ;;
esac
