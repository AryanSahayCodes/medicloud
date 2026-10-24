#!/bin/bash
# Redeploy the latest code from GitHub. Run on the server:  sudo bash /opt/medicloud/deploy/update.sh
set -euo pipefail
cd /opt/medicloud
git pull
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build
docker image prune -f
docker compose ps
