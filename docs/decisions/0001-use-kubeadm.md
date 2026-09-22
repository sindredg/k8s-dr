# 0001: Use kubeadm for cluster bootstrap

Status: Accepted as the design choice in the [project overview](../../README.md) and [plan](../../plan.md). Implementation and validation are pending.

## Context

This lab measures recovery of a self-managed Kubernetes service after losing a region. The cluster must be rebuilt on fresh VMs using the same documented steps in the primary and recovery regions.

## Decision

Use kubeadm to initialize the control plane and join workers. Use Ansible to configure hosts and run the repeatable bootstrap steps. Keep the cluster configuration in the external source repository so recovery does not depend on the primary cluster or Gitea.

## Alternatives and trade-offs

| Option | Trade-off |
| --- | --- |
| [kubeadm on VMs](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/) | Exposes the control-plane and node recovery steps the lab needs to measure, but requires maintaining the OS, runtime, networking, upgrades, and certificates. |
| [Managed Kubernetes (GKE)](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview) | Reduces cluster operations, but delegates much of the bootstrap and regional rebuild behavior this lab is intended to test. |
| [K3s](https://docs.k3s.io/quick-start) | Could shorten setup, but changes the bootstrap and maintenance model under test. |

## Consequences

The recovery runbook must account for kubeadm initialization, worker join, CNI setup, and verification of node readiness. Milestone 2 will test whether these steps are repeatable; this decision does not claim that they work yet.
