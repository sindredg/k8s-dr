#!/bin/bash
# Restores one backup set into the Gitea and PostgreSQL that this Pod's
# namespace pair runs. Selects the newest set with a manifest unless
# RESTORE_SET names one, verifies every digest and decrypts both archives
# before it changes anything, then stops Gitea, recreates the database,
# replaces the Gitea volume, and starts Gitea. ansible/playbooks/restore.yml
# runs it as a Job. See decision 0007.
set -euo pipefail

: "${RESTORE_SOURCE:?}" "${BACKUP_BUCKET:?}" "${NAMESPACE:?}"
: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}" "${PGPASSWORD:?}"
RESTORE_SET=${RESTORE_SET:-}

DEPLOYMENT=gitea
SELECTOR=app.kubernetes.io/name=gitea
REPLICAS=1
DATA=/data
STAGING=/staging
KEY=/restore-key/key
METADATA=http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token
OBJECTS="https://storage.googleapis.com/${BACKUP_BUCKET}"
FILES=(postgresql.dump.age gitea-data.tar.gz.age)

started=$(date +%s)
paused=false
# After the first destructive step, a failed restore leaves Gitea stopped so
# that nothing serves or writes to a partial restore. Rerun the restore.
destructive=false

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

resume_gitea() {
  if [[ "$paused" == true ]]; then
    kubectl -n "$NAMESPACE" scale deployment "$DEPLOYMENT" --replicas="$REPLICAS"
    paused=false
    log "resumed Gitea"
  fi
}

on_exit() {
  local status=$?
  if ((status != 0)); then
    if [[ "$destructive" == true ]]; then
      log "restore failed with status ${status}; Gitea left stopped"
    else
      resume_gitea || true
      log "restore failed with status ${status} before any change"
    fi
  fi
  exit "$status"
}
trap on_exit EXIT
# The script runs as PID 1; see backup.sh.
trap 'exit 143' TERM INT

gitea_pods() { kubectl -n "$NAMESPACE" get pods -l "$SELECTOR" -o name; }

token=$(curl -fsS -H "Metadata-Flavor: Google" "$METADATA" | jq -r .access_token)
get() { curl -fsS --retry 3 -H "Authorization: Bearer ${token}" "$@"; }

# The manifest is uploaded last, so a set without one is incomplete.
list_manifests() {
  local page="" response
  while :; do
    response=$(get -G "https://storage.googleapis.com/storage/v1/b/${BACKUP_BUCKET}/o" \
      --data-urlencode "matchGlob=${RESTORE_SOURCE}/*/manifest.json" \
      --data-urlencode "fields=items(name),nextPageToken" \
      ${page:+--data-urlencode "pageToken=${page}"})
    jq -r '.items[]?.name' <<<"$response"
    page=$(jq -r '.nextPageToken // empty' <<<"$response")
    [[ -n "$page" ]] || break
  done
}

if [[ -z "$RESTORE_SET" ]]; then
  # Set names are UTC timestamps, so the lexical maximum is the newest.
  RESTORE_SET=$(list_manifests | cut -d/ -f2 | sort | tail -n 1)
fi
if [[ ! "$RESTORE_SET" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  log "no complete set under ${RESTORE_SOURCE}/, or an invalid set name: '${RESTORE_SET}'"
  exit 1
fi
PREFIX="${RESTORE_SOURCE}/${RESTORE_SET}"
created=$(date -u -d "$(sed -E 's/^(.{4})(.{2})(.{2})T(.{2})(.{2})(.{2})Z$/\1-\2-\3T\4:\5:\6Z/' <<<"$RESTORE_SET")" +%s)
log "restoring ${PREFIX}; backup age at restore start $((started - created)) seconds"

get -o "${STAGING}/manifest.json" "${OBJECTS}/${PREFIX}/manifest.json"
jq -e --arg cluster "$RESTORE_SOURCE" --arg created "$RESTORE_SET" \
  '.format == 1 and .cluster == $cluster and .created == $created
   and ([.files[].name] | sort) == ["gitea-data.tar.gz.age", "postgresql.dump.age"]' \
  "${STAGING}/manifest.json" >/dev/null || { log "manifest does not describe ${PREFIX}"; exit 1; }

for name in "${FILES[@]}"; do
  get -o "${STAGING}/${name}" "${OBJECTS}/${PREFIX}/${name}"
  expected=$(jq -r --arg name "$name" '.files[] | select(.name == $name) | "\(.sha256) \(.size)"' "${STAGING}/manifest.json")
  actual="$(sha256sum "${STAGING}/${name}" | cut -d' ' -f1) $(stat -c %s "${STAGING}/${name}")"
  if [[ "$actual" != "$expected" ]]; then
    log "${name} does not match the manifest; choose another set with RESTORE_SET"
    exit 1
  fi
done
log "digests match the manifest"

# Decrypt both archives once before the restore, so that a wrong key or a
# damaged archive fails while the current data is still in place.
age --decrypt -i "$KEY" "${STAGING}/postgresql.dump.age" | pg_restore --list >/dev/null
age --decrypt -i "$KEY" "${STAGING}/gitea-data.tar.gz.age" | tar -tzf - >/dev/null
log "both archives decrypt and read"

paused=true
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

destructive=true
psql --dbname=postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${PGDATABASE}\" WITH (FORCE)" \
  -c "CREATE DATABASE \"${PGDATABASE}\" OWNER \"${PGUSER}\""
age --decrypt -i "$KEY" "${STAGING}/postgresql.dump.age" |
  pg_restore --dbname="$PGDATABASE" --exit-on-error --single-transaction --no-owner
log "restored the database"

find "$DATA" -mindepth 1 -delete
age --decrypt -i "$KEY" "${STAGING}/gitea-data.tar.gz.age" | tar -C "$DATA" -xzf -
log "restored the Gitea volume"
destructive=false

resume_gitea
kubectl -n "$NAMESPACE" rollout status deployment "$DEPLOYMENT" --timeout=600s
log "restored ${PREFIX} in $(($(date +%s) - started)) seconds"
