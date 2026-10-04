#!/bin/bash
# Scheduled consistent backup of PostgreSQL and the Gitea volume. Scales Gitea
# to zero, streams an encrypted pg_dump and volume archive to local staging,
# scales Gitea back up, then uploads the objects and a manifest written last.
# A set without a manifest is incomplete and restore ignores it. See
# decision 0007.
set -euo pipefail

: "${BACKUP_CLUSTER:?}" "${BACKUP_BUCKET:?}" "${PING_URL:?}"
: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}" "${PGPASSWORD:?}"

NAMESPACE=gitea
DEPLOYMENT=gitea
SELECTOR=app.kubernetes.io/name=gitea
# The HelmRelease runs one replica. Resuming to a fixed count also repairs a
# Deployment left at zero by a run that was killed mid-capture.
REPLICAS=1
DATA=/data
STAGING=/staging
RECIPIENTS=/backup/recipients.txt
METADATA=http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token
CREATED=$(date -u +%Y%m%dT%H%M%SZ)
PREFIX="${BACKUP_CLUSTER}/${CREATED}"

paused=false

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# The heartbeat must never fail the backup, and a failed ping is only logged.
heartbeat() {
  curl -fsS -m 10 --retry 3 -o /dev/null "$@" || log "heartbeat ping failed"
}

resume_gitea() {
  if [[ "$paused" == true ]]; then
    kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas="$REPLICAS"
    paused=false
    log "resumed Gitea"
  fi
}

on_exit() {
  local status=$?
  resume_gitea || status=1
  if ((status != 0)); then
    log "backup ${PREFIX} failed with status ${status}"
    heartbeat --data-raw "backup ${PREFIX} failed" "${PING_URL}/fail"
  fi
  exit "$status"
}
trap on_exit EXIT
# The script runs as PID 1, which ignores SIGTERM without a handler. Exit so
# the EXIT trap resumes Gitea when the Job deadline or a node drain stops the
# Pod. SIGKILL skips the trap; the next run then resumes Gitea.
trap 'exit 143' TERM INT

gitea_pods() { kubectl -n "$NAMESPACE" get pods -l "$SELECTOR" -o name; }

# Uploads with the XML API. Content-MD5 makes Cloud Storage reject a corrupt
# upload, and generation-match 0 refuses to replace an existing object.
upload() {
  local file=$1 name=$2 type=$3 md5
  md5=$(md5sum "$file" | cut -d' ' -f1 | tr a-f A-F | basenc --base16 -d | base64)
  curl -fsS --retry 3 -o /dev/null -X PUT -T "$file" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-MD5: ${md5}" \
    -H "Content-Type: ${type}" \
    -H "x-goog-if-generation-match: 0" \
    "https://storage.googleapis.com/${BACKUP_BUCKET}/${PREFIX}/${name}"
}

heartbeat "${PING_URL}/start"
log "backup ${PREFIX} started"

gitea_version=$(kubectl -n "$NAMESPACE" get deployment "$DEPLOYMENT" \
  -o jsonpath='{.metadata.labels.app\.kubernetes\.io/version}')

paused=true
pause_started=$(date +%s)
kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas=0
for _ in $(seq 60); do
  [[ -z "$(gitea_pods)" ]] && break
  sleep 3
done
if [[ -n "$(gitea_pods)" ]]; then
  log "Gitea did not stop within 180 seconds"
  exit 1
fi
log "Gitea stopped"

pg_dump --format=custom | age --encrypt -R "$RECIPIENTS" -o "${STAGING}/postgresql.dump.age"
tar -C "$DATA" -cf - . | gzip | age --encrypt -R "$RECIPIENTS" -o "${STAGING}/gitea-data.tar.gz.age"

resume_gitea
pause_seconds=$(($(date +%s) - pause_started))
log "capture paused Gitea for ${pause_seconds} seconds"

token=$(curl -fsS -H "Metadata-Flavor: Google" "$METADATA" | jq -r .access_token)
files=()
for name in postgresql.dump.age gitea-data.tar.gz.age; do
  upload "${STAGING}/${name}" "$name" application/octet-stream
  files+=("$(jq -n --arg name "$name" \
    --arg sha256 "$(sha256sum "${STAGING}/${name}" | cut -d' ' -f1)" \
    --argjson size "$(stat -c %s "${STAGING}/${name}")" \
    '{name: $name, sha256: $sha256, size: $size}')")
done

printf '%s\n' "${files[@]}" | jq -s \
  --arg cluster "$BACKUP_CLUSTER" \
  --arg created "$CREATED" \
  --argjson pause_seconds "$pause_seconds" \
  --arg gitea "$gitea_version" \
  --arg postgresql "$(pg_dump --version)" \
  '{format: 1, cluster: $cluster, created: $created, pause_seconds: $pause_seconds,
    versions: {gitea: $gitea, pg_dump: $postgresql}, files: .}' >"${STAGING}/manifest.json"
upload "${STAGING}/manifest.json" manifest.json application/json
log "uploaded ${PREFIX}"

kubectl -n "$NAMESPACE" rollout status deployment "$DEPLOYMENT" --timeout=300s
heartbeat --data-raw "backup ${PREFIX} complete, Gitea paused ${pause_seconds}s" "$PING_URL"
log "backup ${PREFIX} complete"
