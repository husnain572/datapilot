# DataPilot security

DataPilot runs code written by an AI model, on data uploaded by strangers. We treat both as hostile. This document describes the intended design and what it can't protect against.

> **Status:** the sandbox controls below are built, and were attack-tested on the real server. Results are in [docs/BUILD_STATUS.md](docs/BUILD_STATUS.md). Testing can show that specific attacks fail, but it can't guarantee that all attacks will.

## What we protect

- API keys and other secrets
- The server and the other services running on it
- One user's data from other users
- The accuracy of the numbers we show

## Layers of defense

| Layer | Control | Stops |
|---|---|---|
| Network | Firewall allows only ports 22, 80, and 443. Port 80 only redirects to HTTPS. Nginx exposes only Streamlit. The backend and executor listen only on the server itself. | Direct attacks on internal services |
| Application | Upload limits, input validation, one or two analyses at a time, a daily AI request limit, and hourly limits for each visitor (no login) | Bad files, abuse, using up the free AI allowance |
| File formats | Every format is converted to CSV and given the same checks. Excel files are checked for zip bombs before loading, and parsed with XML attacks blocked (defusedxml). Parquet size is checked from its metadata first | Hostile spreadsheets and compressed files |
| Executor | Separate service and user, the only one with Docker access. Token-protected, can't read `.env`, and applies fixed sandbox settings | A compromised backend or bad code choosing weaker settings |
| Sandbox | The settings listed below | Damage from generated code |
| Output | No files leave the sandbox, only capped JSON validated by Pydantic. Charts are plain values, and text is shown without links or images | Malicious output reaching users |
| Results | Independent recalculation and a number check on the AI's text | Wrong or invented numbers |

## Sandbox settings

| Setting | What it does |
|---|---|
| `--network none` | No internet, so data and secrets can't be sent out |
| `--read-only` | Code can't change the container's system files |
| Non-root user | Code runs without administrator rights |
| `--cap-drop ALL` | Removes special Linux privileges |
| `--security-opt no-new-privileges` | Code can't gain more privileges later |
| Default seccomp profile | Blocks risky requests to the Linux kernel |
| `--memory`, `--cpus`, `--pids-limit` | Limits memory, CPU, and process count, which stops memory and fork bombs |
| File-size limit, 64 MB temporary space in memory, capped output | Code can't fill the server's disk |
| Timeout enforced from outside | Stops infinite loops |
| Read-only dataset, no environment secrets | Code can't alter the data or find keys |
| New container per attempt | Nothing carries over between runs |

gVisor isn't in use. The executor has a setting for it, so it can be added later as a second barrier.

## Remaining risks

Docker doesn't guarantee complete security. These risks remain:

1. **Kernel escape.** Containers share the server's Linux kernel, so a kernel bug could let code break out. Mitigations: keep the server patched, and keep little of value on it. The AI key is on this server, so an escape could expose it. It's a free-tier key with no payment attached.
2. **Executor compromise.** The executor has Docker access, which amounts to root on the server. It's kept small, private, and token-protected. Its user can also read the app's folders except `.env`, including uploaded datasets.
3. **Prompt injection.** Text in a CSV could try to manipulate the AI. Bad code would still run in the sandbox and its numbers would still be checked, but a misleading explanation is possible.
4. **Wrong analysis logic.** Validation can show that a calculation was performed correctly. It can't prove it was the right calculation for the question, so we show the code and the assumptions.
5. **Shared server.** The website, backend, and sandboxes run on one small server. Traffic is encrypted with HTTPS (a free Let's Encrypt certificate that renews itself).
6. **Open access.** There's no login, so a determined person could use up the day's free AI allowance, for example from many network addresses. Visitors would then see the saved example and a message to try tomorrow. Spreadsheet and Parquet parsing also run in the backend process, not the sandbox, so a bug in those libraries is a risk.
7. **AI providers.** Prompts go to Groq, to OpenAI if `AI_PROVIDER=openai`, and to Google if the Gemini fallback is turned on. Check each provider's terms for how free-tier data is used. We send column details, statistics, and small samples, never a whole dataset, but those samples can include real values. Don't upload sensitive data.
8. **Public demo abuse.** Limits reduce abuse but won't stop a determined attacker. Don't upload sensitive data to the demo.

Checks on generated code are only an early warning and can be bypassed. The sandbox is the real boundary.

## Handling secrets

- Secrets live only in `.env`, which Git ignores.
- Secrets never enter the sandbox or the logs.
- The executor gets its token from its own file, without the AI keys.
- The frontend never reads `.env`. On the server, it gets only the backend's address from `frontend.env`, so AI keys never enter the Streamlit process.
- We review staged files before every push, and scan the repo before it goes public.
- If a key leaks, revoke it in the Vultr console first, then remove it from the code. Deleting it from Git isn't enough, because it stays in the history.

## Reporting a problem

Please open a GitHub issue without exploit details, and ask the maintainer for a private way to share them.
