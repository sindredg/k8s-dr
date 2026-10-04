# 0011: External uptime probe

Status: Accepted on 2026-10-03. The check is applied, and it measured the outage of [drill 1](../worklogs/06-disaster-drill.md#drill-1) on 2026-10-04.

Date: 2026-10-03

## Goal

Measure recovery time from outside the failed region. [Decision 0002](0002-recovery-contract.md#measurement) starts the RTO at the first failed check of an external probe that runs every minute, and requires the probe before the [milestone 6](../../plan.md#6-disaster-drill) drill.

## Decision

Use a Google Cloud Monitoring uptime check, defined in `infra/shared`.

| Setting | Value | Reason |
| --- | --- | --- |
| Target | `https://git.sindrg.com/api/healthz` | The public name follows the DNS cutover, so one check reports the outage and the recovery. The path answers without sign-in and reports the database. |
| Period | 60 seconds | The interval that decision 0002 sets. |
| Timeout | 10 seconds | |
| Checkers | Europe, Virginia, Asia Pacific | Three is the minimum the API accepts. None runs on the cluster VMs. |
| Pass | A 2xx response with a valid certificate | A recovery cluster with a wrong or missing certificate must not count as recovered. |

The check has no alert policy. The drill reads its results afterwards; the Healthchecks.io heartbeat already alerts on missing backups, and nobody is on call for this service.

| Option | Trade-off |
| --- | --- |
| Cloud Monitoring uptime check (selected) | One Terraform resource in a root that exists, no new account or credential, and checkers in three regions. It lives in the same Google Cloud project as both clusters, so it does not survive the loss of the project or of Cloud Monitoring. |
| Grafana Cloud synthetic monitoring | Outside Google Cloud, next to the cluster dashboards. Needs a second provider or manual setup, and an access token that the recovery store must hold. |
| UptimeRobot free tier | Outside Google Cloud, but checks every five minutes, which is too coarse for a one-minute measurement. |
| Uptime Kuma on a VM outside Google Cloud | Fully owned, but one more server to build, patch, and pay for. |

The drill simulates the loss of a region, not of the project. A probe in the same project measures that. The limit is stated under [Known limits](#known-limits).

## Reading the results

After a drill, list the failed and passed checks between the isolation and the end of the cutover. Each checker region reports its own series.

```bash
PROJECT_ID="$(gcloud config get-value project)"
CHECK_ID="$(terraform -chdir=infra/shared output -raw uptime_check_id)"
curl -sS -G "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/timeSeries" \
  -H @<(printf 'Authorization: Bearer %s\n' "$(gcloud auth print-access-token)") \
  --data-urlencode "filter=metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id=\"${CHECK_ID}\"" \
  --data-urlencode "interval.startTime=<isolation time>" \
  --data-urlencode "interval.endTime=<end time>" |
  jq -r '.timeSeries[] | .metric.labels.checker_location as $l | .points[] | "\(.interval.endTime) \($l) \(.value.boolValue)"' | sort
```

The RTO starts at the earliest `false` line and stops when the fixture checks pass through `git.sindrg.com`. The first `true` line after the cutover shows when the probe saw the service again.

The command ran on 2026-10-04 with a ten-minute interval and the service up. Every line was `true`. The first lines:

```text
2026-10-04T08:31:00Z apac-singapore true
2026-10-04T08:31:00Z eur-belgium true
2026-10-04T08:31:00Z usa-virginia true
2026-10-04T08:31:10Z apac-singapore true
2026-10-04T08:31:10Z eur-belgium true
2026-10-04T08:31:10Z usa-virginia true
```

Each region reports a point every 10 seconds, although `gcloud monitoring uptime describe` shows `period: 60s`. Hypothesis, not confirmed: the metric repeats the latest result between checks. In drill 1 the three regions reported their first `false` at 14:28:40, 14:29:20, and 14:29:30 for one isolation, a spread of 50 seconds, which fits one check a minute per region at different offsets. Treat the first `false` line as accurate to the 60-second period, not to 10 seconds.

## Consequences

- `infra/shared` gains one resource and the output `uptime_check_id`. Applying it changes nothing else.
- Three checkers every minute run about 130,000 executions a month. Cloud Monitoring's free allotment was one million a month when this was written; confirm it in the pricing page before applying.
- The check keeps running between drills and shows any other outage of the public name.

## Known limits

- The probe and both clusters share one Google Cloud project. A project-level failure stops the probe with the service.
- The probe reports that the endpoint answers, not that the data is right. The fixture checks stop the RTO.
- A checker sees the cutover only after its resolver drops the old record. The 60-second TTL bounds that delay.
