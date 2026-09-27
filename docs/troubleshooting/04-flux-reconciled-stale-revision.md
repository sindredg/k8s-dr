# Flux reconciled the previous branch

Observed on 2026-09-27 when the primary cluster switched from `main` to the `feat/gitea` branch.

## Symptom

`make bootstrap FLUX_GIT_BRANCH=feat/gitea` at `59da3be` failed at `Wait for Flux to apply the cluster configuration` after the full 900-second wait:

```text
error: timed out waiting for the condition on kustomizations/flux-system
```

Recap: control plane `ok=69 changed=14 unreachable=0 failed=1 skipped=8`; worker `ok=55 changed=0 unreachable=0 failed=0 skipped=9`. `run_with_iap: started 2026-09-26T23:27:05Z, finished 2026-09-26T23:44:47Z, elapsed 1007s, exit 2`.

`make validate-cluster` and `make validate-services`, run straight after, both passed. The PostgreSQL and Gitea pods were less than two minutes old.

## Cause

Confirmed from the `kustomize-controller` and `source-controller` logs (`kubectl logs -n flux-system deploy/<controller> --since=90m`).

1. The Flux role annotated the `GitRepository` and the `flux-system` Kustomization with one reconcile request. The next task, `kubectl wait --for=condition=Ready gitrepository/flux-system`, passed at once because the `GitRepository` was already `Ready` with the `main` artifact.
2. At 23:29:46, `flux-system` reconciled `main@sha1:331bb4f`. The source controller stored the `feat/gitea` artifact at 23:29:45 and 23:29:46, after that reconcile had read its source.
3. At 23:30:15, the child Kustomization `certificates` reconciled with the new artifact but still had the spec from `main`. It failed with `kustomization path not found: .../deploy/clusters/primary/certificates`, because `feat/gitea` moved that directory. A failed Kustomization retries at its 10-minute interval.
4. `flux-system` has `wait: true`, so its health check waited for `certificates`. At 23:44:46 it failed with `timeout waiting for: [Kustomization/flux-system/certificates status: 'InProgress']`.
5. The next `flux-system` reconcile used `feat/gitea`, created `cluster-settings`, `postgresql`, and `gitea`, and finished in 1m45s.

The race only hurts when the new revision changes a child Kustomization in a way the old spec cannot build, such as a moved path. A fresh cluster has no previous artifact, so a recovery bootstrap is not affected.

## Fix

The Flux role now:

1. Requests a fetch of the `GitRepository` only.
2. Waits until `.status.artifact.revision` starts with `<branch>@`.
3. Requests a reconcile of `flux-system`.
4. Waits until `.status.lastAppliedRevision` equals the fetched revision and `Ready` is `True`, for up to 900 seconds. A `Ready` condition left from the previous revision no longer ends the wait.

A unit test checks the task order and both wait conditions.

## Verify

`make bootstrap FLUX_GIT_BRANCH=feat/gitea` at `796f863` passed with `failed=0` on both hosts, including `Wait for Flux to fetch the requested branch` and the revision-and-Ready wait. Flux reported every Kustomization at `feat/gitea@sha1:796f863`. See the [milestone 3 worklog](../worklogs/03-service-deployment.md#validation-record-1).

That run did not move a child path, so it shows that the new tasks work, not that they prevent the race. The race needs a branch switch that changes a child Kustomization's path.
