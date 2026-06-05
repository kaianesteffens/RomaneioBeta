#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-smoke}"

VM_USER="${VM_USER:-eduardo}"
VM_HOST="${VM_HOST:-127.0.0.1}"
VM_PORT="${VM_PORT:-2222}"
VM_KEY="${VM_KEY:-$HOME/.ssh/fretio_vm}"
TASK_NAME="${TASK_NAME:-Fretio_VM_Test}"

SAFE_MODE="$(printf '%s' "$MODE" | tr -cd 'A-Za-z0-9_-')"
RUN_ID="$(date +%Y%m%d-%H%M%S)-${SAFE_MODE}"

LOCAL_LOG_DIR="logs/vm"
LOCAL_LOG="${LOCAL_LOG_DIR}/latest-${SAFE_MODE}.log"

mkdir -p "$LOCAL_LOG_DIR"

echo "Disparando teste na VM Windows..."
echo "Modo: $MODE"
echo "Run ID: $RUN_ID"

ssh -i "$VM_KEY" -p "$VM_PORT" "${VM_USER}@${VM_HOST}" \
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"Set-Content -Path 'C:/fretio-vm/requested-mode.txt' -Value '${MODE}'; Set-Content -Path 'C:/fretio-vm/requested-run-id.txt' -Value '${RUN_ID}'; schtasks /run /tn '${TASK_NAME}'\""

echo "Aguardando resultado..."

for i in {1..120}; do
  STATUS="$(
    ssh -i "$VM_KEY" -p "$VM_PORT" "${VM_USER}@${VM_HOST}" \
      "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"if (Test-Path 'C:/fretio-vm/results/${RUN_ID}/status.txt') { Get-Content -Raw 'C:/fretio-vm/results/${RUN_ID}/status.txt' }\"" 2>/dev/null \
      | tr -d '\r\n '
  )"

  if [[ "$STATUS" == "running" ]]; then
    echo "Ainda rodando..."
  fi

  if [[ "$STATUS" == "passed" || "$STATUS" == "failed" ]]; then
    ssh -i "$VM_KEY" -p "$VM_PORT" "${VM_USER}@${VM_HOST}" \
      "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"Get-Content -Raw 'C:/fretio-vm/results/${RUN_ID}/run.log'\"" > "$LOCAL_LOG" 2>/dev/null || true

    echo "Status: $STATUS"
    echo "Log local: $LOCAL_LOG"
    echo ""
    echo "==== Últimas linhas do log ===="
    tail -n 160 "$LOCAL_LOG" || true

    if [[ "$STATUS" == "passed" ]]; then
      exit 0
    else
      exit 1
    fi
  fi

  sleep 3
done

echo "ERRO: timeout aguardando resultado da VM."
echo "Run ID: $RUN_ID"
echo "Verifique na VM:"
echo "C:/fretio-vm/results/$RUN_ID"
exit 1
