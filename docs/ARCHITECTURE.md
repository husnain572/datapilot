# DataPilot architecture

## Overview

```
Browser ──HTTPS──> Nginx ──> Streamlit (screens)
                               │ REST
                               ▼
                    FastAPI backend ──HTTPS──> OpenAI (or Groq), optional Gemini fallback
                  (agent, validation, reports)
                               │ private network + token
                               ▼
                    Executor (the only part allowed to control Docker)
                               │ starts one throwaway container per attempt
                               ▼
                    Sandbox: no network, no secrets, strict limits
```

## In plain English: a restaurant

| Component | Restaurant role | Job |
|---|---|---|
| Streamlit | Dining room | Screens for uploading, asking, and viewing results |
| FastAPI | Manager | Coordinates the work and checks results before they go out |
| AI model | Recipe writer | Plans, writes code, and explains, but never supplies numbers |
| Executor | Keeper of the kitchen keys | Builds each sandbox the same safe way, every time |
| Sandbox | Sealed, disposable kitchen | Runs the code, then gets thrown away |
| Nginx | Front door | The only public entrance; it sends visitors to Streamlit |

## Data flow for one question

1. The user uploads a CSV. The backend validates it, stores it, and profiles it.
2. The user asks a question in Streamlit.
3. The backend sends the question and a dataset summary to the AI, which returns a plan and Python code together. This saves free AI requests.
4. The backend checks that the plan uses only real columns, and runs a quick check on the code.
5. The backend sends the code and dataset to the executor.
6. The executor starts a new sandbox, enforces the time limit, collects capped output, and removes the container.
7. The backend validates the results and charts, then recalculates key figures on its own.
8. If the run or a recalculation failed, a sanitized error goes back to the AI. There's one first attempt plus at most two retries, each in a new container.
9. The AI explains the validated results, and a check confirms that its numbers match them.
10. Streamlit shows the plan, code, logs, verified numbers, charts, and recommendations.

## Key decisions

| Decision | Why |
|---|---|
| A modular AI provider: OpenAI (official SDK, Responses API) in production, with Groq and Gemini as options | A small router tries each provider in turn, within its daily request and token caps, so providers can be switched by settings. |
| Plain Python workflow instead of LangGraph | The steps are fixed and retries are limited, so a simple loop is easier to read and debug |
| Separate executor service | Docker access amounts to root on the server, so neither the backend nor the sandbox gets it |
| Docker runs only on the Vultr VM | Current Docker Desktop doesn't support macOS 12, and generated code must never run on the owner's Mac |
| Files on disk, no database | One folder per dataset with a JSON details file. There's no database to run, which suits one VM. |
| Charts are plain values, drawn by the frontend | Generated HTML could run scripts in a user's browser |
| No writable folder shared with the sandbox | Results return as capped output, so generated code can't fill the server's disk |
| JSON format described in the prompt | Free models hung when sent `response_format` in testing, and Pydantic still checks every reply |
| Declared checks, no `eval` | Lets the backend recalculate figures without running AI-written code |

## How results are verified

1. **Fixed format:** Pydantic validates everything the sandbox reports, and rejects anything unexpected.
2. **No files:** nothing leaves the sandbox except that validated data.
3. **Recalculation:** the AI declares key figures as simple checks, such as `sum(quantity*unit_price)`. The backend's own code computes them and shows **Verified ✓** or **Mismatch ✗**.
4. **Number check:** numbers in the written explanation must match validated results. If not, it's regenerated once, then any statement that still fails is removed.
5. **Clear labels:** observations, assumptions, and recommendations are kept separate.

## Development and production

| | Your Mac (development) | Vultr VM (production) |
|---|---|---|
| Streamlit, FastAPI, executor | Run directly with uv (no executor) | systemd services |
| Sandbox | Not available, because Docker doesn't run on this Mac | Same VM, reached on the server's own loopback address |
| Public access | Not needed | Nginx with HTTPS at https://datapilot-husnain.duckdns.org (free DuckDNS name and Let's Encrypt certificate) |

See [SECURITY.md](../SECURITY.md) for the security design and remaining risks.
