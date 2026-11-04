# DataPilot implementation plan

**Goal:** a working, deployed entry for the Vultr: Agent Rush Hackathon, Challenge 1 ("Blast Radius Zero").

**Deadline:** the online build runs Nov 3–8, 2026. The exact closing time is **unconfirmed**, so check lablab or Discord. One unverified team note says Nov 8, 22:00 UTC. Our target is to submit by **Nov 8, 12:00 UTC**.

The rule is **deploy early, then improve**. Dates are targets.

| When | Phase | Finished when |
|---|---|---|
| Nov 4 | **0 Docs** | All Phase 0 documents exist and have been reviewed |
| Nov 4 | **1 Foundation:** uv project, FastAPI, Streamlit, Git and GitHub | App runs on the Mac, tests pass, first commit is on GitHub |
| Nov 4–5 | **8a Early deploy:** VM, SSH, firewall, Docker, HTTPS | Public URL shows the health page |
| Nov 5 | **2 Upload and profiling** | Bad files rejected in tests, sample CSV profiled correctly |
| Nov 5 | **3 Sandbox:** simple revenue container, then restrictions one at a time | Each attack test is contained, shown by real output |
| Nov 6 | **4 Vultr AI client:** errors, rate limits, model comparison | Real response parsed and validated |
| Nov 6 | **5 Agent workflow** | Demo question works, retries stay within the limit, numbers verified |
| Nov 7 | **6 Dashboard:** chat, plan, code, logs, charts, downloads | Full flow works in a browser |
| Nov 7 | **7 Testing:** malformed files, malicious code, timeouts, invalid outputs, unauthorized access | Real test results recorded |
| Nov 7–8 | **8b Production hardening** | Limits, restarts, and an update procedure in place |
| Nov 8 | **9 Delivery:** README, slides, cover image, video, submission | Final test passes on the live site and entry is submitted |

## Scope

**Must have:** upload, profile, agent, sandbox, verification, charts, Markdown report, Containment Lab, live deployment.

**First to cut if time runs short:** PDF report, gVisor, session-history polish, landing-page polish, processed-CSV export.

## API

- **Health:** `GET /health`, `GET /ready`
- **Datasets:** `POST /api/v1/datasets`, `GET /api/v1/datasets/{id}`, `GET /api/v1/datasets/{id}/preview`, `DELETE /api/v1/datasets/{id}`
- **Analyses:** `POST /api/v1/analyses`, `GET /api/v1/analyses/{id}`, `GET /api/v1/analyses/{id}/logs`
- **Files:** `GET /api/v1/artifacts/{id}`, `GET /api/v1/analyses/{id}/report`
- **Containment Lab:** `POST /api/v1/containment-tests`

## Accounts and services needed

| Item | Cost | When |
|---|---|---|
| lablab.ai registration and Discord | Free | Now |
| New Vultr account (email never used on Vultr) plus hackathon gift code | $200 free credit | Now |
| GitHub account | Free | Phase 1 |
| Vultr VM: 2 vCPU, 4 GB RAM, current Ubuntu LTS | Paid from credits; check the price in the console first | Phase 8a |
| Serverless Inference subscription and API key | Paid from credits; read the charges notice | Phase 4 |
| Domain name (optional) | Free option: an `sslip.io` address based on the server's IP | Phase 8a |

## Submission checklist (from the official hackathon pages)

- [ ] Registered on lablab.ai and joined Discord
- [ ] Public GitHub repo with setup instructions and docs
- [ ] MIT-compliant, original code
- [ ] README explains the Vultr services used
- [ ] Backend deployed on a Vultr VM, with LLM calls through Serverless Inference
- [ ] Public demo URL
- [ ] Demo video showing one containment moment
- [ ] Slide presentation and cover image
- [ ] Clear explanation of the architecture and use case
- [ ] Title, short and long descriptions, and tags
- [ ] Entry submitted on lablab.ai, which is the only route to judging

## Open questions

- [ ] What is the exact submission deadline and time zone?
- [ ] What are the VM and inference subscription prices? (Check in the console.)
- [ ] Does gVisor work on our VM? (Test in Phase 3.)
- [ ] Which model gives the best results? (Compare in Phase 4.)
- [ ] What are the inference rate limits? (Not found in the docs; observe in Phase 4.)
