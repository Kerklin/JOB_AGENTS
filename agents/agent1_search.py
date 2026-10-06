"""AGENT 1 - JOB SCOUT.

Finds vacancies that match the search profile.  No AI is used here.
Reads: config/sources.json, config/profile.json, watchlist.txt, manual workflow input.
Writes: new jobs into the shared state (stage = "new", or "restricted" when the
posting is clearly closed to you, e.g. "nationals of Sudan only").
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .common import (ROOT, HttpError, canonical_url, find_deadline, html_to_text, http, job_id, log,
                     today, utcnow)

_HARD_INTERNAL = re.compile(
    r"internal (?:candidates|applicants) only|open to internal candidates only|staff members? only|"
    r"for (?:un|unicef|undp|unops|wfp|iom|unhcr)? ?staff only|current (?:un )?staff only", re.I)
_HARD_NATIONAL = [
    re.compile(r"nationals? of ([A-Za-z' -]{3,30}?) only", re.I),
    re.compile(r"(?:open to|restricted to|only for|limited to) (?:nationals|citizens) of ([A-Za-z' -]{3,30}?)(?:[.,;\n)]| only|$)", re.I),
    re.compile(r"locally recruited in ([A-Za-z' -]{3,30}?)(?:[.,;\n)]|$)", re.I),
]
_SOFT = re.compile(r"\bnationals?\b|\bcitizens?\b|work permit|visa|security clearance", re.I)


def link_ok(u: str) -> bool:
    return u.startswith("https://") or (os.environ.get("ALLOW_LOCAL") == "1" and u.startswith("http://"))


def eligibility(profile: dict, text: str) -> tuple[bool, list[str]]:
    """(restricted, flags). 'restricted' only for explicit closed-to-you wording."""
    home = [h.lower() for h in profile.get("home_terms", [])]
    flags: list[str] = []
    restricted = False
    if _HARD_INTERNAL.search(text or ""):
        restricted = True
        flags.append("Internal candidates only")
    for rx in _HARD_NATIONAL:
        for m in rx.finditer(text or ""):
            who = m.group(1).strip()
            if not any(h in who.lower() for h in home):
                restricted = True
                flags.append(f"Restricted to {who}")
    if not restricted and _SOFT.search(text or ""):
        flags.append("Check nationality / permit rules")
    return restricted, sorted(set(flags))[:4]


def score(profile: dict, title: str, text: str) -> int:
    t, body = (title or "").lower(), (text or "").lower()
    total = 0
    for kw, w in profile.get("keywords", {}).items():
        k = kw.lower()
        if k in t:
            total += 2 * w
        elif k in body:
            total += w
    for neg in profile.get("negative", []):
        if neg.lower() in t:
            total -= 8
    if any(p in body or p in t for p in profile.get("good_places", [])):
        total += 2
    return total


# ----------------------------------------------------------------------------- sources

def _xml_items(text: str) -> list[dict]:
    if "<!DOCTYPE" in text[:2000].upper() or "<!ENTITY" in text.upper():
        raise ValueError("feed rejected (XML entities are not allowed)")
    root = ET.fromstring(text)
    items = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d = {}
        for ch in el:
            n = ch.tag.split("}")[-1]
            if n == "link":
                d["link"] = ch.get("href") or (ch.text or "").strip()
            elif n in ("title", "description", "summary", "content", "pubDate", "updated", "author", "creator"):
                d[n] = (ch.text or "").strip()
        if d.get("link") and d.get("title"):
            items.append(d)
    return items


def fetch_rss(src, ctx):
    r = http(src["url"])
    out = []
    for it in _xml_items(r.text):
        summ = html_to_text(it.get("description") or it.get("summary") or it.get("content") or "")
        out.append({"title": it["title"], "url": it["link"], "summary": summ,
                    "employer": it.get("author") or it.get("creator") or "", "location": ""})
    return out


def fetch_arbeitnow(src, ctx):
    r = http(src["url"])
    data = json.loads(r.text).get("data", [])
    out = []
    for j in data:
        out.append({"title": j.get("title", ""), "url": j.get("url", ""), "employer": j.get("company_name", ""),
                    "location": j.get("location", ""), "summary": html_to_text(j.get("description", ""))})
    return out


def fetch_html_links(src, ctx):
    r = http(src["url"])
    rx = re.compile(src.get("href_regex") or r".")
    out, seen = [], set()
    for m in re.finditer(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        href = urllib.parse.urljoin(src["url"], m.group(1))
        title = html_to_text(m.group(2)).replace("\n", " ").strip()
        if not rx.search(href) or len(title) < 8 or href in seen:
            continue
        seen.add(href)
        out.append({"title": title[:200], "url": href, "summary": "", "employer": "", "location": ""})
    return out


def fetch_reliefweb(src, ctx):
    app = os.environ.get("RELIEFWEB_APPNAME", "").strip()
    if not app:
        raise LookupError("no RELIEFWEB_APPNAME secret")
    base = src.get("url") or "https://api.reliefweb.int/v2/jobs"
    body = {"limit": 60, "sort": ["date.created:desc"],
            "query": {"value": src.get("query", "construction OR engineer"), "operator": "OR", "fields": ["title", "body"]},
            "fields": {"include": ["title", "url_alias", "url", "source.name", "country.name", "date.closing", "body"]}}
    r = http(base + "?appname=" + urllib.parse.quote(app), "POST",
             {"Content-Type": "application/json"}, json.dumps(body).encode())
    out = []
    for it in json.loads(r.text).get("data", []):
        f = it.get("fields", {})
        url = f.get("url_alias") or f.get("url") or ""
        closing = (f.get("date", {}) or {}).get("closing", "")[:10]
        out.append({"title": f.get("title", ""), "url": url,
                    "employer": ", ".join(s.get("name", "") for s in f.get("source", [])),
                    "location": ", ".join(c.get("name", "") for c in f.get("country", [])),
                    "summary": (f.get("body") or "")[:6000], "deadline": closing})
    return out


def fetch_brave(src, ctx):
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        raise LookupError("no BRAVE_API_KEY secret")
    out = []
    for q in src.get("queries", [])[:6]:
        url = "https://api.search.brave.com/res/v1/web/search?count=10&freshness=pm&q=" + urllib.parse.quote(q)
        r = http(url, headers={"X-Subscription-Token": key, "Accept": "application/json"})
        for it in json.loads(r.text).get("web", {}).get("results", []):
            out.append({"title": it.get("title", ""), "url": it.get("url", ""), "employer": "",
                        "location": "", "summary": html_to_text(it.get("description", ""))})
    return out


FETCHERS = {"rss": fetch_rss, "arbeitnow": fetch_arbeitnow, "html_links": fetch_html_links,
            "reliefweb": fetch_reliefweb, "brave": fetch_brave}
DEFAULT_INTERVAL = {"brave": 1440}


# ----------------------------------------------------------------------------- state updates

def _dup_keys(jobs: dict) -> set:
    return {(j.get("title", "").lower().strip(), j.get("employer", "").lower().strip()) for j in jobs.values()}


def add_candidate(ctx, c: dict, source: str, force: bool = False) -> str | None:
    url = (c.get("url") or "").strip()
    title = re.sub(r"\s+", " ", c.get("title") or "").strip()
    if not url.startswith("http") or not title:
        return None
    jid = job_id(url)
    jobs = ctx.state["jobs"]
    summary = c.get("summary") or ""
    deadline = c.get("deadline") or find_deadline(summary) or find_deadline(title)
    if jid in jobs:
        j = jobs[jid]
        j["last_seen"] = today().isoformat()
        if deadline and not j.get("deadline"):
            j["deadline"] = deadline
        if force and j.get("stage") in ("expired", "skipped", "restricted", "needs_text"):
            j["stage"] = "new"
        return jid
    if deadline and deadline < today().isoformat() and not force:
        return None
    dkey = (title.lower(), (c.get("employer") or "").lower().strip())
    if not force and dkey in _dup_keys(jobs):
        return None
    ps = score(ctx.profile, title, summary)
    if ps < ctx.s["min_prescore_store"] and not force:
        return None
    restricted, flags = eligibility(ctx.profile, title + "\n" + summary)
    jobs[jid] = {
        "id": jid, "title": title[:200], "employer": (c.get("employer") or "")[:120],
        "location": (c.get("location") or "")[:120], "url": canonical_url(url), "source": source,
        "deadline": deadline, "found": today().isoformat(), "last_seen": today().isoformat(),
        "prescore": ps, "flags": flags, "stage": "restricted" if (restricted and not force) else "new",
        "fit": None, "pack": None, "tries": {"screen": 0, "pack": 0}, "note": "",
    }
    if len(summary) >= 400:
        ctx.desc_cache[jid] = summary
    return jid


def _due(state_src: dict, interval_min: int) -> bool:
    last = state_src.get("last_try")
    if not last:
        return True
    try:
        t = dt.datetime.fromisoformat(last)
    except ValueError:
        return True
    return utcnow() - t >= dt.timedelta(minutes=interval_min - 1)


def run(ctx) -> None:
    log("Agent 1 (Scout): searching")
    new_total = 0
    srcs_state = ctx.state["sources"]
    for src in ctx.sources:
        name, typ = src.get("name", "source"), src.get("type")
        if not src.get("enabled", True) or typ not in FETCHERS:
            continue
        st = srcs_state.setdefault(name, {})
        if not _due(st, int(src.get("interval_minutes", DEFAULT_INTERVAL.get(typ, 60)))):
            continue
        st["last_try"] = utcnow().isoformat(timespec="seconds")
        try:
            cands = FETCHERS[typ](src, ctx)
            before = len(ctx.state["jobs"])
            for c in cands:
                add_candidate(ctx, c, name)
            added = len(ctx.state["jobs"]) - before
            new_total += added
            if not cands:
                st.update(ok=False, status="failed", found=0, added=0, error="0 listings - the page layout may have changed")
                log(f"  {name}: FAILED (0 listings)")
                continue
            st.update(ok=True, status="ok", found=len(cands), added=added, last_ok=st["last_try"], error="")
            log(f"  {name}: {len(cands)} listings, {added} new")
        except LookupError as e:      # optional source without its key
            st.update(ok=None, status="skipped", error=str(e))
        except Exception as e:        # noqa: BLE001 - a bad source must never stop the run
            msg = f"{type(e).__name__}: {str(e)[:100]}"
            st.update(ok=False, status="failed", error=msg)
            log(f"  {name}: FAILED ({msg})")
    # links pasted into watchlist.txt
    wl = ROOT / "watchlist.txt"
    if wl.exists():
        for line in wl.read_text(encoding="utf-8", errors="replace").splitlines():
            u = line.strip()
            if link_ok(u) and job_id(u) not in ctx.state["jobs"]:
                jid = add_candidate(ctx, {"title": "Job from watchlist (title read later)", "url": u}, "watchlist", force=True)
                if jid:
                    ctx.state["jobs"][jid]["note"] = "From watchlist.txt"
                    new_total += 1
    # a link or text given when starting the workflow by hand
    url = (os.environ.get("INPUT_JOB_URL") or "").strip()
    text = (os.environ.get("INPUT_JOB_TEXT") or "").strip()
    if link_ok(url):
        title = (text.splitlines()[0][:120] if text else "Job from manual run (title read later)")
        jid = add_candidate(ctx, {"title": title, "url": url, "summary": text}, "manual", force=True)
        if jid:
            ctx.force_ids.append(jid)
            if text:
                ctx.manual_text[jid] = text
            ctx.state["jobs"][jid]["note"] = "Manual run"
            new_total += 1
    elif url:
        ctx.warn("The job link you typed was ignored: it must start with https://")
    ctx.notes.append(f"Scout: {new_total} new job(s)")
    _maintain(ctx)


def _maintain(ctx) -> None:
    jobs = ctx.state["jobs"]
    t = today()
    keep = dt.timedelta(days=ctx.s["keep_days_after_deadline"])
    for jid in list(jobs):
        j = jobs[jid]
        d = j.get("deadline")
        if d:
            try:
                dd = dt.date.fromisoformat(d)
            except ValueError:
                j["deadline"] = ""
                continue
            if dd < t:
                j["stage"] = "expired" if j.get("stage") != "pack_done" else j["stage"]
                if t - dd > keep:
                    del jobs[jid]
        elif j.get("last_seen") and (t - dt.date.fromisoformat(j["last_seen"])).days > 45:
            del jobs[jid]
    if len(jobs) > ctx.s["max_jobs_kept"]:
        order = sorted(jobs.values(), key=lambda j: (j["stage"] in ("new", "screened"), j.get("prescore", 0)))
        for j in order[: len(jobs) - ctx.s["max_jobs_kept"]]:
            jobs.pop(j["id"], None)
