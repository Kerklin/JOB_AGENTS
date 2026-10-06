"""Shared run context: settings, state, budget, CV (from a secret), warnings."""
from __future__ import annotations

import os
import time

from .common import CONFIG, DATA, Budget, load_json, log

DEFAULTS = {
    "ai_daily_cap": 120, "ai_hourly_cap": 20, "ai_per_run_cap": 12, "ai_min_gap_seconds": 6,
    "max_screenings_per_run": 6, "max_packs_per_run": 1, "min_fit_for_pack": 60,
    "min_prescore_store": 4, "min_prescore_ai": 6, "cv_max_chars": 11000, "jd_max_chars": 6500,
    "keep_days_after_deadline": 14, "max_jobs_kept": 400, "run_time_budget_minutes": 18,
}


def _num(v, default):
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


class Ctx:
    def __init__(self):
        raw = load_json(CONFIG / "settings.json", {})
        self.s = {k: _num(raw.get(k, d), d) for k, d in DEFAULTS.items()}
        self.profile = load_json(CONFIG / "profile.json", {})
        self.sources = load_json(CONFIG / "sources.json", {}).get("sources", [])
        st = load_json(DATA / "jobs.json", {})
        self.state = {"jobs": st.get("jobs", {}), "sources": st.get("sources", {}),
                      "updated": st.get("updated", "")}
        for j in self.state["jobs"].values():       # tolerate older / hand-edited files
            j.setdefault("tries", {"screen": 0, "pack": 0})
            j["tries"].setdefault("screen", 0)
            j["tries"].setdefault("pack", 0)
            j.setdefault("stage", "new")
            j.setdefault("flags", [])
        self.budget = Budget(self.s["ai_daily_cap"], self.s["ai_hourly_cap"],
                             self.s["ai_per_run_cap"], self.s["ai_min_gap_seconds"])
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.desc_cache: dict[str, str] = {}
        self.manual_text: dict[str, str] = {}
        self.force_ids: list[str] = []
        self.analyses: dict[str, dict] = {}
        self.after_screen = None  # set by run.py: packs a job right after it is screened
        self.started = time.monotonic()
        flag = (os.environ.get("AI_ENABLED") or "yes").strip().lower()
        self.ai_enabled = flag not in ("no", "false", "0", "off", "stop")
        self.dry_run = (os.environ.get("DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
        cv = (os.environ.get("MASTER_CV") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        self.cv = cv
        self.cv_ok = False
        self.ai_stopped = ""
        if not self.ai_enabled:
            self.warn("AI is switched off (repository variable AI_ENABLED = no). Jobs are still collected.")
        elif self.dry_run:
            self.notes.append("Dry run: no AI, no e-mail.")
        elif not cv:
            self.warn("Agents 2-6 are waiting: the MASTER_CV secret is missing (see SETUP.md, step 4).")
        elif len(cv) > self.s["cv_max_chars"]:
            self.warn(f"MASTER_CV is {len(cv):,} characters; the limit is {self.s['cv_max_chars']:,}. "
                      "Shorten older roles (nothing is silently cut), then agents 2-6 start.")
        elif len(cv) < 800:
            self.warn("MASTER_CV looks too short (under 800 characters). Paste your full CV as plain text.")
        else:
            self.cv_ok = True

    # -- helpers
    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
            log("WARNING: " + msg)

    def minutes_left(self) -> float:
        return self.s["run_time_budget_minutes"] - (time.monotonic() - self.started) / 60

    def ai_available(self) -> bool:
        return self.ai_enabled and self.cv_ok and not self.dry_run and not self.ai_stopped

    def stop_ai(self, reason: str) -> None:
        if not self.ai_stopped:
            self.ai_stopped = reason
            self.notes.append("AI stopped for this run: " + reason)
            log("AI stopped for this run: " + reason)
