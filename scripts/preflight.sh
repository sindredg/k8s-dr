#!/usr/bin/env bash
# Checks from the operator machine that everything a recovery depends on is
# usable, before the primary is isolated: tools, keys, Terraform state, the
# newest backup set, the fixtures, and the Cloudflare token. It changes
# nothing and prints no credential or state content. See
# docs/runbooks/regional-recovery.md.
# Usage: scripts/preflight.sh
set -uo pipefail

cd "$(dirname "$0")/.."

FLUX_AGE_KEY_FILE=${FLUX_AGE_KEY_FILE:-$HOME/.config/k8s-dr/age.agekey}
BACKUP_AGE_KEY_FILE=${BACKUP_AGE_KEY_FILE:-$HOME/.config/k8s-dr/backup.agekey}
# The cluster prefix to restore from.
BACKUP_SOURCE=${BACKUP_SOURCE:-primary}
# A newest set older than this cannot meet the RPO in decision 0002.
MAX_BACKUP_AGE_SECONDS=${MAX_BACKUP_AGE_SECONDS:-7200}
# The drill must end before the token that issues the recovery certificates
# expires.
MIN_TOKEN_DAYS=${MIN_TOKEN_DAYS:-7}
GIT_URL=$(sed -n 's/^flux_git_url: "\(.*\)"$/\1/p' ansible/playbooks/group_vars/all.yml)
GIT_BRANCH=$(sed -n 's/^flux_git_branch: "\(.*\)"$/\1/p' ansible/playbooks/group_vars/all.yml)
ZONE=${ZONE:-sindrg.com}
TOOLS=(git terraform gcloud sops age age-keygen jq curl sha256sum)

passed=0
failed=0
missing=0

# The downloaded set is encrypted; it is still removed on every exit.
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# Three outcomes: a prerequisite that is absent is not a check that failed.
report() {
  local outcome=$1 id=$2 detail=$3
  case "$outcome" in
    pass)
      printf 'ok       %-16s %s\n' "$id" "$detail"
      passed=$((passed + 1))
      ;;
    fail)
      printf 'FAIL     %-16s %s\n' "$id" "$detail"
      failed=$((failed + 1))
      ;;
    missing)
      printf 'MISSING  %-16s %s\n' "$id" "$detail"
      missing=$((missing + 1))
      ;;
    manual)
      printf 'manual   %-16s %s\n' "$id" "$detail"
      ;;
  esac
}

have() {
  command -v "$1" >/dev/null 2>&1
}

check_tools() {
  local tool absent=()
  for tool in "${TOOLS[@]}"; do
    have "$tool" || absent+=("$tool")
  done
  if [ ${#absent[@]} -eq 0 ]; then
    report pass tools "${TOOLS[*]}"
  else
    report missing tools "not installed: ${absent[*]}"
  fi
}

check_git_source() {
  local sha
  if sha=$(git ls-remote "$GIT_URL" "refs/heads/$GIT_BRANCH" 2>/dev/null | cut -f1) && [ -n "$sha" ]; then
    report pass git-source "$GIT_BRANCH is ${sha:0:12} at $GIT_URL"
  else
    report fail git-source "cannot read $GIT_BRANCH from $GIT_URL"
  fi
}

# The public key of a private key file must be a recipient the repository
# encrypts for, or the key decrypts nothing.
check_key() {
  local id=$1 file=$2 recipients=$3 public
  if [ ! -r "$file" ]; then
    report missing "$id" "no readable key at $file; restore it from the credential store"
  elif ! public=$(age-keygen -y "$file" 2>/dev/null); then
    report fail "$id" "$file is not an age private key"
  elif grep -q "$public" "$recipients"; then
    report pass "$id" "matches a recipient in $recipients"
  else
    report fail "$id" "its public key is not a recipient in $recipients"
  fi
}

check_fixture_secret() {
  if [ ! -r "$FLUX_AGE_KEY_FILE" ]; then
    report missing fixture-secret "needs the SOPS age key"
  elif SOPS_AGE_KEY_FILE=$FLUX_AGE_KEY_FILE sops --decrypt recovery/fixtures.sops.yaml >/dev/null 2>&1; then
    report pass fixture-secret "recovery/fixtures.sops.yaml decrypts"
  else
    report fail fixture-secret "recovery/fixtures.sops.yaml does not decrypt with the SOPS age key"
  fi
}

# A plan reads the remote state and the provider without changing either.
# Exit status 2 means the plan has changes, which is the normal state of a
# recovery root that is not applied.
check_state() {
  local root=$1 id=state-$1 file status
  for file in terraform.tfvars backend.hcl; do
    if [ ! -r "infra/$root/$file" ]; then
      report missing "$id" "no infra/$root/$file; create it from the example"
      return
    fi
  done
  if [ ! -d "infra/$root/.terraform" ]; then
    report missing "$id" "run terraform -chdir=infra/$root init -backend-config=backend.hcl"
    return
  fi
  terraform -chdir="infra/$root" plan -lock=false -input=false -detailed-exitcode >/dev/null 2>&1
  status=$?
  case $status in
    0) report pass "$id" "state is reachable; the plan has no changes" ;;
    2) report pass "$id" "state is reachable; the plan has changes" ;;
    *) report fail "$id" "terraform plan failed; run it in infra/$root to see why" ;;
  esac
}

# Repeats the restore's own checks on the newest complete set: every digest
# in the manifest, and that both archives decrypt and read.
# jq reads the manifest on stdin: a jq installed as a snap cannot open files
# under /tmp.
check_backup() {
  local bucket newest set age_seconds started
  if ! bucket=$(terraform -chdir=infra/shared output -raw backup_bucket_name 2>/dev/null) || [ -z "$bucket" ]; then
    report missing backup-set "cannot read backup_bucket_name from infra/shared"
    return
  fi
  if [ ! -r "$BACKUP_AGE_KEY_FILE" ]; then
    report missing backup-set "needs the backup age key"
    return
  fi
  newest=$(gcloud storage ls "gs://${bucket}/${BACKUP_SOURCE}/*/manifest.json" 2>/dev/null | sort | tail -n 1)
  if [ -z "$newest" ]; then
    report fail backup-set "no complete set under ${BACKUP_SOURCE}/, or the bucket is unreachable"
    return
  fi
  set=$(basename "$(dirname "$newest")")
  if ! gcloud storage cp "gs://${bucket}/${BACKUP_SOURCE}/${set}/*" "$WORKDIR/" >/dev/null 2>&1; then
    report fail backup-set "cannot download ${BACKUP_SOURCE}/${set}"
    return
  fi
  if ! (cd "$WORKDIR" && jq -r '.files[] | "\(.sha256)  \(.name)"' <manifest.json | sha256sum -c --quiet) >/dev/null 2>&1; then
    report fail backup-set "${set}: a digest does not match the manifest"
    return
  fi
  if ! age -d -i "$BACKUP_AGE_KEY_FILE" "$WORKDIR/gitea-data.tar.gz.age" 2>/dev/null | tar -tzf - >/dev/null 2>&1; then
    report fail backup-set "${set}: the Gitea archive does not decrypt and read"
    return
  fi
  if [ "$(age -d -i "$BACKUP_AGE_KEY_FILE" "$WORKDIR/postgresql.dump.age" 2>/dev/null | head -c 5)" != "PGDMP" ]; then
    report fail backup-set "${set}: the PostgreSQL dump does not decrypt"
    return
  fi
  started=$(date -u -d "${set:0:4}-${set:4:2}-${set:6:2} ${set:9:2}:${set:11:2}:${set:13:2}" +%s)
  age_seconds=$(($(date -u +%s) - started))
  if [ "$age_seconds" -le "$MAX_BACKUP_AGE_SECONDS" ]; then
    report pass backup-set "${set} verifies and decrypts; age $((age_seconds / 60)) minutes"
  else
    report fail backup-set "${set} verifies, but its age of $((age_seconds / 60)) minutes exceeds the RPO"
  fi
}

check_fixtures() {
  if make --no-print-directory check-fixtures >/dev/null 2>&1; then
    report pass fixtures "the user, repository, commit, and issue are present on the serving cluster"
  else
    report fail fixtures "make check-fixtures failed; run it to see why"
  fi
}

# The token reaches curl in a header file on stdin, never in an argument.
cloudflare() {
  local token=$1 path=$2
  printf 'Authorization: Bearer %s\n' "$token" |
    curl -sS -m 30 -H @- "https://api.cloudflare.com/client/v4${path}" 2>/dev/null
}

# The recovery cluster issues its certificates with this token, and
# scripts/dns_record.sh changes the drill's records with it. The dashboard is
# the fallback, which this script cannot check.
check_cloudflare() {
  local token verify status expires days zone record
  if [ ! -r "$FLUX_AGE_KEY_FILE" ]; then
    report missing cloudflare-token "needs the SOPS age key"
    return
  fi
  if ! token=$(SOPS_AGE_KEY_FILE=$FLUX_AGE_KEY_FILE sops --decrypt \
    --extract '["stringData"]["api-token"]' deploy/certificates/cloudflare-api-token.sops.yaml 2>/dev/null); then
    report fail cloudflare-token "the token does not decrypt with the SOPS age key"
    return
  fi
  verify=$(cloudflare "$token" /user/tokens/verify)
  status=$(printf '%s' "$verify" | jq -r '.result.status // "unreadable"' 2>/dev/null)
  if [ "$status" != "active" ]; then
    report fail cloudflare-token "Cloudflare reports the token as ${status:-unreadable}"
    return
  fi
  expires=$(printf '%s' "$verify" | jq -r '.result.expires_on // empty')
  if [ -n "$expires" ]; then
    days=$((($(date -u -d "$expires" +%s) - $(date -u +%s)) / 86400))
    if [ "$days" -lt "$MIN_TOKEN_DAYS" ]; then
      report fail cloudflare-token "active, but it expires in $days days; rotate it first"
      return
    fi
    report pass cloudflare-token "active; expires in $days days"
  else
    report pass cloudflare-token "active; no expiry"
  fi

  zone=$(cloudflare "$token" "/zones?name=${ZONE}" | jq -r '.result[0].id // empty' 2>/dev/null)
  if [ -z "$zone" ]; then
    report fail cloudflare-zone "the token cannot read the ${ZONE} zone"
    return
  fi
  record=$(cloudflare "$token" "/zones/${zone}/dns_records?type=A&name=git.${ZONE}" |
    jq -r '.result[0] | "\(.ttl) \(.proxied)"' 2>/dev/null)
  if [ "$record" = "60 false" ]; then
    report pass cloudflare-zone "git.${ZONE} is DNS only with a 60-second TTL"
  else
    report fail cloudflare-zone "git.${ZONE} is not DNS only with a 60-second TTL (ttl and proxied: ${record:-unreadable})"
  fi
  report manual cloudflare-login "confirm that you can sign in to the Cloudflare dashboard, the fallback for make dns-set"
}

printf 'Recovery preflight at %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

check_tools
if [ "$missing" -eq 0 ]; then
  check_git_source
  check_key key-sops "$FLUX_AGE_KEY_FILE" .sops.yaml
  check_key key-backup "$BACKUP_AGE_KEY_FILE" deploy/apps/gitea/backup/recipients.txt
  check_fixture_secret
  check_state shared
  check_state recovery
  check_backup
  check_fixtures
  check_cloudflare
fi

printf '\n%d passed, %d failed, %d missing\n' "$passed" "$failed" "$missing"
if [ "$failed" -gt 0 ] || [ "$missing" -gt 0 ]; then
  printf 'Do not isolate the primary until every line is ok.\n'
  exit 1
fi
