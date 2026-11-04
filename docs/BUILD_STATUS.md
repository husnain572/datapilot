# DataPilot build status

**Last updated:** Nov 4, 2026

## Where to continue

**Deadline: Nov 7, 2026, 12:00 am PKT. Target: submit by Nov 6, 9:00 pm PKT, or earlier.**

1. Merge the `phase-2-upload-profiling` pull request into `main`.
2. Create the Vultr VM, which is blocking the sandbox work. Check that the SSH key works for logging in.
3. Build Phase 3 (sandbox and executor) on the VM.
4. Create the Serverless Inference subscription for Phase 4.

GitHub uses HTTPS by the owner's choice (SSH gave a permission error, which wasn't investigated).

## Progress

| Phase | Status | Notes |
|---|---|---|
| 0 Docs | Done | |
| 1 Foundation | Done | Pushed to GitHub over HTTPS |
| 2 Upload and profiling | Done on its branch, tested on the Mac | Waiting for merge; not yet deployed |
| 8a Early deploy | Not started | Vultr credit applied; VM not created |
| 3 Sandbox | Not started | Needs the VM, since Docker runs only there |
| 4 Vultr AI client | Not started | Needs an inference API key |
| 5 Agent workflow | Not started | |
| 6 Dashboard | Partly done | Upload and profile screens exist; analysis screens don't |
| 7 Testing | Not started | |
| 8b Production hardening | Not started | |
| 9 Delivery | Not started | |

## What works today

Tested on the owner's Mac (macOS 12.7.6, Intel, Python 3.11.0):

- **Upload:** CSV upload with validation, normalized storage, random IDs, and clear error messages.
- **Rejected files tested:** wrong file type, too large, binary, empty, header-only, uneven rows, duplicate or blank column names, unclosed quotes, too many rows or columns, and malicious filenames.
- **Profile:** row and column counts, column types, missing values, duplicates, number statistics, category summaries, and sample records.
- **Other API features:** preview, delete, and automatic removal after 24 hours.
- **Screen:** upload, sample dataset button, profile dashboard, and remove button.

**Not built yet:** sandbox, AI, analysis, reports, and deployment. `SECURITY.md` describes a sandbox design that hasn't been tested.

**Not checked by eye:** the page has been tested automatically, but not yet viewed in a browser with the new upload screens.

## Latest test results (Nov 4, 2026)

```
uv run pytest -q
64 passed, 1 warning

Sample dataset, real page and real backend:
Rows 1,010 | Columns 5 | Missing values 30 | Duplicate rows 13
Tabs: Columns, Number statistics, Categories, Preview. 0 exceptions.
Remove button: dataset deleted, nothing left on disk.

Real API requests:
Upload sample -> HTTP 201
Bad row count -> HTTP 422, "Line 2 has 1 values, but the header has 2 columns."
Not a CSV     -> HTTP 415, "Please upload a .csv file."
Delete        -> HTTP 204, then HTTP 404
```

The sample's missing and duplicate counts are also confirmed by a separate calculation in the tests. Rows are 1,000 generated plus 10 planted duplicates. The 13 duplicates are those 10 plus 3 that occurred by chance.

## Known issues

- pytest shows a deprecation warning from a library: Starlette's test client plans to move away from `httpx`. It doesn't affect the app.
- Pandas treats text like `NA` as missing, so a region code of "NA" (North America) would be counted as missing. To be revisited if time allows.
- The full upload is received before the size limit applies. Caddy must also limit request size in production (Phase 8a).
- `~/.git` exists in the home folder. It doesn't affect this project, but `git add` run from the home folder could stage personal files.

## Open questions

Tracked in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#open-questions).
