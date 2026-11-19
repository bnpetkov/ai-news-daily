#!/usr/bin/env bash
# One-shot setup on the Ubuntu home server.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"

echo "▶ system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg python3-venv python3-pip fonts-inter >/dev/null || sudo apt-get install -y -qq ffmpeg python3-venv python3-pip

echo "▶ python venv"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "▶ offline French voice (Piper fallback)"
V=assets/voices
B=https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium
[ -f $V/fr_FR-siwis-medium.onnx ] || curl -sSL -o $V/fr_FR-siwis-medium.onnx $B/fr_FR-siwis-medium.onnx
[ -f $V/fr_FR-siwis-medium.onnx.json ] || curl -sSL -o $V/fr_FR-siwis-medium.onnx.json $B/fr_FR-siwis-medium.onnx.json

[ -f .env ] || cp .env.example .env

echo "▶ checks"
command -v claude >/dev/null && echo "  claude CLI: OK" || echo "  ⚠ claude CLI not found — install Claude Code or set llm.backend: anthropic_api"

echo "▶ systemd user timer (05:30 every day)"
mkdir -p ~/.config/systemd/user
CLAUDE_DIR="$(dirname "$(command -v claude 2>/dev/null || echo /usr/local/bin/claude)")"
sed -e "s#%h/ai-news-daily#$DIR#g" \
    -e "s#^ExecStart=#Environment=PATH=$CLAUDE_DIR:/usr/local/bin:/usr/bin:/bin\nExecStart=#" \
    deploy/ia1minute.service > ~/.config/systemd/user/ia1minute.service
cp deploy/ia1minute.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ia1minute.timer
sudo loginctl enable-linger "$USER"   # run even when nobody is logged in
systemctl --user list-timers ia1minute.timer --no-pager

echo "✔ done. Test now with:  .venv/bin/python run_daily.py --force"
