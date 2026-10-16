#!/bin/sh
set -eu
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing local/plant-images local/ai-artifacts
mc anonymous set none local/plant-images
mc anonymous set none local/ai-artifacts
mc admin user add local "$MINIO_APP_USER" "$MINIO_APP_PASSWORD"
mc admin policy create local plantcare-app /bootstrap/app-policy.json
mc admin policy attach local plantcare-app --user "$MINIO_APP_USER"
echo 'Private buckets and application credentials are ready.'
