# DataPilot build status

**Last updated:** Nov 4, 2026

## Where to continue

1. The owner creates an SSH key, adds it to GitHub, and pushes to a private repo.
2. The owner creates accounts: lablab.ai and Discord, Vultr with the gift code.
3. Confirm the exact submission deadline.
4. Start Phase 8a (early deploy), reusing the same SSH key for the Vultr server.

## Progress

| Phase | Status | Notes |
|---|---|---|
| 0 Docs | Done, awaiting owner review | All documents written |
| 1 Foundation | Code done and tested, first commit made (`d30a5b3`); **GitHub push still to do** | See results below |
| 8a Early deploy | Not started | Needs a Vultr account |
| 2 Upload and profiling | Not started | |
| 3 Sandbox | Not started | Needs the VM, since Docker runs only there |
| 4 Vultr AI client | Not started | Needs an inference API key |
| 5 Agent workflow | Not started | |
| 6 Dashboard | Not started | |
| 7 Testing | Not started | |
| 8b Production hardening | Not started | |
| 9 Delivery | Not started | |

## What works today

Tested on the owner's Mac (macOS 12.7.6, Intel, Python 3.11.0):

- The FastAPI backend starts and answers `/health`, `/ready`, and `/docs`.
- The Streamlit page starts and shows whether the backend is connected.
- `.gitignore` excludes `.env`, `data/`, and `.venv`, and keeps `.env.example` and sample CSVs.

**Not built yet:** upload, sandbox, AI, and deployment. `SECURITY.md` describes a design that hasn't been tested.

## Latest test results (Nov 4, 2026)

```
uv run pytest -q
6 passed, 1 warning in 2.16s

Live servers:
GET /health  -> {"status":"ok"} [HTTP 200]
GET /ready   -> {"status":"ready","checks":{"data_dir_writable":true}} [HTTP 200]
GET /docs    -> [HTTP 200]
Streamlit health -> ok [HTTP 200]
Real page against real backend: "Backend connected.", 0 exceptions
```

## Known issues

- pytest shows a deprecation warning from a library: Starlette's test client plans to move away from `httpx`. It doesn't affect the app, so we've left it for now.
- `~/.git` exists in the home folder. It doesn't affect this project, but `git add` run from the home folder could stage personal files.

## Open questions

Tracked in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#open-questions).
