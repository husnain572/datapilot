# DataPilot

**Your Data Analyst. Your Code. Verifiable Results.**

Upload a CSV and ask questions in plain English. DataPilot's AI writes the analysis code, Python runs it inside a locked-down sandbox, and the app shows only numbers it has checked.

> **Status: in development.** CSV upload and profiling are live. AI analysis is being built. See [docs/BUILD_STATUS.md](docs/BUILD_STATUS.md) for what works today.

## Why DataPilot is different

AI assistants can state a wrong number with complete confidence. DataPilot's AI doesn't get to supply the numbers:

- **AI** understands the question, plans the work, writes code, and explains the results.
- **Python** does every calculation, in a throwaway container with no network and no secrets.
- **The backend** validates the output and recalculates key figures independently.

## Vultr services used

| Service | Role | Status |
|---|---|---|
| Vultr Cloud Compute | Hosts the app and the Docker sandbox | App deployed; sandbox in progress |

AI models run through [OpenRouter's](https://openrouter.ai) free tier, to keep the project free to run. The client works with any OpenAI-compatible API, including Vultr Serverless Inference, by changing settings.

## Run it locally

You need Python 3.11 and [uv](https://docs.astral.sh/uv/). Run each server in its own terminal, from this folder.

```bash
uv sync                                        # install dependencies
cp .env.example .env                           # create your private settings file
uv run uvicorn backend.main:app --reload       # terminal 1: backend
uv run streamlit run frontend/app.py           # terminal 2: frontend
uv run pytest                                  # run the tests
uv run python scripts/check_inference.py       # test the real AI connection (needs AI_API_KEY in .env)
```

- Frontend: http://localhost:8501
- API documentation: http://127.0.0.1:8000/docs

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Build status](docs/BUILD_STATUS.md)
- [Security](SECURITY.md)

## License

[MIT](LICENSE)
