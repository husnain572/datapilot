# DataPilot build status

**Last updated:** Nov 5, 2026

**The app is built, deployed, and tested at http://66.42.63.156.** The hackathon submission isn't done yet.

**Deadline: Nov 7, 2026, 12:00 am PKT. Target: submit by Nov 6, 9:00 pm PKT.**

## Where to continue

These need the owner:

1. Choose the challenge. Challenge 1 requires Vultr Serverless Inference, and the live app uses free OpenRouter models. Enter Challenge 2, or get written approval from the organizers.
2. Replace the OpenRouter key, which was pasted into a chat session. See [DEPLOYMENT.md](DEPLOYMENT.md#replacing-the-ai-key).
3. Make the GitHub repository public.
4. Record the video, make the slides and cover image, and submit on lablab.ai. See [DEMO_SCRIPT.md](DEMO_SCRIPT.md).
5. After judging, and before the Vultr credit expires, destroy the server.

## What's working

| Feature | How it was tested |
|---|---|
| CSV upload, validation, profiling, preview, delete | Automated tests, live browser |
| Question to plan and code, using a free AI model | Real AI, live site |
| Sandbox with no network, no secrets, and strict limits | 19 real attack tests on the server, 7 in the live Containment Lab |
| First attempt plus up to two repairs | Automated tests, and real repairs seen in live runs |
| Result validation and independent recalculation | Automated tests, live runs |
| Number check on the AI's written explanation | Automated tests |
| Dashboard: progress, plan, code, logs, charts, tables, insights, downloads | Automated tests, live browser |
| Follow-up questions | Automated tests only |
| Access code and daily AI request limit | Automated tests; a wrong code was refused live |
| Your own uploaded CSV through a full analysis | **Not tested live.** Only the sample dataset was |
| Vultr Serverless Inference as the AI provider | **Not tested with real calls.** Only simulated replies |

## Latest results (Nov 5, 2026)

```
Full test suite on the server, including real sandbox attacks:
293 passed, 1 warning

Demo question, real AI and real sandbox, production services:
Status: completed | AI requests: 3 | 62 seconds
Attempt 1: recalculation disagreed (reported 48 missing cells, recalculated 30)
Attempt 2: succeeded
Total revenue = 674142.65, Total missing cells = 30
Verified: total revenue, missing cells, top five products, monthly revenue
Independent check using only Python's csv module:
PASS total revenue, PASS missing values, PASS top five products, PASS every month

Real Chrome through http://66.42.63.156:
Upload, profile, access code, sample question, analysis (88 seconds), results: passed
2 interactive charts, 8 tables, 5 download buttons, 0 page exceptions, 0 JavaScript errors
Containment Lab: all 7 attacks contained, in 43 seconds

Server: only ports 22 and 80 are allowed. Ports 8000, 8100, and 8501 are closed from outside.
No leftover sandboxes. The executor user can't read .env, and its settings hold no AI key.
```

## Known limits

- **Free AI limit:** 50 requests a day from OpenRouter, and about 3 per analysis. The app stops at 40 a day. Free models can also be busy or rate-limited at any time, which shows as an error, not a wrong answer.
- **Speed:** an analysis took about 1 to 1.5 minutes in testing.
- **No HTTPS:** the site uses plain HTTP, so the access code and uploads aren't encrypted in transit.
- **Verified means recalculated:** it doesn't prove that the formula was the right one. Tables and figures without a simple check are labelled "Not independently checked".
- **AI assumptions can be wrong:** the number check covers numbers only, so assumptions may include ideas that aren't in the data.
- **Data handling:** pandas treats text like `NA` as missing, so a region code "NA" would count as missing. Datasets and analyses expire after 24 hours.
- **Security:** containers share the server's kernel, and gVisor isn't in use. See [SECURITY.md](../SECURITY.md).
- **Left out to keep things simple:** PDF reports (Markdown only), server-made PNG files (each chart has a browser download), and history beyond the current browser session.
- **Tests:** pytest shows a harmless library deprecation warning.
- `~/.git` exists in the home folder on the owner's Mac, so `git add` run from there could stage personal files.
