# The CAA records of the zone were missing

Observed on 2026-10-16 on the operator machine, during a review of the public repository and the running primary.

## Symptom

`make surface` reported a regression:

```text
REGRESSED caa              no CAA record
```

`dig +short CAA sindrg.com`, against the local resolver and against `1.1.1.1`, returned nothing.

## Cause

Confirmed: the zone held no CAA record. A read-only listing of every record in the `sindrg.com` zone through the Cloudflare API returned two A records, `git` and `git-primary`, and nothing else. The two CAA records added on 2026-10-02 (see the [service hardening worklog](../worklogs/04c-service-hardening.md#caa-record)) were gone.

Not confirmed: when and how they were removed. The token in the repository has DNS access to the zone only and cannot read the Cloudflare audit log. No script in the repository deletes CAA records; `scripts/dns_record.sh` handled A records only.

Confirmed: the daily `Public surface` workflow, which would have reported the regression the next morning, had been disabled by hand since 2026-10-05. Its last run, on 2026-10-05, passed. The `Pins` workflow was disabled at the same time, so the pin check also skipped every pull request after that date.

## Fix

- `make dns-caa` runs `scripts/dns_record.sh caa`, which creates each missing CAA record of the zone apex through the Cloudflare API with the cert-manager token. It never deletes a record, and exits 1 if the zone holds a CAA record outside the expected set. A test keeps its records identical to `CAA_EXPECTED` in `scripts/check-public-surface.sh`.
- The [service deployment runbook](../runbooks/service-deployment.md) runs it in step 6, so a rebuild recreates the records.
- The `Pins` and `Public surface` workflows were enabled again on 2026-10-16. A manual `Pins` run passed the same day.
- The ruleset of `main` requires the `validate`, `docs`, and `pins` checks since 2026-10-16. A disabled `Pins` workflow now blocks every merge instead of passing unnoticed. `Public surface` is not required: it runs on a schedule and only on pull requests that change it.

## Verify

On 2026-10-16, `make dns-caa` ran against the live zone from the operator machine. It reported success; its output was not captured. Then, from the operator machine:

```text
$ dig +short CAA sindrg.com @1.1.1.1
0 issue "letsencrypt.org"
0 issuewild ";"

$ make surface
ok        caa              certificate issuance is restricted
13 ok, 1 known open, 0 regressed, 0 resolved, 0 inconclusive
```

The authoritative server `eva.ns.cloudflare.com` returned the same two records. The surface check compares the whole CAA answer with its expected set, so this also shows that no other CAA record exists.
