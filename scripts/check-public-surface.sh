#!/usr/bin/env bash
# Checks the service from outside: TLS, the HTTP redirect, response headers,
# anonymous access, ports that must stay closed, and the DNS records that
# govern certificate issuance. Needs no credential. See decision 0010.
# Usage: scripts/check-public-surface.sh [--strict] [host]
set -euo pipefail

HOST=${HOST:-git.sindrg.com}
# The DNS zone that holds the host: DS is published for the zone, and a CAA
# lookup climbs from the host to it.
ZONE=${ZONE:-sindrg.com}
STRICT=false

while [ $# -gt 0 ]; do
  case $1 in
    --strict) STRICT=true ;;
    -*)
      printf 'unknown option: %s\n' "$1" >&2
      exit 2
      ;;
    *) HOST=$1 ;;
  esac
  shift
done

# The landing page and the sign-in form.
PATHS=("/" "/user/login")

# Findings that decision 0010 records as open. Delete a line when its finding
# closes; the script fails on a listed finding that passes.
KNOWN_OPEN=(
  "hsts no Strict-Transport-Security header"
  "caa no CAA record in the zone"
)

# Ports that only the private network may reach: SSH, the Kubernetes API, the
# kubelet, and Traefik's NodePort.
CLOSED_PORTS=(22 6443 10250 30080)

# Zones known to publish these records, so an empty answer can be told from a
# resolver that does not answer the query form at all.
CAA_CONTROL=${CAA_CONTROL:-google.com}
DNSSEC_CONTROL=${DNSSEC_CONTROL:-cloudflare.com}

# The whole CAA answer, not a sample of it. RFC 8659 takes the union of the
# records at a name, so a named issuewild entry beside `;` re-authorises the
# wildcard issuance `;` forbids. A record this list does not name is therefore
# a weakening, whoever added it.
CAA_EXPECTED=(
  '0 issue "letsencrypt.org"'
  '0 issuewild ";"'
)

passed=0
regressions=0
known=0
resolved=0
unknown=0

is_known() {
  local id=$1 entry
  for entry in "${KNOWN_OPEN[@]}"; do
    if [ "${entry%% *}" = "$id" ]; then
      return 0
    fi
  done
  return 1
}

# Three outcomes: a probe that could not run is not evidence of a healthy server.
report() {
  local outcome=$1 id=$2 detail=$3
  case "$outcome" in
    pass)
      if is_known "$id"; then
        printf 'RESOLVED  %-16s %s\n' "$id" "$detail"
        printf '          remove %s from KNOWN_OPEN in %s\n' "$id" "$(basename "$0")"
        resolved=$((resolved + 1))
      else
        printf 'ok        %-16s %s\n' "$id" "$detail"
        passed=$((passed + 1))
      fi
      ;;
    fail)
      if is_known "$id"; then
        printf 'known     %-16s %s\n' "$id" "$detail"
        known=$((known + 1))
      else
        printf 'REGRESSED %-16s %s\n' "$id" "$detail"
        regressions=$((regressions + 1))
      fi
      ;;
    inconclusive)
      printf 'unknown   %-16s %s\n' "$id" "$detail"
      unknown=$((unknown + 1))
      ;;
  esac
}

# openssl reads no proxy from the environment; CI reaches the internet directly.
openssl_args=()
if [ -n "${HTTPS_PROXY:-}" ]; then
  openssl_args=(-proxy "${HTTPS_PROXY#http://}")
fi

handshake() {
  local version=$1
  # SECLEVEL=0 or OpenSSL 3 refuses client-side, which reads as a healthy server.
  timeout 30 openssl s_client "-$version" -cipher 'DEFAULT:@SECLEVEL=0' \
    "${openssl_args[@]}" -connect "$HOST:443" -servername "$HOST" \
    </dev/null 2>&1 || true
}

# s_client prints "New, (NONE), Cipher is (NONE)" when it never connected, so a
# negotiated cipher is the only evidence that the server answered at all.
negotiated() {
  # No match is an answer here, and pipefail would otherwise make it an error.
  printf '%s' "$1" | sed -n 's/^New, TLSv[0-9.]*, Cipher is \(..*\)$/\1/p' |
    grep -v '^(NONE)$' | head -1 || true
}

unreached() {
  printf '%s' "$1" | grep -qE 'HTTP CONNECT failed|Connection refused|connect:errno'
}

check_refused() {
  local version=$1 id=$2 out
  out=$(handshake "$version")

  if unreached "$out"; then
    report inconclusive "$id" "the host was never reached, so $version was not offered"
  elif printf '%s' "$out" | grep -q 'no protocols available'; then
    report inconclusive "$id" "this openssl will not offer $version, so the server was never asked"
  elif printf '%s' "$out" | grep -qE 'alert protocol version|wrong version number|unsupported protocol'; then
    report pass "$id" "server refused $version"
  elif [ -n "$(negotiated "$out")" ]; then
    report fail "$id" "server accepted $version"
  else
    report inconclusive "$id" "no verdict from the $version handshake"
  fi
}

check_accepted() {
  local version=$1 id=$2 out cipher
  out=$(handshake "$version")
  cipher=$(negotiated "$out")

  if [ -n "$cipher" ]; then
    report pass "$id" "server accepted $version"
  elif unreached "$out"; then
    report inconclusive "$id" "the host was never reached, so $version was not offered"
  else
    report fail "$id" "server did not complete a $version handshake"
  fi
}

# One request per path serves every header check. GET, because a browser sends GET.
declare -A HEADERS
fetch_headers() {
  local path
  for path in "${PATHS[@]}"; do
    HEADERS["$path"]=$(curl -sS -D - -o /dev/null -m 30 "https://${HOST}${path}" 2>/dev/null |
      tr -d '\r' | tr '[:upper:]' '[:lower:]' || true)
  done
}

# The last status counts: a CONNECT or an error page is not the site's headers.
unreachable() {
  local path status
  for path in "${PATHS[@]}"; do
    status=$(printf '%s' "${HEADERS[$path]}" |
      grep -E '^http/[0-9.]+ [0-9]{3}' | tail -1 | awk '{print $2}')
    case "$status" in
      2??|3??) ;;
      *) return 0 ;;
    esac
  done
  return 1
}

check_header() {
  local id=$1 header=$2 pattern=$3 path missing=()

  if unreachable; then
    report inconclusive "$id" "no response from $HOST, so headers were not read"
    return
  fi

  for path in "${PATHS[@]}"; do
    if ! printf '%s' "${HEADERS[$path]}" | grep -qE "^${header}:.*${pattern}"; then
      missing+=("$path")
    fi
  done

  if [ ${#missing[@]} -eq 0 ]; then
    report pass "$id" "$header present on every path"
  else
    report fail "$id" "$header missing on ${missing[*]}"
  fi
}

check_redirect() {
  local out
  # curl does not follow redirects unless asked, so this is the one under test.
  out=$(curl -sS -D - -o /dev/null -m 30 "http://${HOST}/" 2>/dev/null |
    tr -d '\r' | tr '[:upper:]' '[:lower:]' || true)

  local status
  status=$(printf '%s' "$out" | grep -E '^http/[0-9.]+ [0-9]{3}' | tail -1 | awk '{print $2}')

  case "$status" in
    30[128])
      if printf '%s' "$out" | grep -qE '^location: https://'; then
        report pass "http-redirect" "plain HTTP redirects to HTTPS"
      else
        report fail "http-redirect" "redirected, but not to HTTPS"
      fi
      ;;
    2??)
      report fail "http-redirect" "plain HTTP served content instead of redirecting"
      ;;
    "")
      report inconclusive "http-redirect" "no response on port 80"
      ;;
    *)
      # An error status may not have come from the origin at all.
      report inconclusive "http-redirect" "port 80 answered $status"
      ;;
  esac
}

# cert-manager renews 30 days out, so this only fires once renewal has stalled.
EXPIRY_FLOOR_DAYS=21

check_certificate_expiry() {
  local pem end_date end_epoch days
  pem=$(handshake tls1_2 |
    sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')

  if [ -z "$pem" ]; then
    report inconclusive "cert-expiry" "no certificate was served"
    return
  fi

  end_date=$(printf '%s\n' "$pem" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  end_epoch=$(date -d "$end_date" +%s 2>/dev/null || true)

  if [ -z "$end_epoch" ]; then
    report inconclusive "cert-expiry" "could not read an expiry from the certificate"
    return
  fi

  days=$(((end_epoch - $(date +%s)) / 86400))

  if [ "$days" -ge "$EXPIRY_FLOOR_DAYS" ]; then
    report pass "cert-expiry" "${days} days remaining"
  else
    report fail "cert-expiry" "${days} days remaining, under the ${EXPIRY_FLOOR_DAYS} day floor"
  fi
}

# A CAA value can itself hold a comma or a semicolon, so the records are joined
# for reporting rather than passed through a delimiter-splitting tool.
join_records() {
  awk '{ printf "%s%s", sep, $0; sep = ", " } END { if (NR) print "" }'
}

# Presence is not the control here, so this compares the answer to CAA_EXPECTED.
check_caa() {
  local id=caa out actual expected extra missing
  if ! command -v dig >/dev/null 2>&1; then
    report inconclusive "$id" "dig is not installed, so CAA was not queried"
    return
  fi

  # RFC 8659: the closest name with a CAA record set applies.
  local name=$HOST
  out=$(dig +short CAA "$name" 2>/dev/null || true)
  while [ -z "$out" ] && [ "$name" != "$ZONE" ] && [ "$name" != "${name#*.}" ]; do
    name=${name#*.}
    out=$(dig +short CAA "$name" 2>/dev/null || true)
  done
  if [ -z "$out" ]; then
    if [ -z "$(dig +short CAA "$CAA_CONTROL" 2>/dev/null || true)" ]; then
      report inconclusive "$id" "this resolver returned no CAA for $CAA_CONTROL either, so it does not answer CAA here"
    else
      report fail "$id" "no CAA record"
    fi
    return
  fi

  # One record per line, spacing squeezed and both sides sorted before they are
  # compared. A value can hold a space of its own, as
  # `0 issue "digicert.com; cansignhttpexchanges=yes"` does, so the line is the
  # unit here rather than the field.
  actual=$(printf '%s\n' "$out" | sed 's/[[:space:]]\{1,\}/ /g; s/^ //; s/ $//' \
    | sed '/^$/d' | sort)
  expected=$(printf '%s\n' "${CAA_EXPECTED[@]}" | sort)

  extra=$(comm -23 <(printf '%s\n' "$actual") <(printf '%s\n' "$expected") | join_records)
  missing=$(comm -13 <(printf '%s\n' "$actual") <(printf '%s\n' "$expected") | join_records)

  if [ -n "$extra" ]; then
    report fail "$id" "CAA authorises more than the platform declared: $extra"
  elif [ -n "$missing" ]; then
    report fail "$id" "CAA is missing $missing"
  else
    report pass "$id" "certificate issuance is restricted"
  fi
}

# An empty answer is only evidence when the resolver answers this query form at
# all. Some resolvers, GitHub's runners among them, return nothing for DS
# whatever the zone, which would read as a missing record. The control is a
# zone known to publish one.
check_dns() {
  local id=$1 type=$2 description=$3 control=${4:-} out
  if ! command -v dig >/dev/null 2>&1; then
    report inconclusive "$id" "dig is not installed, so $type was not queried"
    return
  fi

  out=$(dig +short "$type" "$ZONE" 2>/dev/null || true)
  if [ -n "$out" ]; then
    report pass "$id" "$description"
  elif [ -n "$control" ] && [ -z "$(dig +short "$type" "$control" 2>/dev/null || true)" ]; then
    report inconclusive "$id" "this resolver returned no $type for $control either, so it does not answer $type here"
  else
    report fail "$id" "no $type record"
  fi
}

# 200 means an anonymous visitor can list users; 401 or 403 means sign-in is
# required. Anything else may not have come from Gitea.
check_anonymous_api() {
  local status
  status=$(curl -sS -o /dev/null -m 30 -w '%{http_code}' "https://${HOST}/api/v1/users/search" 2>/dev/null || true)
  case "$status" in
    401|403) report pass "anonymous-api" "the API refuses anonymous requests" ;;
    200) report fail "anonymous-api" "the API lists users to anonymous requests" ;;
    *) report inconclusive "anonymous-api" "the API answered ${status:-nothing}" ;;
  esac
}

check_registration() {
  local body status
  body=$(curl -sS -m 30 -w '\n%{http_code}' "https://${HOST}/user/sign_up" 2>/dev/null || true)
  status=${body##*$'\n'}
  case "$status" in
    403|404) report pass "registration" "the sign-up page is not served" ;;
    200)
      if printf '%s' "$body" | grep -q 'Registration is disabled'; then
        report pass "registration" "registration is disabled"
      else
        report fail "registration" "the sign-up page does not say registration is disabled"
      fi
      ;;
    *) report inconclusive "registration" "the sign-up page answered ${status:-nothing}" ;;
  esac
}

connects() {
  timeout 5 bash -c "exec 3<>/dev/tcp/$HOST/$1" 2>/dev/null
}

# A port that does not answer is only evidence when 443 on the same address
# does; otherwise this machine never reached the host.
check_closed_ports() {
  local port open=()
  if ! connects 443; then
    report inconclusive "closed-ports" "port 443 did not answer, so the host was never reached"
    return
  fi
  for port in "${CLOSED_PORTS[@]}"; do
    if connects "$port"; then
      open+=("$port")
    fi
  done
  if [ ${#open[@]} -eq 0 ]; then
    report pass "closed-ports" "no answer on ${CLOSED_PORTS[*]}"
  else
    report fail "closed-ports" "open to the internet: ${open[*]}"
  fi
}

printf 'Checking the public surface of %s\n\n' "$HOST"

check_refused tls1 tls10-refused
check_refused tls1_1 tls11-refused
check_accepted tls1_2 tls12-accepted
check_accepted tls1_3 tls13-accepted

check_redirect

fetch_headers
check_header hsts strict-transport-security 'max-age=[0-9]+'
check_header nosniff x-content-type-options 'nosniff'
check_header frame-options x-frame-options '(sameorigin|deny)'

check_certificate_expiry

check_anonymous_api
check_registration
check_closed_ports

check_caa
check_dns dnssec DS "the zone is signed" "$DNSSEC_CONTROL"

printf '\n%s ok, %s known open, %s regressed, %s resolved, %s inconclusive\n' \
  "$passed" "$known" "$regressions" "$resolved" "$unknown"

status=0
if [ "$regressions" -gt 0 ] || [ "$resolved" -gt 0 ]; then
  status=1
fi
if [ "$STRICT" = true ] && [ "$unknown" -gt 0 ]; then
  status=1
fi
exit "$status"
