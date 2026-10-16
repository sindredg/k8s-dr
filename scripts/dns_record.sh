#!/usr/bin/env bash
# Shows, sets, or deletes one Cloudflare A record of the service, for the
# recovery drill: the git-dr record of a recovery cluster and the cutover of
# git. Records are always DNS only with a 60-second TTL. It uses the token
# that cert-manager uses, decrypted from the repository, and prints no
# credential. See decision 0002 and docs/runbooks/regional-recovery.md.
# The caa action creates the CAA records of the zone apex that decision 0010
# requires. It never deletes a record, and exits 1 if the zone holds a CAA
# record outside that set.
# Usage: scripts/dns_record.sh show <name>
#        scripts/dns_record.sh set <name> <IPv4 address>
#        scripts/dns_record.sh delete <name>
#        scripts/dns_record.sh caa
set -euo pipefail

cd "$(dirname "$0")/.."

FLUX_AGE_KEY_FILE=${FLUX_AGE_KEY_FILE:-$HOME/.config/k8s-dr/age.agekey}
ZONE=${ZONE:-sindrg.com}
# git-primary never moves, so it is not listed.
NAMES=(git git-dr)
TTL=60
# Tag and value of each apex CAA record, all with flags 0. Keep in step with
# CAA_EXPECTED in scripts/check-public-surface.sh; a test compares them.
CAA_RECORDS=(
  'issue letsencrypt.org'
  'issuewild ;'
)

usage() {
  sed -n 's/^# \(Usage: .*\|       scripts.*\)$/\1/p' "$0" >&2
  exit 2
}

[ $# -ge 1 ] || usage
action=$1
name=${2:-}
address=${3:-}

if [ "$action" = caa ]; then
  [ $# -eq 1 ] || usage
else
  [ $# -ge 2 ] || usage
  case " ${NAMES[*]} " in
    *" $name "*) ;;
    *) echo "error: name must be one of: ${NAMES[*]}" >&2; exit 2 ;;
  esac
fi
case "$action" in
  caa) ;;
  show | delete) [ $# -eq 2 ] || usage ;;
  set)
    [[ "$address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "error: set needs an IPv4 address" >&2; exit 2; }
    ;;
  *) usage ;;
esac
# The delete of git would take the public name down.
if [ "$action" = delete ] && [ "$name" = git ]; then
  echo "error: git is never deleted; set it to another address" >&2
  exit 2
fi

token=$(SOPS_AGE_KEY_FILE=$FLUX_AGE_KEY_FILE sops --decrypt \
  --extract '["stringData"]["api-token"]' deploy/certificates/cloudflare-api-token.sops.yaml)

# The token reaches curl in a header file on stdin, never in an argument.
cloudflare() {
  local method=$1 path=$2 body=${3:-}
  printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$token" |
    curl -sS -m 30 -H @- -X "$method" ${body:+--data "$body"} "https://api.cloudflare.com/client/v4${path}"
}

# Prints the result of a successful call, or the API errors and exits.
result() {
  local response
  response=$(cat)
  if [ "$(printf '%s' "$response" | jq -r '.success')" != "true" ]; then
    printf 'error: Cloudflare: %s\n' "$(printf '%s' "$response" | jq -c '.errors')" >&2
    exit 1
  fi
  printf '%s' "$response" | jq -c '.result'
}

zone=$(cloudflare GET "/zones?name=${ZONE}" | result | jq -r '.[0].id // empty')
[ -n "$zone" ] || { echo "error: the token cannot read the ${ZONE} zone" >&2; exit 1; }

now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

if [ "$action" = caa ]; then
  existing=$(cloudflare GET "/zones/${zone}/dns_records?type=CAA&name=${ZONE}" | result |
    jq -r '.[] | "\(.data.flags) \(.data.tag) \(.data.value)"')
  expected=""
  for entry in "${CAA_RECORDS[@]}"; do
    tag=${entry%% *}
    value=${entry#* }
    expected+="0 ${tag} ${value}"$'\n'
    if grep -qxF "0 ${tag} ${value}" <<<"$existing"; then
      echo "present: ${ZONE} CAA 0 ${tag} \"${value}\""
    else
      body=$(jq -cn --arg name "$ZONE" --arg tag "$tag" --arg value "$value" \
        '{type: "CAA", name: $name, ttl: 1, data: {flags: 0, tag: $tag, value: $value}}')
      cloudflare POST "/zones/${zone}/dns_records" "$body" | result >/dev/null
      echo "created: ${ZONE} CAA 0 ${tag} \"${value}\" at $(now)"
    fi
  done
  extra=$(grep -vxF -f <(printf '%s' "$expected") <<<"$existing" || true)
  if [ -n "$extra" ]; then
    printf 'error: CAA records outside the expected set, not deleted:\n%s\n' "$extra" >&2
    exit 1
  fi
  exit 0
fi

fqdn="${name}.${ZONE}"
record=$(cloudflare GET "/zones/${zone}/dns_records?type=A&name=${fqdn}" | result | jq -c '.[0] // empty')
id=$(printf '%s' "$record" | jq -r '.id // empty')

describe() {
  jq -r '"\(.name) A \(.content) ttl \(.ttl) proxied \(.proxied)"'
}

case "$action" in
  show)
    if [ -n "$id" ]; then printf '%s' "$record" | describe; else echo "${fqdn} has no A record"; fi
    ;;
  set)
    body=$(jq -cn --arg name "$fqdn" --arg content "$address" --argjson ttl "$TTL" \
      '{type: "A", name: $name, content: $content, ttl: $ttl, proxied: false}')
    if [ -n "$id" ]; then
      printf 'was: %s\n' "$(printf '%s' "$record" | describe)"
      cloudflare PUT "/zones/${zone}/dns_records/${id}" "$body" | result | describe
    else
      echo "was: ${fqdn} has no A record"
      cloudflare POST "/zones/${zone}/dns_records" "$body" | result | describe
    fi
    echo "set at $(now)"
    ;;
  delete)
    if [ -z "$id" ]; then
      echo "${fqdn} has no A record"
    else
      printf 'was: %s\n' "$(printf '%s' "$record" | describe)"
      cloudflare DELETE "/zones/${zone}/dns_records/${id}" | result >/dev/null
      echo "deleted at $(now)"
    fi
    ;;
esac
