"""Writes the public pages: README.md (job table), docs/index.html (web page), docs/jobs.json.

PRIVACY: only job data is written here (title, employer, link, deadline, fit band).
Never your CV, name, contact details, salary, or pack contents.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from .common import DATA, DOCS, ROOT, load_json, save_json, today

WEB_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My job agents</title>
<style>
body{font:15px/1.45 "Segoe UI",Arial,sans-serif;margin:0;background:#f4f6f8;color:#1c2733}
header{background:#1b4f72;color:#fff;padding:14px 18px}header h1{margin:0;font-size:20px}
#meta{font-size:13px;opacity:.9;margin-top:4px}
#warn{background:#fff4ce;border-left:4px solid #e0a800;margin:12px 18px;padding:8px 12px;display:none;font-size:14px}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin:12px 18px}
.bar input,.bar select{padding:6px 8px;border:1px solid #b8c4ce;border-radius:6px;font-size:14px}
table{border-collapse:collapse;width:calc(100% - 36px);margin:0 18px 30px;background:#fff}
th,td{border-bottom:1px solid #e3e8ed;padding:7px 9px;text-align:left;vertical-align:top;font-size:14px}
th{background:#e8eef3;position:sticky;top:0}
.new{background:#fff;border-radius:3px;padding:1px 5px;font-size:11px;background:#d9f2e0;color:#14653a}
.late{color:#b00020;font-weight:600}.dim{opacity:.55}
</style></head><body>
<header><h1>My job agents</h1><div id="meta">loading...</div></header>
<div id="warn"></div>
<div class="bar">
<input id="q" placeholder="Search title, employer, place">
<select id="band"><option value="">All fits</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option><option value="pending">Not screened yet</option></select>
<label><input type="checkbox" id="hideApplied"> hide applied</label>
</div>
<table id="t"><thead><tr><th>Applied</th><th>Job</th><th>Employer / place</th><th>Deadline</th><th>Left</th><th>Fit</th></tr></thead><tbody></tbody></table>
<script>
(function(){
var host=location.hostname,parts=location.pathname.split('/').filter(Boolean);
var raw=(host.endsWith('github.io')&&parts[0])?'https://raw.githubusercontent.com/'+host.split('.')[0]+'/'+parts[0]+'/main/docs/jobs.json':'';
var applied=JSON.parse(localStorage.getItem('applied')||'{}'),data=null;
function load(url){return fetch(url,{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json();});}
(raw?load(raw).catch(function(){return load('jobs.json');}):load('jobs.json')).then(function(d){data=d;draw();}).catch(function(){document.getElementById('meta').textContent='Could not load jobs.json';});
function daysLeft(s){if(!s)return null;return Math.round((new Date(s+'T23:59:59')-new Date())/86400000);}
function draw(){
 var m=document.getElementById('meta');m.textContent=data.jobs.length+' open jobs - '+data.footer+' - updated '+data.updated;
 var w=document.getElementById('warn');if(data.warnings.length){w.style.display='block';w.textContent='';data.warnings.forEach(function(x){var p=document.createElement('div');p.textContent='\u26a0\ufe0f '+x;w.appendChild(p);});}
 var q=document.getElementById('q').value.toLowerCase(),b=document.getElementById('band').value,h=document.getElementById('hideApplied').checked;
 var tb=document.querySelector('#t tbody');tb.textContent='';
 data.jobs.forEach(function(j){
  var hay=(j.title+' '+j.employer+' '+j.location).toLowerCase();
  if(q&&hay.indexOf(q)<0)return;if(b&&j.band!==b)return;if(h&&applied[j.id])return;
  var tr=document.createElement('tr');if(applied[j.id])tr.className='dim';
  var c=function(){var td=document.createElement('td');tr.appendChild(td);return td;};
  var cb=document.createElement('input');cb.type='checkbox';cb.checked=!!applied[j.id];
  cb.onchange=function(){if(cb.checked)applied[j.id]=1;else delete applied[j.id];localStorage.setItem('applied',JSON.stringify(applied));draw();};
  c().appendChild(cb);
  var td=c();if(/^https:\/\//.test(j.url)){var a=document.createElement('a');a.href=j.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=j.title;td.appendChild(a);}else td.textContent=j.title;
  if(j.is_new){var s=document.createElement('span');s.className='new';s.textContent=' NEW';td.appendChild(s);}
  c().textContent=(j.employer||'')+(j.location?' - '+j.location:'');
  c().textContent=j.deadline||'check';
  var dl=daysLeft(j.deadline),t=c();if(dl!==null){t.textContent=dl+' d';if(dl<=7)t.className='late';}
  c().textContent=j.fit_label;
  tb.appendChild(tr);});
}
['q','band','hideApplied'].forEach(function(id){document.getElementById(id).addEventListener('input',function(){if(data)draw();});});
})();
</script></body></html>
"""


def band(job: dict) -> tuple[str, str, int]:
    f = job.get("fit")
    if f:
        s = f["score"]
        if s >= 75:
            return "high", f"\U0001F7E2 High {s}", 3
        if s >= 60:
            return "medium", f"\U0001F7E1 Medium {s}", 2
        return "low", f"\u26AA Low {s}", 1
    return "pending", "\u23F3 not screened yet", 0


def _md(s: str, n: int = 90) -> str:
    s = re.sub(r"[|\r\n\t]+", " ", s or "")
    s = s.replace("[", "(").replace("]", ")").replace("<", "").replace(">", "").strip()
    return s[:n]


def _left(deadline: str) -> tuple[str, int]:
    try:
        n = (dt.date.fromisoformat(deadline) - today()).days
    except ValueError:
        return "", 9999
    return (f"\U0001F534 {n} d" if n <= 7 else f"{n} d"), n


def render(ctx) -> None:
    jobs = [j for j in ctx.state["jobs"].values() if j.get("stage") not in ("expired", "restricted")]
    rows = []
    for j in jobs:
        b, label, rank = band(j)
        _, n = _left(j.get("deadline", ""))
        rows.append((j, b, label, rank, n))
    rows.sort(key=lambda r: (-r[3], -(r[0].get("fit") or {}).get("score", r[0].get("prescore", 0)), r[4]))
    counts = {k: sum(1 for r in rows if r[1] == k) for k in ("high", "medium", "low", "pending")}
    srcs = [v for v in ctx.state["sources"].values() if v.get("status") in ("ok", "failed")]
    ok = sum(1 for v in srcs if v.get("ok"))
    footer = (f"\U0001F7E2 {counts['high']} \u00B7 \U0001F7E1 {counts['medium']} \u00B7 \u26AA {counts['low']} \u00B7 "
              f"\u23F3 {counts['pending']} | AI today: {ctx.budget.summary()} | sources working: {ok} of {len(srcs)}")
    new_cut = (today() - dt.timedelta(days=2)).isoformat()
    public = [{"id": j["id"], "title": j["title"], "employer": j.get("employer", ""), "location": j.get("location", ""),
               "url": j["url"], "deadline": j.get("deadline", ""), "band": b, "fit_label": label,
               "is_new": j.get("found", "") >= new_cut} for j, b, label, _, _ in rows]
    save_if_changed(DOCS / "jobs.json", {"jobs": public, "footer": footer, "updated": today().isoformat(),
                                         "warnings": ctx.warnings})
    (DOCS / "index.html").write_text(WEB_PAGE, encoding="utf-8")

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    page = ""
    if "/" in repo:
        o, r = repo.split("/", 1)
        page = f"https://{o.lower()}.github.io/{r}/"
    L = ["# My job agents", ""]
    for w in ctx.warnings:
        L.append(f"\u26A0\uFE0F {_md(w, 300)}  ")
    L += ["", f"**{len(rows)} open jobs** ({footer}) \u00B7 updated {today().isoformat()}", ""]
    if page:
        L += [f"\U0001F310 [Open the web page]({page})", ""]
    L += ["Sorted by fit, then deadline \u00B7 \U0001F534 = 7 days or less left \u00B7 always confirm the deadline on the official posting", "",
          "| Job | Employer \u00B7 place | Deadline | Left | Fit |", "|---|---|---|---|---|"]
    for j, b, label, _, n in rows[:80]:
        left = _left(j.get("deadline", ""))[0]
        new = "\U0001F195 " if j.get("found", "") >= new_cut else ""
        place = " \u00B7 ".join(x for x in (_md(j.get("employer", ""), 40), _md(j.get("location", ""), 30)) if x)
        L.append(f"| {new}[{_md(j['title'])}]({j['url']}) | {place} | {j.get('deadline') or 'check'} | {left} | {label} |")
    L += ["", "---", "**Your own job link or text:** Actions \u2192 *Job agents* \u2192 *Run workflow*. "
          "**Setup and fixes:** see SETUP.md."]
    p = ROOT / "README.md"
    txt = "\n".join(L) + "\n"
    if not p.exists() or p.read_text(encoding="utf-8") != txt:
        p.write_text(txt, encoding="utf-8")


def save_if_changed(path, obj) -> None:
    if load_json(path, None) != obj:
        save_json(path, obj)
