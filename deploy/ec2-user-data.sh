#!/bin/bash
# EC2 user data for MediCloud (Amazon Linux 2023). Runs once, as root, on first boot.
# Before pasting into the EC2 launch wizard, change REPO_URL to YOUR GitHub repo.
# Progress log on the server: /var/log/cloud-init-output.log
set -euxo pipefail

REPO_URL="https://github.com/YOUR-GITHUB-USERNAME/medicloud.git"
APP_DIR=/opt/medicloud

# Region of this instance (for CloudWatch Logs)
TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

# 1. Docker engine, started on every boot
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

# 2. Docker Compose + Buildx plugins
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
curl -fsSL https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-amd64 \
  -o /usr/local/lib/docker/cli-plugins/docker-buildx
chmod +x /usr/local/lib/docker/cli-plugins/*

# 3. 1 GB swap so a t3.micro (1 GB RAM) doesn't run out of memory
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap defaults 0 0' >> /etc/fstab
fi

# 4. Get the code and a random DB password (kept only on the server, never in git)
git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"
echo "DB_PASSWORD=$(openssl rand -hex 16)" > .env
echo "AWS_REGION=$REGION" >> .env
chmod 600 .env

# 5. Build and start: API container + PostgreSQL container, logs to CloudWatch
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --build
docker compose ps
echo "MediCloud deployment finished"
