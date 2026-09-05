#!/bin/bash
# Cleanup scenario_18 — kill all background curl workers launched by scenario_18.sh

echo "[cleanup_18] Killing background curl workers..."
pkill -f 'curl.*iot-agent-gateway-api' 2>/dev/null && echo "[cleanup_18] Killed curl workers." || echo "[cleanup_18] No curl workers found (already stopped)."
echo "[cleanup_18] Done."
