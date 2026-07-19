#!/bin/bash 

set -euo pipefail

export $(grep -v '^#' ../.dev_env | xargs)

# This script runs the tests for the project.
PROJECT_DIR=$(dirname "$0")/..
KUBECONFIG=/etc/rancher/k3s/k3s.yaml ./venv/bin/python3 -m pytest "$PROJECT_DIR/tests"