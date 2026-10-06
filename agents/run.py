"""Orchestrator: runs the six agents in order and saves the shared state.

    Agent 1 Scout   -> finds jobs
    Agent 2 Fit     -> fit + HR prescreening + ATS keywords
    Agent 3 CV      -> tailored CV
    Agent 4 Letter  -> tailored cover letter
    Agent 5 Salary  -> salary evaluation
    Agent 6 Mail    -> employer e-mail draft + private delivery
"""
from __future__ import annotations

import sys

from . import agent1_search, agent2_fit, agent3_cv, agent4_letter, agent5_salary, agent6_mail, site
from .common import (DATA, AIError, AIStop, fact_check, keyword_coverage, load_json, log, save_json,
                     today, utcnow)
from .context import Ctx


def make_pack(ctx: Ctx, job: dict, a: dict) -> bool:
    """Agents 3-6 for one screened job. Returns True when a pack was delivered or safely backed up."""
    forced = job["id"] in ctx.force_ids
    cap = ctx.s["max_packs_per_run"] + (1 if forced else 0)
    if getattr(ctx, "packs_done", 0) >= cap or not ctx.ai_available() or ctx.minutes_left() < 4:
        return False
    if agent6_mail.delivery_mode(ctx) == "none":
        return False
    log(f"Agents 3-6: building the pack for: {job['title'][:70]}")
    try:
        cv = agent3_cv.run(ctx, job, a)
        letter = agent4_letter.run(ctx, job, a)
    except AIStop as e:
        ctx.stop_ai(str(e))
        return False
    except AIError as e:
        job["tries"]["pack"] += 1
        log(f"  pack failed ({str(e)[:100]})")
        if job["tries"]["pack"] >= 3:
            job["note"] = "Pack failed 3 times - run it by hand"
            job["stage"] = "skipped"
        return False
    salary = None
    if ctx.ai_available():
        try:
            salary = agent5_salary.run(ctx, job, a)
        except AIStop as e:
            ctx.stop_ai(str(e))
        except (AIError, ValueError) as e:
            log(f"  salary agent could not finish ({str(e)[:80]})")
    email = agent6_mail.draft_email(ctx, job, a)
    warn = ([f"CV: {w}" for w in fact_check(cv, ctx.cv, a["_jd"])] +
            [f"Cover letter: {w}" for w in fact_check(letter, ctx.cv, a["_jd"])] +
            [f"E-mail: {w}" for w in fact_check(email["body"], ctx.cv, a["_jd"])])
    after, missing = keyword_coverage(a["ats_exact"], cv)
    pack = agent6_mail.build_pack(job, a, cv, letter, salary, email, warn, after, missing)
    result = agent6_mail.deliver(ctx, job, pack)
    if result == "failed":
        job["tries"]["pack"] += 1
        ctx.warn("A pack was written but could not be delivered (e-mail failed and no backup key). It was discarded.")
        return False
    job["pack"] = {"delivery": result, "at": today().isoformat(), "ats_after": after}
    job["stage"] = "pack_done"
    ctx.packs_done = getattr(ctx, "packs_done", 0) + 1
    ctx.notes.append(f"Pack {result}: {job['title'][:50]}")
    log(f"  pack {result}")
    return True


def pending_packs(ctx: Ctx) -> list[dict]:
    jobs = [j for j in ctx.state["jobs"].values()
            if j["stage"] == "screened" and j.get("fit") and j["tries"]["pack"] < 3]
    jobs.sort(key=lambda j: (-j["fit"]["score"], j.get("deadline") or "9999"))
    return jobs


def run_packs_for_screened(ctx: Ctx) -> None:
    for job in pending_packs(ctx):
        if not ctx.ai_available() or ctx.minutes_left() < 4 or getattr(ctx, "packs_done", 0) >= ctx.s["max_packs_per_run"]:
            break
        if agent6_mail.delivery_mode(ctx) == "none":
            break
        a = ctx.analyses.get(job["id"]) or agent2_fit.analyse(ctx, job)
        if a is None:
            continue
        if a["verdict"] == "SKIP" or a["fit_score"] < ctx.s["min_fit_for_pack"]:
            job["stage"] = "skipped"
            continue
        make_pack(ctx, job, a)


def main() -> int:
    ctx = Ctx()
    ctx.packs_done = 0
    try:
        agent1_search.run(ctx)
        if not ctx.dry_run:
            agent6_mail.flush_outbox(ctx)
        if ctx.ai_available():
            ctx.after_screen = lambda job, a: make_pack(ctx, job, a)
            run_packs_for_screened(ctx)
            agent2_fit.run(ctx)
    except Exception as e:  # noqa: BLE001 - save whatever was done, then report
        ctx.warn(f"The run stopped early: {type(e).__name__}: {str(e)[:120]}")
    finally:
        ctx.state["updated"] = today().isoformat()
        old = load_json(DATA / "jobs.json", {})
        if old != ctx.state:
            save_json(DATA / "jobs.json", ctx.state)
        site.render(ctx)
        log("Done. " + " | ".join(ctx.notes) + f" | AI: {ctx.budget.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
