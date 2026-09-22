# Worker join failure

This is a diagnostic guide for milestone 2, not a record of an observed incident. Record actual symptoms, cause, fix, and verification in the [bootstrap worklog](../worklogs/02-kubernetes-bootstrap.md).

## Symptom

The worker fails `kubeadm join`, or the join command exits successfully but `kubectl get nodes` does not show the worker as `Ready`.

## Diagnose

1. Record the exact error and whether the worker appears in `kubectl get nodes -o wide`. Redact tokens, certificate hashes, IP addresses if sensitive, and other credentials before sharing output.
2. On the worker, check `systemctl status containerd kubelet` and `journalctl -u kubelet --since "15 minutes ago"`. Confirm the container runtime is running and kubelet errors match the observed failure.
3. Check that the worker can reach the control-plane API endpoint on TCP 6443 and that the endpoint resolves to the intended address. Inspect network rules and routing if it cannot.
4. On the control plane, check whether the join token is valid with `kubeadm token list`. Create a new token and regenerate the join command through the authorized bootstrap procedure if the token expired. Treat the command as a credential.
5. If the node joins but is `NotReady`, inspect `kubectl describe node <worker-name>` and the CNI pods with `kubectl get pods -A`. Check the configured pod network and CNI installation before changing node settings.

## Cause and fix

No cause is confirmed for this project. Apply a fix only after the checks identify the failing layer. Possible fixes include correcting API reachability, restoring containerd or kubelet, renewing a join token, or repairing the CNI. Record the evidence that connects the cause to the fix in the worklog.

Do not run `kubeadm reset` as a routine diagnostic step. It changes node state and may remove evidence needed to understand the failure.

## Verify

Run `kubectl get nodes -o wide` and confirm the worker reports `Ready`. Schedule a disposable workload on the worker and confirm it runs. Restart the worker and repeat both checks before closing the milestone 2 gate.
