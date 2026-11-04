# DataPilot build status

**Last updated:** Nov 4, 2026

## Where to continue

**Deadline: Nov 7, 2026, 12:00 am PKT. Target: submit by Nov 6, 9:00 pm PKT, or earlier.**

1. The owner creates a free OpenRouter key, adds it to `.env` on the Mac as `AI_API_KEY`, and runs `uv run python scripts/check_inference.py`.
2. Merge `phase-3-vultr-inference` into `main` once that passes.
3. Start Phase 4 (analysis agent).

## Progress

| Phase | Status | Notes |
|---|---|---|
| 0 Docs, 1 Foundation | Done | |
| 2 Upload and profiling | Done and deployed | Merged in PR #1; the owner reports it working on the live site |
| Early deployment | Done by the owner | Vultr Ubuntu 24.04, Nginx, systemd, http://66.42.63.156 |
| 3 AI client | **Built and tested with fake replies; real call not yet run** | Now uses OpenRouter's free models; needs the owner's key |
| 4 Analysis agent | Not started | |
| 5 Code generation and Docker sandbox | Not started | Not yet checked whether Docker is installed on the server |
| 6 Validation and explanation | Not started | |
| 7 Dashboard and reports | Partly done | Upload and profile screens exist |
| 8 Testing and final deployment | Not started | |

## What works today

- **Live site:** http://66.42.63.156 returns Streamlit through Nginx. Every path goes to Streamlit, so the FastAPI backend isn't publicly reachable.
- **Upload and profiling:** see the tests below.
- **AI client (`backend/services/inference.py`):** works with any OpenAI-compatible API, with chat, structured output, retries, timeouts, and clear error types. It's not used by any screen yet.

**Not built yet:** agent, sandbox, analysis, and reports. `SECURITY.md` describes a sandbox design that hasn't been tested.

## AI provider facts (Nov 4, 2026)

**OpenRouter (in use), from its docs and live catalog:** `https://openrouter.ai/api/v1`. Free models need no credits, with limits of 20 requests a minute and 50 a day. The catalog listed 16 free entries, and `openrouter/free` is listed with structured output. Real behavior is unconfirmed until the first real call.

**Vultr Serverless Inference (not in use, available by changing settings):** source is https://api.vultrinference.com/docs and its live catalog. Older docs.vultr.com articles are out of date.

- **Address and login:** `POST https://api.vultrinference.com/v1/chat/completions`, with `Authorization: Bearer <key>`.
- **Structured output:** `response_format` with `json_schema`. The catalog lists this for `glm-5.3`, `glm-5.3-flash`, `deepseek-v4.1-flash-1017`, and `qwen3.8-flash-next`.
- **Limits:** most models allow 120 requests a minute and 16 at once. Requests can take about nine minutes before a 504.
- **Prices per million tokens (input/output):** `glm-5.3` $0.75/$3.00, `glm-5.3-flash` $0.10/$0.35, `deepseek-v4.1-flash-1017` $0.15/$0.60, `qwen3.8-flash-next` $0.10/$0.20.

## Latest test results (Nov 4, 2026)

```
uv run pytest -q
124 passed, 1 warning

uv run python scripts/check_inference.py   (no key set)
FAILED (InferenceNotConfiguredError): AI_API_KEY is not set. Add it to the .env file.
```

The inference tests use fake replies in the documented OpenAI-compatible format. **No real AI call has been made yet**, so model behavior, including strict `json_schema` handling, is still unconfirmed.

## Known issues

- pytest shows a deprecation warning from a library: Starlette's test client plans to move away from `httpx`. It doesn't affect the app.
- Pandas treats text like `NA` as missing, so a region code of "NA" (North America) would be counted as missing.
- The full upload is received before the size limit applies. Nginx should also limit request size (not yet checked on the server).
- The live site uses HTTP, so traffic isn't encrypted. Don't upload sensitive data.
- **Before AI goes live:** the public site has no access code or usage limit, so strangers could use up the free daily limit. Both must be added before the AI features are deployed.
- **Hackathon rule:** Challenge 1 requires Vultr Serverless Inference, so this project should enter Challenge 2 or get the organizers' approval.
- `~/.git` exists in the home folder. `git add` run from the home folder could stage personal files.

## Open questions

Tracked in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#open-questions).
