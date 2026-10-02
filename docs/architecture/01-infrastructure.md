# 1. Infrastructure

Terraform creates everything in Google Cloud: the buckets, the network, the two VMs, and the load balancer. It creates nothing inside the VMs; that is [Ansible's job](02-ansible.md).

## Roots

A root is a directory with its own state. The split keeps resources that must outlive a region out of any regional root.

```mermaid
flowchart LR
    bootstrap["infra/bootstrap"] --> statebucket[("State bucket")]
    shared["infra/shared"] --> backupbucket[("Backup bucket")]
    shared --> iam["Project IAM for the administrator"]
    primary["infra/primary"] --> module["modules/regional_cluster"]
    primary --> disk["Worker data disk"]
    recovery["infra/recovery, planned"] -.-> module
    statebucket -. "holds the state of" .-> shared
    statebucket -. "holds the state of" .-> primary
```

| Root | Creates | Lifetime |
| --- | --- | --- |
| `infra/bootstrap` | The state bucket and the operator's access to it | Permanent. Starts with local state, then migrates into the bucket it created. |
| `infra/shared` | The backup bucket, its IAM, and the administrator's IAP, OS Login, and instance admin roles | Permanent. Shared by every region. |
| `infra/primary` | The Finland cluster through the module, plus the worker data disk | Permanent. The data disk has `prevent_destroy`. |
| `infra/recovery` (planned) | The Belgium cluster through the same module | Created for a drill, destroyed afterwards. |

Both buckets are in Belgium (`europe-west1`), so losing Finland loses neither the state nor the backups. Each root stores its state under its own prefix in the state bucket.

## What the module creates

`modules/regional_cluster` is one cluster in one region. The primary and the planned recovery root call it with different inputs.

```mermaid
flowchart TB
    internet(["Internet"]) -- "80, 443" --> address["Static address"]
    address --> rule["Forwarding rule"] --> backend["Backend service, passthrough"] --> worker

    subgraph vpc["VPC and subnet"]
        cp["Control-plane VM"]
        worker["Worker VM"]
        disk[("Data disk")]
        worker --- disk
        cp <-- "all ports, subnet only" --> worker
    end

    iap["IAP range 35.235.240.0/20"] -- "22" --> cp
    iap -- "22" --> worker
    vpc -- "outbound only" --> nat["Cloud NAT"] --> out(["Internet"])
```

| Resource | Detail |
| --- | --- |
| VMs | Two, Ubuntu 24.04 from a pinned image, 2 vCPU and 4 GiB each, Shielded VM, no external address |
| Outbound traffic | Cloud NAT, for package and image downloads |
| Inbound SSH | Only from Google's IAP range. OS Login is on and project SSH keys are blocked. |
| Public traffic | A regional passthrough load balancer sends ports 80 and 443 to the worker. TLS ends in the cluster, not at the load balancer. |
| Node service accounts | One per node. The worker may read and create Cloud Storage objects; the control plane has no API scope. |

## Firewall rules

| Rule | From | To | Ports |
| --- | --- | --- | --- |
| `iap-ssh` | IAP range | Both nodes | 22 |
| `node-internal` | The subnet | Both nodes | All |
| `public-web` | Internet | Worker | 80, 443 |

Nothing else reaches the nodes. The Kubernetes API listens on the control plane's private address only.

## How it connects to the next layer

`make inventory` reads the root's outputs (instance names, private addresses, project, zone, subnet) and writes the Ansible inventory. Terraform and Ansible share nothing else.

## Limits

- One zone and one worker. A zone failure is an outage.
- Terraform runs from the operator machine with personal credentials. There is no pipeline.
- The backup retention policy is unlocked, so a project owner can remove it.

See [decision 0003](../decisions/0003-regional-infrastructure-and-state.md) and the [primary infrastructure runbook](../runbooks/primary-infrastructure.md).
