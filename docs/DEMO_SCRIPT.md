# DataPilot: pitch and demo script

All figures come from real runs on `sample_data/sales.csv`, a made-up dataset.

## One-line pitch

DataPilot is an AI data analyst that can't make up numbers: AI writes the code, a locked-down sandbox runs it, and the backend recalculates the results before you see them.

## Short description

Ask questions of a CSV in plain English. DataPilot plans the analysis, runs generated Python in a disposable Docker sandbox, independently verifies the results, and explains only what it verified.

## The 3-minute video

**Before recording:** open the live site and do one practice run. The page shows how many free analyses are left today. A run takes about a minute, and uses about 3 of the 50 free daily AI requests.

| Time | Show | Say |
|---|---|---|
| 0:00 | Landing page | "AI assistants can give you a wrong number with total confidence. And running AI-written code on your systems is risky. DataPilot solves both." |
| 0:20 | Click **Try the sample sales dataset** | "I upload a CSV. It's validated and profiled automatically: 1,010 rows, 30 missing values, 13 duplicate rows." |
| 0:40 | Click **Use the sample question**, then **Analyze** | "I ask in plain English for revenue, top products, monthly trends, missing values, and recommendations. No code needed." |
| 1:00 | The step-by-step tracker | "The AI plans the work and writes Python, but it never supplies the numbers. The code runs in a brand-new container with no network and no secrets." |
| 1:30 | Key figures and verification labels | "Total revenue is 674,142.65. The green check means our backend recalculated it with separate code, and you can see the exact formula." |
| 1:50 | **Execution log** tab | "If this run shows a repair, this is the system working: recalculation caught a wrong number, and the AI fixed its own code." |
| 2:05 | **Insights** and **Charts and tables** tabs | "Interactive charts, findings kept separate from recommendations, and every number traced to a result. Patterns are never presented as proof of cause." |
| 2:20 | **Containment Lab** tab, click **Run the attacks** | "Now let's attack it. Deleting files, an infinite loop, a fork bomb, a memory bomb, stealing secrets, sending data out." |
| 2:45 | Attack result cards | "All contained. The code does its worst, and the blast radius is one throwaway container." |
| 2:55 | **Download the full report** | "And you can download everything, including the code. Your data analyst, your code, verifiable results." |

The first attempt doesn't always need a repair, so only mention the repair if the log shows one. Don't quote any figure that isn't on screen.

## Questions judges may ask

- **"Can the AI still get it wrong?"** Yes. Verification shows that a number equals its stated formula, not that the formula was the right choice. That's why we show the formula and the code.
- **"Is Docker enough security?"** Not on its own, because containers share the kernel. We list the remaining risks in `SECURITY.md`.
- **"Why not Vultr Serverless Inference?"** The live demo uses a free model because of budget. The client also supports Vultr by changing settings, though that path has only been tested with simulated replies.
- **"What's the business value?"** Teams without analysts get answers they can check, and companies can use AI-written code without trusting it.

## Submission checklist

- [ ] Choose the challenge. Challenge 1 requires Vultr Serverless Inference, so enter Challenge 2 or get written approval from the organizers.
- [ ] Replace the OpenRouter key.
- [ ] Make the GitHub repository public.
- [ ] Record and upload the video, including the containment moment.
- [ ] Create the slides and cover image.
- [ ] Include the demo URL in the submission. No access code is needed.
- [ ] Submit on lablab.ai by Nov 7, 12:00 am Pakistan time.
