# DataPilot implementation plan

**Goal:** a working, deployed entry for the Vultr: Agent Rush Hackathon.

**Budget: $0.** The owner can't pay anything, so the app runs on the Vultr server (paid from the hackathon credit) and OpenRouter's free AI models.

**Challenge choice:** Challenge 1 ("Blast Radius Zero") requires Vultr Serverless Inference, which we no longer use. Challenge 2 ("Powering the Future of Work") makes it optional. The owner should enter Challenge 2 or get written approval from the organizers.

**Deadline:** **Nov 7, 2026, 12:00 am Pakistan time** (Nov 6, 19:00 UTC), confirmed by the owner. **Our target is to submit by Nov 6, 9:00 pm PKT (16:00 UTC)**, or earlier if we can, which leaves at least a 3-hour buffer.

The rule is **deploy early, then improve**. Times are PKT and are targets.

| When | Phase | Status |
|---|---|---|
| Nov 4 | 0 Docs, 1 Foundation, 2 Upload and profiling, early deployment | Done |
| Nov 4–5 | 3 AI client, 4 Analysis agent, 5 Code generation and sandbox, 6 Validation and explanation, 7 Dashboard and reports, 8 Testing and final deployment | Done |
| By Nov 6, 9:00 pm | **Delivery:** video, slides, cover image, public repo, submission | The owner's next step |

## Scope

**Must have:** upload, profile, agent, sandbox, verification, charts, Markdown report, Containment Lab, live deployment.

**Left out to keep the project simple and free:** PDF report (Markdown only), PNG files from the server (each chart has a browser download button), gVisor, saved session history beyond the current browser session.

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
| OpenRouter account and API key | Free; no payment details needed | Now |
| Domain name (optional) | Free option: an `sslip.io` address based on the server's IP | Phase 8a |

## Submission checklist (from the official hackathon pages)

- [ ] Registered on lablab.ai and joined Discord
- [ ] Public GitHub repo with setup instructions and docs
- [ ] MIT-compliant, original code
- [ ] README explains the Vultr services used
- [ ] Backend deployed on a Vultr VM
- [ ] Challenge chosen and its AI rule met (see Challenge choice above)
- [ ] Public demo URL
- [ ] Demo video showing one containment moment
- [ ] Slide presentation and cover image
- [ ] Clear explanation of the architecture and use case
- [ ] Title, short and long descriptions, and tags
- [ ] Entry submitted on lablab.ai, which is the only route to judging

## Open questions

- [ ] Will organizers accept a free model for a Challenge 1 entry, or should this enter Challenge 2? (The owner should ask in Discord.)
