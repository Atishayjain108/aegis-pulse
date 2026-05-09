# How to Use AEGIS Pulse

A plain-English guide for non-technical users.

---

## What is AEGIS Pulse?

AEGIS Pulse is your personal market intelligence assistant. Every day it
automatically collects hundreds of posts and trends from Reddit, Hacker News,
GitHub, and Amazon, then uses AI to figure out which ones represent a real
opportunity worth your attention. Instead of spending hours reading feeds, you
run one command and get a clear verdict: should you act on this trend, wait and
watch, or ignore it entirely?

---

## The ONE command to use every day

Open a terminal, make sure Docker is running (`aegis up` if it is not already),
then type:

```
aegis daily
```

That is it. AEGIS Pulse will:

1. Collect signals from 4 sources (Reddit, Hacker News, GitHub Trending, Amazon Bestsellers)
2. Store them in the database automatically
3. Run the AI analysis pipeline across all 10 intelligence agents
4. Print a clear, colour-coded verdict

To watch a different Reddit community:

```
aegis daily --subreddit Entrepreneur
```

To collect fewer signals (faster, less thorough):

```
aegis daily --limit 100
```

---

## How to read the verdict

After the analysis completes you will see a box like this:

```
=========================================================
  VERDICT   ── PROCEED
  SCORE     ── 0.74 / 1.0   (Confidence: 81%)
  PRIORITY  ── Act within 24 hours
=========================================================
```

Here is what each verdict means in plain English:

| Verdict | What it means | What to do |
|---------|--------------|-----------|
| **PROCEED** | Strong signal. The AI is confident this trend is real and accelerating. | Research it now and consider taking action soon. |
| **HOLD** | Interesting but not convincing yet. | Check again tomorrow. The pattern may strengthen. |
| **BLOCK** | Weak, noisy, or suspicious signal. | Ignore it for now. Not worth your time. |
| **ESCALATE** | Urgent — multiple agents flagged this as critical. | Review immediately; something unusual is happening. |

The **Score** (0.0 to 1.0) reflects how strongly the evidence points toward the
verdict. Above 0.65 is strong; below 0.40 is weak. The **Confidence** percentage
tells you how consistent the 10 AI agents were with each other — 80% and above
means they largely agreed.

---

## How to improve accuracy with a free Groq API key

By default AEGIS Pulse uses a heuristic (rule-based) engine, which is fast but
has no language understanding. Adding a free Groq API key enables a real language
model for deeper reasoning. You will notice richer explanations in the agent
breakdown table.

**Steps (takes about 3 minutes):**

1. Go to [https://console.groq.com](https://console.groq.com) and create a free account.
2. Click **API Keys** in the left sidebar, then **Create API Key**.
3. Copy the key (it starts with `gsk_`).
4. Open the file called `.env` in the AEGIS Pulse project folder.
5. Add this line at the bottom (replace the placeholder with your real key):
   ```
   GROQ_API_KEY=gsk_your_key_here
   ```
6. Save the file and run `aegis daily` again.

That is all — no restart needed. AEGIS Pulse will automatically use Groq for LLM
reasoning on your next run.

---

## What to do when something shows PROCEED

A PROCEED verdict means the AI found a strong, fast-moving signal. Here is a
suggested next-steps checklist:

1. **Read the agent breakdown** printed below the verdict. Look at which agents
   voted PROCEED and what question they were answering (e.g. "How fast is this
   trend growing?" or "Are there regional pricing gaps?").

2. **Open the source yourself.** Visit Reddit, Hacker News, or whichever platform
   scored highest, and read 10–20 recent posts on the topic with fresh eyes.

3. **Search for related products or services.** Use Google, Amazon, or Etsy to see
   if supply is thin relative to the demand the AI detected.

4. **Check the score and confidence.** A score above 0.75 with confidence above 80%
   is a strong signal. A score of 0.55–0.65 is worth monitoring but not acting on
   immediately.

5. **Run again tomorrow.** Trends that persist across two or three consecutive daily
   runs are far more reliable than a single spike.

---

## Quick reference

| Task | Command |
|------|---------|
| Run the full daily workflow | `aegis daily` |
| Watch a different subreddit | `aegis daily --subreddit <name>` |
| Start the Docker stack | `aegis up` |
| Check if everything is healthy | `aegis status` |
| See the most recent signals | `aegis signals tail` |
| Scrape one source manually | `aegis scrape --source hacker-news --limit 30` |
