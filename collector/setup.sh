#!/usr/bin/env bash
# 오라클 Ubuntu VM에서 한 번만 실행: bash setup.sh
set -e
cd "$(dirname "$0")"
sudo timedatectl set-timezone Asia/Seoul || true
sudo apt-get update -y && sudo apt-get install -y python3-pip git nano
pip3 install --user -r requirements.txt 2>/dev/null || pip3 install --break-system-packages -r requirements.txt
[ -f .env ] || cp .env.example .env
[ -f config.json ] || cp config.example.json config.json
chmod 600 .env
mkdir -p state
echo
echo "공인 IP: $(curl -s https://api.ipify.org)   ← 토스·업비트에 이 IP 등록"
echo "다음: nano .env  →  nano config.json  →  python3 collector.py --check"
