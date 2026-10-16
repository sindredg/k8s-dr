#!/bin/sh
# Run git with an HTTPS username from GIT_STDIN_USERNAME and a password read
# from stdin. The password stays out of command arguments, Ansible output, and
# disk. Git runs this script again as GIT_ASKPASS, and that call prints the
# value the prompt asks for. Git asks for the username too, even when the URL
# names the user: libcurl 8.5.0-2ubuntu10.16 sends that user with an empty
# password first, and Git drops the user after the rejection.
set -eu
if [ -n "${GIT_STDIN_PASSWORD+set}" ]; then
  case "${1:-}" in
    Username*) printf '%s\n' "$GIT_STDIN_USERNAME" ;;
    *) printf '%s\n' "$GIT_STDIN_PASSWORD" ;;
  esac
  exit 0
fi
if [ -z "${GIT_STDIN_USERNAME:-}" ]; then
  echo "git_with_password.sh: set GIT_STDIN_USERNAME to the HTTPS user" >&2
  exit 2
fi
IFS= read -r GIT_STDIN_PASSWORD
GIT_ASKPASS="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
# Ignore the operator's Git configuration so no credential helper stores the
# password.
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
GIT_TERMINAL_PROMPT=0
export GIT_STDIN_USERNAME GIT_STDIN_PASSWORD GIT_ASKPASS GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_TERMINAL_PROMPT
exec git "$@"
