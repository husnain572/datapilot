# DataPilot security

DataPilot runs code written by an AI model, on data uploaded by strangers. We treat both as hostile. This document describes the intended design and what it can't protect against.

> **Status:** none of the sandbox controls below are built yet. They are the design for Phase 3 and will be marked as tested only after real attack tests pass.

## What we protect

- API keys and other secrets
- The server and the other services running on it
- One user's data from other users
- The accuracy of the numbers we show

## Layers of defense

| Layer | Control | Stops |
|---|---|---|
| Network | Firewall allows only ports 22, 80, 443 (to be verified). Nginx exposes only Streamlit. Executor isn't public. | Direct attacks on internal services |
| Application | Upload limits, input validation, rate limits, demo access code, daily AI spending cap | Bad files, abuse, runaway costs |
| Executor | Separate service, the only one with Docker access, applies fixed sandbox settings | A compromised backend or bad code choosing weaker settings |
| Sandbox | The settings listed below | Damage from generated code |
| Output | Pydantic validation, file type checks, symlink rejection, no generated HTML shown | Malicious output reaching users |
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
| File-size limits and fixed-size disk area | Code can't fill the server's disk |
| Timeout enforced from outside | Stops infinite loops |
| Read-only dataset, no environment secrets | Code can't alter the data or find keys |
| New container per attempt | Nothing carries over between runs |

gVisor (`runsc`) will be added as a second barrier if it works on our Vultr VM.

## Remaining risks

Docker doesn't guarantee complete security. These risks remain:

1. **Kernel escape.** Containers share the server's Linux kernel, so a kernel bug could let code break out. gVisor reduces this risk but doesn't remove it. Mitigations: keep the server patched, and keep little of value on it.
2. **Executor compromise.** The executor has Docker access, which amounts to root on the server. It's kept small, private, and token-protected.
3. **Prompt injection.** Text in a CSV could try to manipulate the AI. Bad code would still run in the sandbox and its numbers would still be checked, but a misleading explanation is possible.
4. **Wrong analysis logic.** Validation can show that a calculation was performed correctly. It can't prove it was the right calculation for the question, so we show the code and the assumptions.
5. **Free AI provider.** Prompts go to OpenRouter and its model hosts, and free models may keep them. We send column details, statistics, and small samples, never a whole dataset, but those samples can include real values. Don't upload sensitive data.
6. **Public demo abuse.** Limits reduce abuse but won't stop a determined attacker. Don't upload sensitive data to the demo.

Checks on generated code are only an early warning and can be bypassed. The sandbox is the real boundary.

## Handling secrets

- Secrets live only in `.env`, which Git ignores.
- Secrets never enter the sandbox or the logs.
- We review staged files before every push and scan the repo before it goes public.
- If a key leaks, revoke it in the Vultr console first, then remove it from the code. Deleting it from Git isn't enough, because it stays in the history.

## Reporting a problem

Please open a GitHub issue without exploit details, and ask the maintainer for a private way to share them.
