# 0001: Use kubeadm for cluster bootstrap

Status: Accepted as the design choice in the [project overview](../../README.md) and [plan](../../plan.md). Implemented and validated in [milestone 2](../worklogs/02-kubernetes-bootstrap.md) on 2026-09-25.

## Context

This lab measures recovery of a self-managed Kubernetes service after losing a region. The cluster must be rebuilt on fresh VMs using the same documented steps in the primary and recovery regions. The separate [k8-lab](https://github.com/sindredg/k8-lab) already covers managed GKE, so this project focuses on operating the Kubernetes control plane.

Self-managed Kubernetes on VMs also models organizations that must control the operating system layer, for example to meet regulatory requirements for host hardening, patching, audit, or data handling that a managed service does not expose. In that setting, managed Kubernetes is not an option, and regional recovery must work with self-operated nodes. See [production readiness](../production-readiness.md) for what this lab leaves out.

## Decision

Use kubeadm to initialize the control plane and join workers. Use Ansible to configure hosts and run the repeatable bootstrap steps. Keep the cluster configuration in the external source repository so recovery does not depend on the primary cluster or Gitea.

## Alternatives and trade-offs

| Option | Trade-off |
| --- | --- |
| [kubeadm on VMs](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/) | Exposes the control-plane and node recovery steps the lab needs to measure, but requires maintaining the OS, runtime, networking, upgrades, and certificates. |
| [Managed Kubernetes (GKE)](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview) | Reduces cluster operations, but delegates much of the bootstrap and regional rebuild behavior this lab is intended to test. |
| [K3s](https://docs.k3s.io/quick-start) | A valid, portable Kubernetes distribution that simplifies installation. It would shorten the build, but expose fewer upstream bootstrap choices for this learning goal. |

## Consequences

The recovery runbook must account for kubeadm initialization, worker join, CNI setup, certificate and version lifecycle, and verification of node readiness. One control plane and one worker do not provide local high availability; the project tests regional cold recovery. Milestone 2 showed the build is repeatable on replacement VMs. kubeadm is a learning choice here, not a requirement for DR.
