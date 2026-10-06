# JOB_AGENTS – six agents that work together

One public GitHub repository, one workflow, six agents. Everything personal stays in **Secrets** (private) or in **your e-mail**. Nothing personal is ever written into the repository.

| # | Agent | What it does | AI? |
|---|---|---|---|
| 1 | **Scout** | Searches job sources every 15 min, removes closed / nationals-only / internal-only posts | no |
| 2 | **Fit** | Reads the full posting + your CV: requirement-by-requirement check, eligibility risks, HR prescreening questions, ATS keywords. The ATS % is **counted by code**, not guessed | yes |
| 3 | **CV** | Writes a tailored, ATS-safe CV (plain text + Word) using only facts from your CV | yes |
| 4 | **Letter** | Writes a tailored cover letter (260–330 words, Word) | yes |
| 5 | **Salary** | Salary written in the posting wins; otherwise a labelled estimate (low / middle / high / what to ask) | yes |
| 6 | **Mail** | Drafts the e-mail to the employer and sends the **whole pack to your own inbox**. Nothing is ever sent to an employer | yes |

Pipeline: `1 → 2 → (fit ≥ 60) → 3 → 4 → 5 → 6`. Jobs below 60 stop after agent 2, so no AI is wasted on them.

## What is public and what is private

| Public (in the repository) | Private |
|---|---|
| Job title, employer, link, deadline, fit band | Your CV (secret `MASTER_CV`) |
| Search settings (`config/`) | Tailored CV, letter, salary, HR answers, e-mail draft (sent only to **your** inbox) |
| Encrypted backups in `outbox/` (unreadable without `BACKUP_KEY`) | Gmail app password, `BACKUP_KEY` |

---

## Setup (about 25 minutes, once)

### Step 1 – Prepare your CV (5 min)
- Plain text, **at most 11,000 characters** (the free AI accepts about 8,000 tokens per request). Nothing is silently cut: if it is too long, the front page tells you the exact number.
- Start with 4 lines the agents need for eligibility checks:
  ```
  Full Name
  Nationality: ...   Based in: ...   Languages: ...
  E-mail: ...   Phone: ...   LinkedIn: ...
  ```
- Then your summary, experience (Month YYYY – Month YYYY), education, certifications.

### Step 2 – Create the repository (2 min)
1. github.com → **New repository** → name **JOB_AGENTS** → **Public** → leave "Add a README" **unticked** → **Create**.

### Step 3 – Upload the files (5 min)
1. Unzip `JOB_AGENTS.zip` on your computer.
2. In the new repository click **uploading an existing file**.
3. Open the unzipped folder and select **everything inside it** (including the folders `.github`, `agents`, `config`, `data`, `docs`, `outbox`, `scripts`, `tests`, `tools`) and **drag it into the browser**. Wait until every file is listed.
4. **Commit changes**.
5. Check: open `.github/workflows/agents.yml` – it must exist. (If `.github` did not upload: **Add file → Create new file**, type `.github/workflows/agents.yml`, paste the content, commit.)

### Step 4 – Add the secrets (8 min)
**Settings → Secrets and variables → Actions → Secrets tab → New repository secret.** Names must match exactly (capital letters, no spaces). Use *Repository secrets* (not Environment or Dependabot).

| Secret | Value | Required? |
|---|---|---|
| `MASTER_CV` | Your whole CV as plain text (Step 1) | **yes** |
| `MAIL_USER` | Your Gmail address | **yes** (for delivery) |
| `MAIL_PASS` | A Gmail **app password** (16 characters, spaces are fine). Google Account → Security → turn on 2-Step Verification → **App passwords** | **yes** |
| `BACKUP_KEY` | 30+ random characters, e.g. from PowerShell: `-join ((48..57)+(65..90)+(97..122) \| Get-Random -Count 40 \| % {[char]$_})` | recommended |
| `MAIL_TO` | Another address to receive packs (default = `MAIL_USER`) | optional |
| `RELIEFWEB_APPNAME` | Only if ReliefWeb approved an app name for you | optional |
| `BRAVE_API_KEY` | Adds web search + salary sources. Brave charges per query after free credits | optional |

**About `BACKUP_KEY`:** if Gmail is down, the finished pack is encrypted with this key and kept in `outbox/`; the next run e-mails it and deletes it. **Set it once and never change it** – backups waiting in `outbox/` need the same key. To read a backup by hand: `pip install cryptography`, then `python tools/decrypt_backup.py outbox/<file>.enc`.

### Step 5 – Workflow permissions (1 min)
**Settings → Actions → General → Workflow permissions → Read and write permissions → Save.**

### Step 6 – Test (3 min)
1. **Actions** tab → **Job agents** → **Run workflow** → tick **Test only (dry_run)** → **Run workflow**.
2. After ~1 minute, open the repository front page (`README.md`): you should see a job table and *"sources working: x of y"*.
3. Run it again **without** dry_run. Within one or two runs the first pack arrives in your inbox with subject `[Job Agents] APPLY 82 – …`.

The schedule then runs every **15 minutes** on its own. To change it, edit the line `cron: "*/15 * * * *"` in `.github/workflows/agents.yml` (5 minutes is the shortest GitHub allows).

### Step 7 (optional) – Web page
**Settings → Pages → Deploy from a branch → `main` → `/docs` → Save.** The address appears on the Pages screen (`https://kerklin.github.io/JOB_AGENTS/`). The page reads the job list directly from the repository and keeps your **Applied** ticks in your browser only.

---

## Everyday use
- **Analyse one job now:** Actions → Job agents → Run workflow → paste the `https://` link. If the page needs a login, also paste the job text into *job_text*. A manual job gets its pack even if the fit is low.
- **Add many links:** edit `watchlist.txt` (one `https://` link per line, public links only).
- **Add / remove a source:** edit `config/sources.json`.
- **Tune the search:** edit `config/profile.json` (keywords and weights).
- **Emergency stop for AI:** Settings → Secrets and variables → Actions → **Variables** → New variable `AI_ENABLED` = `no`. Jobs are still collected. Delete it to resume.
- **Different AI:** variables `AI_MODEL`, `AI_BASE_URL` and secret `AI_API_KEY` switch to any OpenAI-compatible service (useful if you want a larger CV limit: raise `cv_max_chars` in `config/settings.json`).

## Safety limits built in
| Limit | Default | Where |
|---|---|---|
| AI requests per day / hour / run | 120 / 20 / 12 | `config/settings.json` |
| Packs per run | 1 (2 with a manual job) | `config/settings.json` |
| Request counted **before** it is sent | always | code |
| HTTP 429 (rate limit) | AI pauses ≥ 15 min, run continues without AI | code |
| 3 AI errors in a row | AI pauses 6 h | code |
| Access denied | AI pauses 6 h, front page says how to fix | code |
| Two runs at the same time | impossible (`concurrency`) | workflow |
| Results saved even if a step crashes | yes (`if: always()`) | workflow |
| Posting text treated as data, not instructions | yes | prompts |
| E-mail ever sent to an employer | **never** | code |
| Pack numbers / certificates / links not in your CV | flagged under "CHECK BEFORE SENDING" | code |

GitHub Models is free but meant for prototyping: about 150 requests a day for the small model, 8,000 tokens in / 4,000 out per request. This is why the defaults stay below 150.

## Fixing the messages on the front page

| Message | Fix |
|---|---|
| *MASTER_CV secret is missing* | Step 4 |
| *MASTER_CV is N characters; the limit is 11,000* | Shorten older roles in the secret (edit the secret, paste again) |
| *Application packs are paused: … MAIL_PASS …* | `MAIL_PASS` must be a 16-character **app password**, not your Gmail password. Update the secret |
| *E-mail is not working … ENCRYPTED backups* | Same fix; the stored packs are sent automatically afterwards |
| *Backup … cannot be opened – was BACKUP_KEY changed?* | Put the old key back (or delete the old `.enc` files in `outbox/`) |
| *AI access denied (HTTP 403)* | Confirm the line `models: read` in `agents.yml`, and that GitHub Models is available to your account |
| *AI rate limit reached (HTTP 429)* | Nothing to do. It resumes by itself; lower `ai_daily_cap` if it happens daily |
| *sources working: 3 of 6* | Open Actions → latest run → *Run the six agents* → see which source says FAILED. Broken sources never stop the run; switch one off with `"enabled": false` |
| *needs login / JavaScript page* | Run the workflow by hand with the link **and** the pasted job text |
| Workflow stops running after weeks | GitHub pauses schedules after 60 days without repository activity; the agents' own commits count, but press **Enable workflow** if it was paused |
| Run is red at *Save results* | Open the log; the script retries 5 times. Usually fixed by the next run |

## Test it yourself (optional)
`python tests/run_tests.py` runs 60+ checks against stand-in job board, AI and Gmail servers. No internet or secrets needed.

## Known limits (honest list)
- The AI can make mistakes. Read every line of the CV and letter against your real record before applying. The "CHECK BEFORE SENDING" list helps, but it cannot catch everything.
- Salary figures without a posted salary or web sources are **estimates** (confidence "low").
- Job sources change their pages. Agent 1 reports the ones that stop working; adding your own RSS feeds in `config/sources.json` is the most reliable way to widen the search.
