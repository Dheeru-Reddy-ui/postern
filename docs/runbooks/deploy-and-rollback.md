# Runbook: deploy and roll back

Deploys are automatic. Rollbacks are manual and deliberate. Set these first (PowerShell):

```bash
$P = "<project_id>"; $R = "us-central1"
```

## How a deploy happens

Every push to `main` runs `.github/workflows/ci.yml`:

1. **checks:** lint, types, tests, repo hygiene, and an image build.
2. **deploy** (only on `main`, only once the `GCP_*` repository variables exist):
   1. Authenticate through Workload Identity Federation. No JSON key.
   2. Build the image and push it to Artifact Registry as
      `postern:<sha>-<run_id>-<attempt>`.
   3. `gcloud run deploy postern-api --image <digest>` creates a new revision.
   4. `gcloud run jobs update postern-worker --image <digest>`.
   5. **Smoke test:** `GET /health` must report the pushed commit's `git_sha`, and one
      execution of `postern-worker` must succeed.

**Check a deploy:**

```bash
gh run watch --repo Dheeru-Reddy-ui/postern
```
```bash
curl "$(gcloud run services describe postern-api --project $P --region $R --format='value(status.url)')/health"
```

## Roll back postern-api

1. List revisions, newest first, and pick the last good one:
   ```bash
   gcloud run revisions list --service postern-api --project $P --region $R --limit 5
   ```
2. Send all traffic to it. This takes effect in seconds, with no rebuild:
   ```bash
   gcloud run services update-traffic postern-api --to-revisions <GOOD_REVISION>=100 --project $P --region $R
   ```
3. Verify that `/health` reports the old `git_sha`.

**Traffic is now pinned.** New deploys create revisions that receive no traffic, so CI's
smoke test fails on purpose until you roll forward.

## Roll back postern-worker

1. List recent images:
   ```bash
   gcloud artifacts docker images list "$R-docker.pkg.dev/$P/postern/postern" --include-tags --sort-by=~UPDATE_TIME --limit 5
   ```
2. Point the job at the last good digest:
   ```bash
   gcloud run jobs update postern-worker --image "$R-docker.pkg.dev/$P/postern/postern@sha256:<GOOD_DIGEST>" --project $P --region $R
   ```
3. Verify:
   ```bash
   gcloud run jobs execute postern-worker --project $P --region $R --wait
   ```

## Roll forward

After the fix is merged and deployed:

```bash
gcloud run services update-traffic postern-api --to-latest --project $P --region $R
```

Then re-run the failed deploy job, or push again, so the worker is updated and the smoke
test passes.

## Drift

Terraform ignores images, client fields and traffic (ADR-010), so neither deploys nor
rollbacks show up in `terraform plan`. Anything else that differs is real drift: find out
who changed it before re-applying.

## Records

- A rollback caused by a real problem is an incident. Write
  `docs/incidents/YYYY-MM-DD-slug.md` from `docs/incidents/_template.md`.
- A rollback done for practice (such as the Phase 1 "Done when") is a **game day** drill.
  Label it as one everywhere it's recorded.
