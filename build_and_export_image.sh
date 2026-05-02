#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="smart-notifier:phase3"
OUTPUT_TAR="/Users/cwzs/Desktop/smart-notifier-phase3.tar"

echo "[1/3] Building image: ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" .

echo "[2/3] Exporting image to: ${OUTPUT_TAR}"
docker save -o "${OUTPUT_TAR}" "${IMAGE_TAG}"

echo "[3/3] Done"
ls -lh "${OUTPUT_TAR}"
