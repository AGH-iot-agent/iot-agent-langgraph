#!/bin/bash
# Cleanup scenario_03 — kill all background curl workers launched by scenario_03.sh

echo "[cleanup_03] Killing background curl workers..."
# Kill all curl processes belonging to the current user that target the gateway
pkill -f 'curl.*iot-agent-gateway-api' 2>/dev/null && echo "[cleanup_03] Killed curl workers." || echo "[cleanup_03] No curl workers found (already stopped)."
echo "[cleanup_03] Done."

