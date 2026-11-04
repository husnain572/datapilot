# DataPilot implementation plan

**Goal:** a working, deployed entry for the Vultr: Agent Rush Hackathon, Challenge 1 ("Blast Radius Zero").

**Deadline:** **Nov 7, 2026, 12:00 am Pakistan time** (Nov 6, 19:00 UTC), confirmed by the owner. **Our target is to submit by Nov 6, 9:00 pm PKT (16:00 UTC)**, or earlier if we can, which leaves at least a 3-hour buffer.

The rule is **deploy early, then improve**. Times are PKT and are targets.

| When | Phase | Finished when |
|---|---|---|
| Done | **0 Docs, 1 Foundation, 2 Upload and profiling, early deployment** | Live at http://66.42.63.156 |
| Nov 4 | **3 Vultr AI client** | Tests pass and a real call succeeds with the owner's key |
| Nov 5, morning | **4 Analysis agent:** question to validated plan | Plans use only real columns, tested with real model output |
| Nov 5, afternoon | **5 Code generation and Docker sandbox** | Each attack test is contained on the VM, shown by real output |
| Nov 5, evening | **6 Validation and explanation** | Sample question gives verified numbers |
| Nov 6, by 12:00 pm | **7 Dashboard and reports** | Full flow works in a browser on the public URL |
| Nov 6, by 3:00 pm | **8 Testing and final deployment** | Real test results recorded, live site verified |
| Nov 6, by 9:00 pm | **Delivery:** README, slides, cover image, video, submission | Entry submitted on lablab.ai |

**Feature freeze: Nov 6, 12:00 pm PKT.** After that, we only fix bugs and prepare the submission.

## Scope

**Must have:** upload, profile, agent, sandbox, verification, charts, Markdown report, Containment Lab, live deployment.

**Cut unless we're ahead of schedule:** PDF report, gVisor, session history, landing-page polish, processed-CSV export, comparing models beyond finding one that works.

**Vultr credit:** $200 applied, and it expires 30 days after signup.

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

- [ ] What are the VM and inference subscription prices? (Check in the console.)
- [ ] Does gVisor work on our VM? (Test in Phase 3.)
- [ ] Which model gives the best results? (Compare in Phase 4.)
- [ ] What are the inference rate limits? (Not found in the docs; observe in Phase 4.)
