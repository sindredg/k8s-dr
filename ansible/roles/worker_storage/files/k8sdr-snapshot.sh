#!/bin/bash
# Creates and removes the point-in-time LVM snapshot of the worker data volume
# that the backup Job captures from. The worker_storage role installs it as
# /usr/local/sbin/k8sdr-snapshot; the backup Pod runs it in the host mount and
# IPC namespaces. See decision 0014.
#
# create <namespace>/<claim>...  Remove a snapshot left by an earlier run,
#   snapshot the data volume, mount the snapshot, and bind the Local Path
#   Provisioner directory of each claim to /var/lib/k8s-dr-backup/<namespace>.
# remove  Unmount the binds and the snapshot, and remove the snapshot.
set -euo pipefail
shopt -s nullglob

VG=k8sdr
ORIGIN=data
SNAPSHOT=backup
# Volume directories relative to the root of the data filesystem. The role
# mounts it at worker_data_mount, and local_path_root is local-path below it.
LOCAL_PATH=local-path
# The parent is 0700, because a mount hides the mode of its mount point.
SNAPSHOT_DIR=/run/k8sdr-snapshot
SNAPSHOT_MOUNT=${SNAPSHOT_DIR}/data
TARGET_ROOT=/var/lib/k8s-dr-backup

log() { printf '%s k8sdr-snapshot: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
fail() { log "$*"; exit 1; }

# util-linux mountpoint returns 32 for "not a mount point" and 1 for errors,
# including a missing path. /run is empty after a reboot.
is_mounted() {
  local status=0
  [[ -e "$1" ]] || return 1
  mountpoint -q "$1" || status=$?
  case $status in
    0) return 0 ;;
    32) return 1 ;;
    *) fail "cannot tell whether $1 is a mount point (mountpoint exit ${status})" ;;
  esac
}

# An lvs error is not "no snapshot": a lock or metadata error would leave the
# snapshot in place while remove reports success.
snapshot_exists() {
  local names name
  names=$(lvs --noheadings -o lv_name "$VG") || fail "cannot list the logical volumes of ${VG}"
  while read -r name; do
    [[ "$name" == "$SNAPSHOT" ]] && return 0
  done <<<"$names"
  return 1
}

# Explicit checks, because the EXIT trap calls remove from a list, where
# errexit does not apply.
remove() {
  local target
  for target in "$TARGET_ROOT"/*; do
    if is_mounted "$target"; then
      umount "$target" || fail "cannot unmount ${target}"
    fi
  done
  if is_mounted "$SNAPSHOT_MOUNT"; then
    umount "$SNAPSHOT_MOUNT" || fail "cannot unmount ${SNAPSHOT_MOUNT}"
  fi
  if snapshot_exists; then
    lvremove --yes "${VG}/${SNAPSHOT}" || fail "cannot remove ${VG}/${SNAPSHOT}"
    log "removed ${VG}/${SNAPSHOT}"
  fi
}

create() {
  (($# > 0)) || fail "create needs at least one <namespace>/<claim>"
  local claim namespace name matches=()
  local -A seen=()
  for claim in "$@"; do
    # Kubernetes names have no underscore, so the Local Path Provisioner
    # directory name pvc-<uid>_<namespace>_<claim> cannot match two claims.
    [[ "$claim" =~ ^([a-z0-9-]+)/([a-z0-9.-]+)$ ]] || fail "invalid claim '${claim}'"
    namespace=${BASH_REMATCH[1]}
    [[ -z "${seen[$namespace]:-}" ]] || fail "more than one claim in namespace ${namespace}"
    seen[$namespace]=1
  done

  remove
  # Device-mapper freezes the filesystem while it switches the snapshot in.
  lvcreate --snapshot --name "$SNAPSHOT" --extents 100%FREE "${VG}/${ORIGIN}"
  install -d -m 0700 "$SNAPSHOT_DIR"
  install -d -m 0700 "$SNAPSHOT_MOUNT"
  # Read-write, because PostgreSQL replays its WAL on the copy.
  mount -o nodev,nosuid,noexec "/dev/${VG}/${SNAPSHOT}" "$SNAPSHOT_MOUNT"

  install -d -m 0755 "$TARGET_ROOT"
  for claim in "$@"; do
    namespace=${claim%%/*}
    name=${claim#*/}
    matches=("${SNAPSHOT_MOUNT}/${LOCAL_PATH}/pvc-"*"_${namespace}_${name}")
    ((${#matches[@]} == 1)) ||
      fail "expected one volume directory for ${claim}, found ${#matches[@]}"
    install -d -m 0755 "${TARGET_ROOT}/${namespace}"
    mount --bind "${matches[0]}" "${TARGET_ROOT}/${namespace}"
    log "bound ${claim} to ${TARGET_ROOT}/${namespace}"
  done
}

# A failed create leaves nothing mounted. An EXIT trap, not `create || ...`,
# because errexit does not apply inside a function called from a list.
cleanup_failed_create() {
  local status=$?
  if ((status != 0)); then
    remove || log "cleanup after the failed create also failed"
  fi
  exit "$status"
}

case "${1:-}" in
  create)
    shift
    trap cleanup_failed_create EXIT
    create "$@"
    ;;
  remove)
    remove
    ;;
  *)
    fail "usage: k8sdr-snapshot create <namespace>/<claim>... | remove"
    ;;
esac
