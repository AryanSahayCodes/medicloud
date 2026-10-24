#!/bin/bash
# Failure injection for the TTR experiment. Run ON THE SERVER (Session Manager):
#   sudo bash /opt/medicloud/tools/inject_failure.sh crash-api   # kill -9 the app container's process
#   sudo bash /opt/medicloud/tools/inject_failure.sh crash-db    # kill -9 the database process
#   sudo bash /opt/medicloud/tools/inject_failure.sh status
# Keep tools/uptime_probe.py running on your laptop meanwhile: it measures the real TTR.
set -euo pipefail
cd /opt/medicloud
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.aws.yml"
ts() { date -u +%H:%M:%S.%3N; }

crash() {
  local svc=$1
  local cid pid before restarts state
  cid=$($COMPOSE ps -q "$svc")
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid")
  before=$(docker inspect -f '{{.RestartCount}}' "$cid")
  echo "[$(ts) UTC] Injecting failure: SIGKILL to $svc (container ${cid:0:12}, host pid $pid)"
  kill -9 "$pid"
  # Wait until Docker's restart policy has restarted the container and it is healthy again
  for i in $(seq 1 480); do
    restarts=$(docker inspect -f '{{.RestartCount}}' "$cid" 2>/dev/null || echo "$before")
    state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null || echo "gone")
    if [[ "$restarts" -gt "$before" && "$state" == "running healthy" ]]; then
      echo "[$(ts) UTC] $svc restarted by Docker and healthy again (restart count $before -> $restarts)"
      return
    fi
    sleep 0.25
  done
  echo "[$(ts) UTC] $svc did not become healthy within 2 minutes; check: $COMPOSE logs $svc"
}

case "${1:-}" in
  crash-api) crash api ;;
  crash-db)  crash db ;;
  status)    $COMPOSE ps ;;
  *) echo "usage: $0 crash-api | crash-db | status"; exit 1 ;;
esac
