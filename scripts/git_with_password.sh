#!/bin/sh
# Run git with an HTTPS password read from stdin. The password stays out of
# command arguments, Ansible output, and disk. Git runs this script again as
# GIT_ASKPASS, and that call prints the password.
set -eu
if [ -n "${GIT_STDIN_PASSWORD+set}" ]; then
  printf '%s\n' "$GIT_STDIN_PASSWORD"
  exit 0
fi
IFS= read -r GIT_STDIN_PASSWORD
GIT_ASKPASS="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
# Ignore the operator's Git configuration so no credential helper stores the
# password.
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
GIT_TERMINAL_PROMPT=0
export GIT_STDIN_PASSWORD GIT_ASKPASS GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_TERMINAL_PROMPT
exec git "$@"
