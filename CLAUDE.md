# DataPilot: instructions for Claude Code

DataPilot is an AI data analyst for the Vultr: Agent Rush Hackathon (Challenge 1, "Blast Radius Zero"). Current progress is in `docs/BUILD_STATUS.md`. The plan is in `docs/IMPLEMENTATION_PLAN.md`.

## The owner is a beginner who is learning
For each major task, explain in simple English: what we're doing, why, what the technology is, what you'll change, what they need to do, how to verify it, and what they learned. Teach only what's needed at that moment. Explain any command before asking them to run it.

## Core principle
AI plans, writes code, and explains. Python calculates. **The AI must never supply a numerical result.** Every number shown comes from validated sandbox output.

## Rules that must not be broken
- Never run generated code on the Mac or inside the backend process. It runs only in the sandbox on the Vultr VM.
- Docker is not available on this Mac (macOS 12, Intel). Don't try to install it.
- Only the executor may access the Docker socket. Never give it to the backend or the sandbox.
- Keep secrets out of the sandbox, logs, and Git. Never ask for API keys in chat. The owner enters them in `.env`.
- Use Vultr Serverless Inference for runtime AI, and never silently swap providers.
- Check real model IDs at `GET https://api.vultrinference.com/v1/models`. The current API reference is https://api.vultrinference.com/docs. Older articles on docs.vultr.com are out of date.
- `~/.git` exists in the home folder. Run Git commands only inside this project. Don't delete `~/.git` without approval.
- Never claim something works without running it. Record real test output in `docs/BUILD_STATUS.md`.
- Get approval before destructive commands, spending money, or making the repo public.

## Commands
```bash
uv sync
uv run uvicorn backend.main:app --reload
uv run streamlit run frontend/app.py
uv run pytest
```

## Working style
- Build in small milestones. After each one, run the tests, update `docs/BUILD_STATUS.md`, and summarize.
- Add dependencies only when a phase needs them.
- At the end of each session, record what's done, what remains, and exactly where to continue.
