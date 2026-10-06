"""Reads the full job posting for a job (used by agents 2-6)."""
from __future__ import annotations

import re

from .common import html_to_text, http, jsonld_jobposting, find_deadline, HttpError


class NeedText(Exception):
    """The page is a login / JavaScript wall: the text must be supplied by hand."""


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.8)
    return text[:head].rstrip() + "\n[...]\n" + text[-(limit - head):].lstrip()


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_jd(ctx, job: dict) -> str:
    jid = job["id"]
    if jid in ctx.manual_text:
        text = ctx.manual_text[jid]
    elif jid in ctx.desc_cache:
        text = ctx.desc_cache[jid]
    else:
        try:
            r = http(job["url"], timeout=30)
        except HttpError as e:
            raise NeedText(f"could not open the job link ({e})")
        page = r.text
        jp = jsonld_jobposting(page)
        text = ""
        if jp and len(html_to_text(str(jp.get("description", "")))) > 300:
            org = (jp.get("hiringOrganization") or {})
            org = org.get("name", "") if isinstance(org, dict) else str(org)
            loc = jp.get("jobLocation")
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
            place = ", ".join(str(addr.get(k, "")) for k in ("addressLocality", "addressCountry") if addr.get(k)) \
                if isinstance(addr, dict) else ""
            head = f"{jp.get('title', '')}\nEmployer: {org}\nLocation: {place}\n" \
                   f"Closing date: {str(jp.get('validThrough', ''))[:10]}\n\n"
            text = head + html_to_text(str(jp.get("description", "")))
        else:
            text = html_to_text(page)
            m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            if m and text:
                text = html_to_text(m.group(1)) + "\n\n" + text
    text = _tidy(text)
    if len(text) < 500 or (len(text) < 1500 and re.search(
            r"enable javascript|sign in to|log in to|please log in|access denied|verify you are human", text[:1200], re.I)):
        raise NeedText("the page has no readable job text (login or JavaScript page)")
    ctx.desc_cache[jid] = text
    d = find_deadline(text)
    if d and not job.get("deadline"):
        job["deadline"] = d
    return _cut(text, ctx.s["jd_max_chars"])
