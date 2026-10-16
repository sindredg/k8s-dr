# Git rejected the fixture user

Observed on 2026-10-16 on the operator machine, during the milestone 8 gate checks against the primary.

## Symptom

`make check-fixtures` failed at `Require the clone to succeed`:

```text
git clone exited 128: remote: Failed to authenticate user
fatal: Authentication failed for 'https://git.sindrg.com/recovery-fixture/recovery-fixture.git/'
```

The tasks before it passed, including `Sign in as the fixture user` through the API with the same password. The same check passed on 2026-10-14, and `scripts/capture_probe.py` ran authenticated `git ls-remote` without errors until 2026-10-15 00:37 UTC.

## Cause

Confirmed from a `GIT_TRACE_CURL` trace of the clone, decoded to the username and the password length:

1. The first request had no credentials. Gitea answered `401` with `www-authenticate: Basic realm="Gitea"`.
2. The second request sent `recovery-fixture` with an empty password. `scripts/git_with_password.sh` had not been called yet. Gitea answered `401`.
3. Git then asked `GIT_ASKPASS` for `Username for 'https://git.sindrg.com'` and then for the password. The helper printed the password for every prompt, so Git sent the password as the username. Gitea answered `401`.

The helper assumed Git only asks for the password when the URL names the user. Step 3 shows Git asking for both.

Gitea itself accepted the credentials: `curl -u` with the same user and password on the same `info/refs` URL returned `200` and the refs.

Hypothesis, not confirmed: the change came from a libcurl update on the operator machine. The dpkg log shows `libcurl3t64-gnutls`, the library Git uses, upgraded from `8.5.0-2ubuntu10.15` to `8.5.0-2ubuntu10.16` on 2026-10-15 07:53 local time, after the last passing run. Git stayed at `1:2.43.0-1ubuntu7.3`. The previous library version was not reinstalled to confirm this.

## Fix

- `scripts/git_with_password.sh` answers the username prompt with `GIT_STDIN_USERNAME` and the password prompt with the password from stdin. It exits with an error if `GIT_STDIN_USERNAME` is not set.
- The fixture playbooks set `GIT_STDIN_USERNAME` to the fixture user. `scripts/capture_probe.py` sets it to `GIT_PROBE_USER`.
- The clone URLs no longer name the user, so Git asks for both values through the helper and skips the request with the empty password.

`tests/test_git_with_password.py` stands in for Git and asks for the username and then the password. It failed on the previous helper with `username=secret`.

## Verify

On 2026-10-16, from the operator machine against the primary:

- A clone through the helper with `GIT_STDIN_USERNAME` set exited `0`, both with and without the user in the URL.
- `make check-fixtures` ended with `failed=0`.
- `make write-check` ended with `failed=0`: `Push accepted at 2026-10-16T04:44:42Z`.
