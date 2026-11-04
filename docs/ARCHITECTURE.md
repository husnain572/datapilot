# DataPilot architecture

> This describes the target design. [BUILD_STATUS.md](BUILD_STATUS.md) shows what exists today.

## Overview

```
Browser ──HTTPS──> Caddy ──> Streamlit (screens)
                               │ REST
                               ▼
                    FastAPI backend ──HTTPS──> Vultr Serverless Inference
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
| Vultr AI model | Recipe writer | Plans, writes code, and explains, but never supplies numbers |
| Executor | Keeper of the kitchen keys | Builds each sandbox the same safe way, every time |
| Sandbox | Sealed, disposable kitchen | Runs the code, then gets thrown away |
| Caddy | Front door | Handles HTTPS and is the only public entrance |

## Data flow for one question

1. The user uploads a CSV. The backend validates it, stores it, and profiles it.
2. The user asks a question in Streamlit.
3. The backend sends the question and dataset profile to the AI, which returns a structured plan.
4. The AI writes Python code, and the backend runs a quick check on it.
5. The backend sends the code and dataset to the executor.
6. The executor starts a new sandbox, enforces the time limit, collects the output, and removes the container.
7. The backend validates `result.json` and any charts, then recalculates key figures on its own.
8. If the run failed, a sanitized error goes back to the AI. There's one first attempt plus at most two retries, each in a new container.
9. The AI explains the validated results, and a check confirms that its numbers match them.
10. Streamlit shows the plan, code, logs, verified numbers, charts, and recommendations.

## Key decisions

| Decision | Why |
|---|---|
| Plain Python workflow instead of LangGraph | The steps are fixed and retries are limited, so a simple loop is easier to read and debug |
| Separate executor service | Docker access amounts to root on the server, so neither the backend nor the sandbox gets it |
| Docker runs only on the Vultr VM | Current Docker Desktop doesn't support macOS 12, and generated code must never run on the owner's Mac |
| Files on disk, no database | One folder per dataset with a JSON details file. There's no database to run, which suits one VM. |
| Plotly JSON and PNG charts only | Generated HTML could run scripts in a user's browser |
| Declared checks, no `eval` | Lets the backend recalculate figures without running AI-written code |

## How results are verified

1. **Fixed format:** Pydantic validates the sandbox's `result.json`.
2. **File checks:** unexpected files and symlinks are rejected, and file types are checked by their contents.
3. **Recalculation:** the AI declares key figures as simple checks, such as `sum(quantity*unit_price)`. The backend's own code computes them and shows **Verified ✓** or **Mismatch ✗**.
4. **Number check:** numbers in the written explanation must match validated results. If not, it's regenerated once, then the app shows only tables and charts.
5. **Clear labels:** observations, assumptions, and recommendations are kept separate.

## Development and production

| | Your Mac (development) | Vultr VM (production) |
|---|---|---|
| Streamlit and FastAPI | Run directly with uv | Docker Compose |
| Sandbox | Reached through an SSH tunnel to the VM | Same VM, private network |
| HTTPS | Not needed | Caddy |

See [SECURITY.md](../SECURITY.md) for the security design and remaining risks.
