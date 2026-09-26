# Flux rejected the age key

Observed on 2026-09-26 during the first Flux bootstrap on the primary cluster.

## Symptom

`make bootstrap FLUX_GIT_BRANCH=feat/flux-bootstrap` stopped at `Wait for Flux to apply the cluster configuration` with `UNREACHABLE! ... Data could not be sent to remote host "127.0.0.1"`. Recap: control plane `ok=68 changed=13 unreachable=1 failed=0`; `run_with_iap: ... elapsed 264s, exit 4`.

The cluster showed the underlying failure:

```text
$ kubectl -n flux-system get kustomization flux-system
READY   STATUS
False   failed to import 'age.agekey' data from sops decryption Secret 'flux-system/sops-age': failed to parse and add to age identities: malformed secret key: mixed case
```

The `GitRepository` was `Ready` with the expected revision, and all Flux controllers were running.

## Causes

Three separate problems combined.

1. **Corrupted key (confirmed).** The Secret task built the key value as `lookup(...) ~ '\n'` inside a YAML folded scalar. YAML does not process escapes in folded scalars, so Jinja received a backslash and an `n`, and the value ended with the two characters `\n`. The lowercase `n` made age reject the key line as mixed case. Reproduced locally by running the same task with a throwaway key and `tee` in place of `kubectl`: `age-keygen -y` on the rendered value failed with `error at line 3: malformed secret key: mixed case`.
2. **Stale failure after the fix (confirmed).** After the fix, the next bootstrap replaced the Secret (`changed`), but `kubectl wait` still timed out after 300 seconds with the same message. The new Git revision had triggered a reconcile at 18:54:26 UTC, before Ansible replaced the Secret. Flux does not watch the decryption Secret, and reapplying an unchanged `Kustomization` does not trigger a reconcile, so the next attempt was 10 minutes away.
3. **Dropped SSH session (hypothesis).** The first run lost its SSH connection while `kubectl wait` produced no output for several minutes. With SSH keepalives added, the second run held the session for the full 300-second wait. That is consistent with an idle session being dropped inside the IAP tunnel, but one run does not confirm it.

## Fix

- Removed the `~ '\n'` suffix. age does not need a trailing newline. A test runs the real task through `ansible-playbook` and compares the rendered value with the key file; it fails on the old task.
- Added a task that sets `reconcile.fluxcd.io/requestedAt` on the `GitRepository` and `Kustomization` after the Secret and sync objects are applied and before the waits.
- `scripts/run_with_iap.py` sets `ServerAliveInterval=30` and `ServerAliveCountMax=4` for Ansible SSH.

## Verify

`make bootstrap FLUX_GIT_BRANCH=feat/flux-bootstrap` passed with `failed=0` on both hosts. `make validate-cluster` passed, and Flux reported the applied revision of the fix commit. See the [milestone 3 worklog](../worklogs/03-service-deployment.md#validation-record).
