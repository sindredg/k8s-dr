#!/bin/bash
# Scheduled backup of PostgreSQL and the Gitea volume from one LVM snapshot of
# the worker data volume, while Gitea keeps running. Each container of the
# backup Pod runs one subcommand:
#
#   snapshot         Native sidecar, privileged: creates the snapshot on the
#                    host, waits, and removes it when the Pod stops.
#   dump-postgresql  Starts a PostgreSQL server without a network listener on
#                    the snapshot's data directory and dumps it.
#   archive-gitea    Archives the Gitea volume from the snapshot.
#   upload           Uploads both objects and a manifest written last.
#
# A set without a manifest is incomplete and restore ignores it. See decisions
# 0007 and 0014.
set -euo pipefail

: "${BACKUP_CLUSTER:?}" "${PING_URL:?}"

STATE=/state
STAGING=/staging
SNAPSHOT_ROOT=/snapshot
RECIPIENTS=/backup/recipients.txt
METADATA=http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token
# Host namespaces of the worker, from its /proc mounted at /host/proc.
HOST_SNAPSHOT=(nsenter --mount=/host/proc/1/ns/mnt --ipc=/host/proc/1/ns/ipc --
  /usr/local/sbin/k8sdr-snapshot)

log() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$step" "$*"; }

# The heartbeat must never fail the backup, and a failed ping is only logged.
heartbeat() {
  curl -fsS -m 10 --retry 3 -o /dev/null "$@" || log "heartbeat ping failed"
}

prefix() { printf '%s/%s' "$BACKUP_CLUSTER" "$(cat "${STATE}/created")"; }

# Each step sets cleanup to undo what it started. The status is read before
# the cleanup runs, so a failed step is reported even if its cleanup succeeds.
cleanup=:
on_exit() {
  local status=$?
  "$cleanup" || log "cleanup failed"
  if ((status != 0)); then
    log "failed with status ${status}"
    heartbeat --data-raw "backup $(prefix 2>/dev/null || echo "${BACKUP_CLUSTER}/unknown") failed in ${step}" \
      "${PING_URL}/fail"
  fi
  exit "$status"
}

# The capture steps start only after the sidecar wrote ok or failed.
require_snapshot() {
  if [[ "$(cat "${STATE}/snapshot")" != ok ]]; then
    log "no snapshot; see the snapshot container log"
    exit 1
  fi
}

snapshot() {
  : "${SNAPSHOT_CLAIMS:?}"
  local claims
  read -r -a claims <<<"$SNAPSHOT_CLAIMS"

  # Removal runs on every stop that is not SIGKILL. The next create removes a
  # snapshot left by SIGKILL.
  # A failed removal exits non-zero, so on_exit reports it.
  release() {
    "${HOST_SNAPSHOT[@]}" remove
    log "snapshot removed"
    exit 0
  }
  trap release TERM INT

  date -u +%Y%m%dT%H%M%SZ >"${STATE}/created"
  heartbeat "${PING_URL}/start"
  log "backup $(prefix) started"
  if "${HOST_SNAPSHOT[@]}" create "${claims[@]}"; then
    echo ok >"${STATE}/snapshot"
    log "snapshot created for ${claims[*]}"
  else
    # Stay up so that the capture steps see the failure and fail the Pod.
    echo failed >"${STATE}/snapshot"
    log "snapshot failed"
    heartbeat --data-raw "backup $(prefix) failed in snapshot" "${PING_URL}/fail"
  fi
  # The script runs as PID 1, which ignores SIGTERM without a handler. A
  # background sleep lets the trap run as soon as the signal arrives.
  sleep infinity &
  wait "$!"
}

dump_postgresql() {
  : "${SNAPSHOT_PGDATA:?}" "${PGUSER:?}" "${PGDATABASE:?}"
  require_snapshot
  started=false
  stop_postgresql() {
    if [[ "$started" == true ]]; then
      pg_ctl stop --pgdata="$SNAPSHOT_PGDATA" --mode=fast --wait --silent
      started=false
    fi
  }
  cleanup=stop_postgresql

  # The lock file names a process of the live server, which this container
  # cannot see, so a stale PID could match one of its own processes.
  rm -f "${SNAPSHOT_PGDATA}/postmaster.pid"
  printf 'local all all trust\n' >/tmp/pg_hba.conf
  started=true
  # Crash recovery replays the WAL on the snapshot, then the server accepts
  # connections on a socket in /tmp only.
  pg_ctl start --pgdata="$SNAPSHOT_PGDATA" --wait --timeout=600 --silent \
    --log=/tmp/postgresql.log \
    --options="-c listen_addresses='' -c unix_socket_directories=/tmp \
-c hba_file=/tmp/pg_hba.conf -c archive_mode=off" ||
    { cat /tmp/postgresql.log; exit 1; }
  log "temporary server started on the snapshot"

  pg_dump --host=/tmp --format=custom |
    age --encrypt -R "$RECIPIENTS" -o "${STAGING}/postgresql.dump.age"
  pg_dump --version >"${STAGING}/pg_dump.version"
  stop_postgresql
  log "dumped PostgreSQL"
}

archive_gitea() {
  require_snapshot
  tar -C "${SNAPSHOT_ROOT}/gitea" -cf - . | gzip |
    age --encrypt -R "$RECIPIENTS" -o "${STAGING}/gitea-data.tar.gz.age"
  log "archived the Gitea volume"
}

# Uploads with the XML API. Content-MD5 makes Cloud Storage reject a corrupt
# upload, and generation-match 0 refuses to replace an existing object.
put() {
  local file=$1 name=$2 type=$3 md5
  md5=$(md5sum "$file" | cut -d' ' -f1 | tr a-f A-F | basenc --base16 -d | base64)
  curl -fsS --retry 3 -o /dev/null -X PUT -T "$file" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-MD5: ${md5}" \
    -H "Content-Type: ${type}" \
    -H "x-goog-if-generation-match: 0" \
    "https://storage.googleapis.com/${BACKUP_BUCKET}/$(prefix)/${name}"
}

upload() {
  : "${BACKUP_BUCKET:?}"
  local gitea_version files=() name
  gitea_version=$(kubectl -n gitea get deployment gitea \
    -o jsonpath='{.metadata.labels.app\.kubernetes\.io/version}')

  token=$(curl -fsS -H "Metadata-Flavor: Google" "$METADATA" | jq -r .access_token)
  for name in postgresql.dump.age gitea-data.tar.gz.age; do
    put "${STAGING}/${name}" "$name" application/octet-stream
    files+=("$(jq -n --arg name "$name" \
      --arg sha256 "$(sha256sum "${STAGING}/${name}" | cut -d' ' -f1)" \
      --argjson size "$(stat -c %s "${STAGING}/${name}")" \
      '{name: $name, sha256: $sha256, size: $size}')")
  done

  # Gitea is not paused, so pause_seconds is 0. Restore reads only format,
  # cluster, created, and files.
  printf '%s\n' "${files[@]}" | jq -s \
    --arg cluster "$BACKUP_CLUSTER" \
    --arg created "$(cat "${STATE}/created")" \
    --arg gitea "$gitea_version" \
    --arg postgresql "$(cat "${STAGING}/pg_dump.version")" \
    '{format: 1, cluster: $cluster, created: $created, pause_seconds: 0,
      capture: "lvm-snapshot",
      versions: {gitea: $gitea, pg_dump: $postgresql}, files: .}' >"${STAGING}/manifest.json"
  put "${STAGING}/manifest.json" manifest.json application/json
  log "uploaded $(prefix)"
  heartbeat --data-raw "backup $(prefix) complete, Gitea not paused" "$PING_URL"
}

step=${1:-}
trap on_exit EXIT
case "$step" in
  snapshot) snapshot ;;
  dump-postgresql) dump_postgresql ;;
  archive-gitea) archive_gitea ;;
  upload) upload ;;
  *)
    echo "usage: backup.sh snapshot|dump-postgresql|archive-gitea|upload" >&2
    exit 2
    ;;
esac
