#!/bin/bash

set -x

export $(grep -v '^#' .env | xargs)

git clone https://github.com/AGH-iot-agent/iot-agent-stream-worker.git

cd iot-agent-stream-worker
RANDOM_SUFFIX=$(date +%s)

git checkout -b test/scenario_07_$RANDOM_SUFFIX

rm ./Helm/values-dev.yaml ./Helm/values-sbx.yaml
cp ../values-dev.yaml ./Helm/values-dev.yaml
cp ../values-sbx.yaml ./Helm/values-sbx.yaml

git add .
git commit -m "Update Helm values for scenario 07"

git push origin test/scenario_07_$RANDOM_SUFFIX

gh pr create \
  --title "Test PR for scenario 07: OOMKilled due to insufficient memory limits in Helm values" \
  --body "This PR is created to test the DevOps agent's ability to detect pod OOMKilled failures caused by an insufficient memory limit (32Mi) in the Helm chart values for iot-agent-stream-worker. A Spring Boot service requires at least 256Mi to start." \
  --base main

cd ..
rm -rf iot-agent-stream-worker
