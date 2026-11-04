# DataPilot build status

**Last updated:** Nov 4, 2026

## Where to continue

**Deadline: Nov 7, 2026, 12:00 am PKT. Target: submit by Nov 6, 9:00 pm PKT, or earlier.**

1. The owner adds their inference key to `.env` on the Mac and runs `uv run python scripts/check_inference.py`.
2. Merge `phase-3-vultr-inference` into `main` once that passes.
3. Start Phase 4 (analysis agent).

## Progress

| Phase | Status | Notes |
|---|---|---|
| 0 Docs, 1 Foundation | Done | |
| 2 Upload and profiling | Done and deployed | Merged in PR #1; the owner reports it working on the live site |
| Early deployment | Done by the owner | Vultr Ubuntu 24.04, Nginx, systemd, http://66.42.63.156 |
| 3 Vultr AI client | **Built and tested with fake replies; real call not yet run** | Needs the owner's key |
| 4 Analysis agent | Not started | |
| 5 Code generation and Docker sandbox | Not started | Not yet checked whether Docker is installed on the server |
| 6 Validation and explanation | Not started | |
| 7 Dashboard and reports | Partly done | Upload and profile screens exist |
| 8 Testing and final deployment | Not started | |

## What works today

- **Live site:** http://66.42.63.156 returns Streamlit through Nginx. Every path goes to Streamlit, so the FastAPI backend isn't publicly reachable.
- **Upload and profiling:** see the tests below.
- **Inference client (`backend/services/inference.py`):** chat and structured output, with Vultr's retry rules, timeouts, and clear error types. It's not used by any screen yet.

**Not built yet:** agent, sandbox, analysis, and reports. `SECURITY.md` describes a sandbox design that hasn't been tested.

## Verified Vultr API facts (Nov 4, 2026)

Source: https://api.vultrinference.com/docs and the live model catalog. Older docs.vultr.com articles are out of date.

- **Address and login:** `POST https://api.vultrinference.com/v1/chat/completions`, with `Authorization: Bearer <key>`.
- **Structured output:** `response_format` with `json_schema`. The catalog lists this for `glm-5.3`, `glm-5.3-flash`, `deepseek-v4.1-flash-1017`, and `qwen3.8-flash-next`.
- **Limits:** most models allow 120 requests a minute and 16 at once. Requests can take about nine minutes before a 504.
- **Prices per million tokens (input/output):** `glm-5.3` $0.75/$3.00, `glm-5.3-flash` $0.10/$0.35, `deepseek-v4.1-flash-1017` $0.15/$0.60, `qwen3.8-flash-next` $0.10/$0.20.

## Latest test results (Nov 4, 2026)

```
uv run pytest -q
124 passed, 1 warning

uv run python scripts/check_inference.py   (no key set)
FAILED (InferenceNotConfiguredError): VULTR_SERVERLESS_INFERENCE_API_KEY is not set. Add it to the .env file.
```

All 60 inference tests use fake replies based on Vultr's documented formats. **No real call to Vultr has been made yet**, so model behavior, including strict `json_schema` handling, is still unconfirmed.

## Known issues

- pytest shows a deprecation warning from a library: Starlette's test client plans to move away from `httpx`. It doesn't affect the app.
- Pandas treats text like `NA` as missing, so a region code of "NA" (North America) would be counted as missing.
- The full upload is received before the size limit applies. Nginx should also limit request size (not yet checked on the server).
- The live site uses HTTP, so traffic isn't encrypted. Don't upload sensitive data.
- **Before AI goes live:** the public site has no access code or spending cap. Both must be added before the AI features are deployed.
- `~/.git` exists in the home folder. `git add` run from the home folder could stage personal files.

## Open questions

Tracked in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#open-questions).
