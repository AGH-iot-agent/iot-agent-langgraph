#!/bin/bash
# Cleanup scenario_11 — kill all background curl workers

echo "[cleanup_11] Killing background curl workers targeting iot-agent-alert-api..."
pkill -f 'curl.*iot-agent-alert-api' 2>/dev/null \
  && echo "[cleanup_11] Workers killed." \
  || echo "[cleanup_11] No workers found (already stopped)."
echo "[cleanup_11] Done."
