# 0016: Drills on demand

Status: Accepted on 2026-10-19. It changes parts of [decision 0015](0015-unattended-drills.md), [decision 0013](0013-automated-recovery.md), and [decision 0008](0008-cold-recovery.md), and each of them points here. [Milestone 11](../worklogs/11-unattended-drills.md) builds and measures it.

Date: 2026-10-19

## Context

The first unattended drill ran on 2026-10-19, two days after the drills were switched on, and failed in the restore ([troubleshooting 08](../troubleshooting/08-restore-dump-check-sigpipe.md)). Three parts of the design made that failure costly and made the next drill wait:

- Each recovery issues two production certificates, one for `git.sindrg.com` and one for `git-dr.sindrg.com`. Let's Encrypt allows five certificates for the same set of names every seven days and refills one every 34 hours. Decision 0015 runs three drills a week to stay under that limit, so a fix waits up to two days for its drill, and every rehearsal of the recovery spends the same budget.
- A drill whose recovery fails skips the return ([decision 0015](0015-unattended-drills.md#a-drill-workflow-starts-and-ends-each-drill)), and the primary stays stopped. In drill `20261019T070024Z` the public name did not serve from the isolation at 07:31:05Z until a `Return` run dispatched by hand started the primary at 13:58:23Z.
- After a failed automatic start, the watchdog starts nothing until a newer `Recover` run passes ([decision 0013](0013-automated-recovery.md#what-it-starts)). The `Return` run that restored the primary did not clear that rule, so automatic recovery stayed off after the service was back, and the drill guard, which applies the same rule, refused every drill.

## Decisions

### A drill can start at any time

The `Drill` workflow already accepts a manual dispatch:

```bash
gh workflow run drill.yml --ref main
```

With the changes below, a dispatched drill spends no budget of the limit for the same set of names, and it ends on the primary whatever the recovery does, so a drill can follow a fix the same day. The schedule stays, and `SCHEDULED_DRILLS` switches both triggers. A dispatched drill runs the same guard and the same steps, and after the dispatch nobody acts until the issue opens. The [milestone 11](../worklogs/11-unattended-drills.md) gate therefore counts drills started by a dispatch as well as by the schedule. The [results page](../drills.md) already records which trigger started each drill.

### Each recovery run adds one name to its certificates

| Option | Effect | Problem |
| --- | --- | --- |
| One name per certificate, as before | Three drills a week, plus rehearsals, share five certificates | A failed drill waits up to two days for the next one |
| Carry the primary's certificates in the backup | The recovery issues nothing and does not depend on Let's Encrypt | The primary must also hold a certificate for `git-dr.sindrg.com`, the backup set gains the private keys and a new file, and the recovery must install the Secrets before Flux applies the certificates. More than can be built and tested before the Cloudflare token expires |
| Let's Encrypt staging for drills | No production limit | Clients and the drill's own checks do not trust the staging root, so a drill would not test the public service |
| A second certificate authority | A separate limit | A new ACME account with an external account binding, a CAA change, and one more provider that the recovery depends on |
| One more name per recovery run (selected) | Each run orders sets of names that no earlier run used, so the limit for the same set does not apply. The limit of 50 certificates per registered domain every seven days, refilled one every 202 minutes, does | The extra name appears in Certificate Transparency logs, and the recovery still depends on Let's Encrypt |

Let's Encrypt's [rate limit documentation](https://letsencrypt.org/docs/rate-limits/) describes this: "you can change the set of identifiers by adding `blog.example.com` to request additional certificates." Such orders are not renewals. A recovery issues two certificates, so about 25 recoveries fit in seven days, then one every 404 minutes.

The extra name is `run-<start>.sindrg.com`, for example `run-20261019t151356z.sindrg.com`, where `<start>` is the start time in the recovery run state:

1. `scripts/recover.py` records the name in the run state at the `bootstrap` stage, so a resumed run keeps it, and passes it to `make bootstrap`.
2. The Flux role writes it to the `certificate-settings` ConfigMap in `flux-system` before it applies the Flux sync.
3. On the recovery cluster only, the `certificates` Kustomization builds `deploy/clusters/recovery/certificates`, which adds the name to both certificates, and reads `certificate-settings` after `cluster-settings`.

The primary's certificates do not change. A bootstrap from the runbook creates no `certificate-settings`, so it uses the default `recovery.sindrg.com` from the recovery `cluster-settings`, and the limit of five a week applies to that set as before.

The name needs no address record. cert-manager proves it with a TXT record under the name, through the same Cloudflare token, and the CAA record on `sindrg.com` covers it.

### A drill ends on the primary

The return step runs after every step. Once the drill has stopped the primary:

- If a `Recover` run of this drill recorded a recovery environment, the drill dispatches the `Return` workflow, as after a passed recovery. The test is the precondition of the `Return`: the run state is newer than the isolation, `provisioning` passed, and it holds the recovery address.
- Otherwise the drill starts the primary VMs again, as it already did when no `Recover` run started within an hour.

This includes a recovery that failed and a drill that missed a target. Decision 0015 skipped the return in both cases to keep the recovery environment for diagnosis. The diagnosis of drill `20261019T070024Z` used the run state, the stage timeline, and the workflow logs, which all remain; it did not need the cluster. A drill on demand can repeat the failure once a fix is ready, so keeping a broken recovery cluster, and the outage with it, buys little.

The issue names the first step that failed, and a failed return is recorded beside it. The `Return` discards every write that the recovery cluster accepted, as after every drill.

### A return that passed clears a failed automatic start

The watchdog and the drill guard start nothing after a failed automatic `Recover` run, unless a `Return` run created after it passed. That return started the primary, pointed the public name at it, lifted the fence, and removed the recovery environment, so nothing that the failed start left remains.

After a real loss of the primary, a return cannot pass, so the rule of decision 0013 still holds there: a person fixes the cause and resumes or reruns the recovery.

## Trade-offs

- Each drill is a real outage of `git.sindrg.com`, about 45 minutes when it passes. More drills mean more outages. A failed drill now also ends with the primary serving, about one recovery plus one return after the isolation.
- A scheduled drill after a failed one is no longer refused. If the cause is not fixed, it fails the same way and causes one more outage. Turn `SCHEDULED_DRILLS` off while a cause is open.
- The recovery's certificates carry a name that nothing serves. It is visible in Certificate Transparency logs.
- The recovery still needs Let's Encrypt and the Cloudflare API to answer. Carrying the certificates in the backup removes that dependency. Revisit it if the recovery must work while either is unavailable.
- Each recovery spends two of the 50 certificates a week for `sindrg.com`.
