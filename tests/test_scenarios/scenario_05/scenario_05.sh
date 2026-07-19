#!/bin/bash

set -x

export $(grep -v '^#' .env | xargs)

git clone https://github.com/AGH-iot-agent/iot-agent-alert-api.git

cd iot-agent-alert-api
RANDOM_SUFFIX=$(date +%s)

git checkout -b test/scenario_05_$RANDOM_SUFFIX

rm ./Helm/values-dev.yaml ./Helm/values-sbx.yaml
cp ../values-dev.yaml ./Helm/values-dev.yaml
cp ../values-sbx.yaml ./Helm/values-sbx.yaml

git add .
git commit -m "Update Helm values for scenario 05"

git push origin test/scenario_05_$RANDOM_SUFFIX

gh pr create \
  --title "Test PR for scenario 05: CrashLoopBackOff due to incorrect liveness probe port in Helm values" \
  --body "This PR is created to test the DevOps agent's ability to detect CrashLoopBackOff caused by an incorrect liveness probe port (9999) in the Helm chart values for iot-agent-alert-api. The correct port name is 'default-service'." \
  --base main

cd ..
rm -rf iot-agent-alert-api
