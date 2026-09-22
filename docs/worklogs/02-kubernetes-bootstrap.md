# Milestone 2: Kubernetes bootstrap

Status: Pending. No cluster bootstrap or validation is recorded yet.

## Scope

Configure Linux, containerd, kubelet, kubeadm, and kubectl with Ansible. Initialize the control plane, join the worker, install a CNI, and add only the ingress and storage components required for the service. Automate a rebuild from fresh VMs. See [milestone 2](../../plan.md#2-kubernetes-bootstrap).

## Work completed

None recorded.

## Validation gate

Do not mark this milestone complete until evidence shows that:

1. Both nodes report `Ready` after bootstrap.
2. A disposable app schedules and can be reached through the intended path.
3. The worker rejoins after a restart.
4. A fresh rebuild follows the same automated steps.

## Validation record

| Date | Check and command | Result and sanitized evidence |
| --- | --- | --- |
| Pending | `kubectl get nodes -o wide` | Not run |
| Pending | Deploy and reach a disposable app | Not run |
| Pending | Restart worker, then check node status | Not run |
| Pending | Rebuild from fresh VMs | Not run |

## Failures and remaining work

All milestone 2 plan steps remain open. For join failures, use the [worker join guide](../troubleshooting/001-worker-join-failure.md) and record the observed symptom, confirmed cause, fix, and verification here. Do not paste kubeconfigs, join tokens, or unsanitized command output.
