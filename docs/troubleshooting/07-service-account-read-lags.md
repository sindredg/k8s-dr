# The return read the primary worker account as still disabled

Observed on 2026-10-17 in the `Return` run 39056248685, during the post-merge verification of [milestone 10](../worklogs/10-automated-recovery.md#after-the-merge-verification).

## Symptom

The `lift-fence` stage failed one second after the enable call reported success:

```text
2026-10-17T18:05:09.7732960Z $ gcloud iam service-accounts enable k8sdr-primary-worker@<project>.iam.gserviceaccount.com
2026-10-17T18:05:10.5524159Z Enabled service account [k8sdr-primary-worker@<project>.iam.gserviceaccount.com].
2026-10-17T18:05:10.6610570Z $ gcloud iam service-accounts describe k8sdr-primary-worker@<project>.iam.gserviceaccount.com '--format=value(disabled)'
2026-10-17T18:05:11.3223150Z error: lift-fence failed: the primary worker account reports disabled=True
```

## Cause

Confirmed: the enable took effect, and the read that followed returned the old value. Before the resumed run started at 18:07:09Z, `gcloud iam service-accounts list` on the operator machine returned `disabled` as `False` for the account, with no other enable in between. The resumed run 39056698142 enabled the account again at 18:08:16Z, read it once, and passed.

`scripts/return_to_primary.py` read the account once, straight after the change, and failed the stage on the first answer. The `fence` stage of `scripts/recover.py` did the same after the disable. The gate run of the return on the same day passed with the same code, so the delay is not constant.

Not confirmed: how long such a delay can last. The project has one observation of it, shorter than two minutes.

## Fix

Both stages poll the account every five seconds until it reports the expected value, for at most two minutes (`ACCOUNT_TIMEOUT` in `scripts/recover.py`). A value that never changes still fails the stage. The [regional recovery runbook](../runbooks/regional-recovery.md) tells an operator to read again after a minute.

## Verify

Local, against stubs, 2026-10-17. `test_the_fence_waits_for_the_account_to_read_disabled` and `test_the_fence_lift_waits_for_the_account_to_read_enabled` answer the first read with the old value and the second with the new one. Both failed against the code before the fix and pass after it. The tests that keep an account in its old state still fail the stage.

The fix has not run live. The next `Recover` and `Return` runs from `main` after the merge exercise it.
