"""Shared helpers for all six agents.

Only the Python standard library is used, plus `cryptography` (for the encrypted
e-mail backup).  Nothing in this file contains personal data.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import ipaddress
import json
import os
import re
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(os.environ.get("AGENTS_ROOT") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"
CONFIG = ROOT / "config"
OUTBOX = ROOT / "outbox"
DOCS = ROOT / "docs"

UA = "Mozilla/5.0 (compatible; JobAgents/1.0; +https://github.com)"

# --------------------------------------------------------------------------- logging

_SECRET_ENV = ["MAIL_PASS", "BACKUP_KEY", "GITHUB_TOKEN", "AI_API_KEY",
               "BRAVE_API_KEY", "RELIEFWEB_APPNAME"]


def redact(text: str) -> str:
    """Remove secret values from anything that is printed."""
    for k in _SECRET_ENV:
        v = os.environ.get(k, "")
        if len(v) >= 6:
            text = text.replace(v, "***")
    return text


def log(msg) -> None:
    print(redact(str(msg)), flush=True)


# --------------------------------------------------------------------------- time

def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def today() -> dt.date:
    override = os.environ.get("AGENTS_TODAY")
    if override:
        return dt.date.fromisoformat(override)
    return utcnow().date()


# --------------------------------------------------------------------------- files

def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARNING: could not read {path.name}: {e}")
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return (s or "job")[:n]


def clean_header(text: str, n: int = 180) -> str:
    return re.sub(r"[\r\n\t]+", " ", text or "").strip()[:n]


# --------------------------------------------------------------------------- HTTP

class HttpError(Exception):
    def __init__(self, status: int, message: str = "", headers=None, body: str = ""):
        super().__init__(f"HTTP {status} {message}".strip())
        self.status = status
        self.headers = headers or {}
        self.body = body


class Resp:
    def __init__(self, status, headers, text):
        self.status, self.headers, self.text = status, headers, text


def check_public(url: str) -> None:
    """Refuse non-https and private / metadata addresses (SSRF safety)."""
    p = urllib.parse.urlparse(url)
    if os.environ.get("ALLOW_LOCAL") == "1" and p.scheme in ("http", "https"):
        return
    if p.scheme != "https" or not p.hostname:
        raise HttpError(0, "only https links are allowed")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise HttpError(0, f"DNS failure: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise HttpError(0, "address not allowed")


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirect)


def http(url: str, method: str = "GET", headers: dict | None = None,
         body: bytes | None = None, timeout: int = 25, max_bytes: int = 3_000_000) -> Resp:
    check_public(url)
    h = {"User-Agent": UA, "Accept-Language": "en"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with _opener.open(req, timeout=timeout) as r:
            raw = r.read(max_bytes + 1)[:max_bytes]
            charset = r.headers.get_content_charset() or "utf-8"
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            return Resp(r.status, hdrs, raw.decode(charset, "replace"))
    except urllib.error.HTTPError as e:
        try:
            txt = e.read(20000).decode("utf-8", "replace")
        except Exception:
            txt = ""
        raise HttpError(e.code, str(e.reason), {k.lower(): v for k, v in e.headers.items()}, txt)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
        raise HttpError(0, f"network error: {e}")


# --------------------------------------------------------------------------- HTML -> text

class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag == "li":
                self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    p = _TextParser()
    try:
        p.feed(s or "")
        p.close()
    except Exception:
        pass
    text = "".join(p.out)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        elif blank == 0:
            out.append("")
            blank = 1
    return "\n".join(out).strip()


def jsonld_jobposting(page: str) -> dict | None:
    """Return the schema.org JobPosting block of a page, if there is one."""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return x
                if "@graph" in x:
                    stack.append(x["@graph"])
    return None


def canonical_url(url: str) -> str:
    p = urllib.parse.urlparse((url or "").strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref", "source")]
    return urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/",
                                    "", urllib.parse.urlencode(q), ""))


def job_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def find_deadline(text: str) -> str:
    """Best-effort closing date -> 'YYYY-MM-DD' (empty string if none found)."""
    if not text:
        return ""
    ctx = r"(?:closing date|closes?|deadline|apply by|application deadline|valid through|closing)[^0-9A-Za-z]{0,6}"
    pats = [
        ctx + r"(?:\w+day,?\s+)?(\d{1,2})[ \-]([A-Za-z]{3,9})[ ,\-]*(\d{4})",
        ctx + r"([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})",
        ctx + r"(\d{4})-(\d{2})-(\d{2})",
    ]
    for i, pat in enumerate(pats):
        m = re.search(pat, text, re.I)
        if not m:
            continue
        try:
            if i == 0:
                d, mon, y = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
            elif i == 1:
                mon, d, y = _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
            else:
                y, mon, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return dt.date(y, mon, d).isoformat()
        except (KeyError, ValueError):
            continue
    return ""


# --------------------------------------------------------------------------- JSON from the AI

def extract_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in answer")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = t[start:i + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
    raise ValueError("JSON object not closed (answer cut off)")


# --------------------------------------------------------------------------- AI budget + client

class AIStop(Exception):
    """AI must not be used any more in this run (limit, pause, access)."""


class AIError(Exception):
    """One AI request failed; other work may continue."""


class Budget:
    """Hard AI limits.  A request is counted BEFORE it is sent."""

    def __init__(self, daily: int, hourly: int, per_run: int, min_gap: float = 6.0):
        self.path = DATA / "ledger.json"
        self.d = load_json(self.path, {})
        self.daily, self.hourly, self.per_run, self.min_gap = daily, hourly, per_run, min_gap
        self.run_calls = 0
        self.last_call = 0.0
        self._roll()

    def _hour_key(self) -> str:
        return utcnow().strftime("%Y-%m-%dT%H")

    def _roll(self) -> None:
        if self.d.get("day") != today().isoformat():
            self.d["day"], self.d["day_calls"] = today().isoformat(), 0
        h = self._hour_key()
        hours = self.d.get("hours") or {}
        self.d["hours"] = {h: hours.get(h, 0)}

    def paused_until(self):
        v = self.d.get("paused_until")
        if not v:
            return None
        try:
            return dt.datetime.fromisoformat(v)
        except ValueError:
            return None

    def check(self) -> None:
        self._roll()
        pu = self.paused_until()
        if pu and utcnow() < pu:
            raise AIStop(f"AI paused until {pu:%Y-%m-%d %H:%M} UTC ({self.d.get('pause_reason', '')})")
        if self.d["day_calls"] >= self.daily:
            raise AIStop(f"daily AI limit reached ({self.daily})")
        if self.d["hours"][self._hour_key()] >= self.hourly:
            raise AIStop(f"hourly AI limit reached ({self.hourly})")
        if self.run_calls >= self.per_run:
            raise AIStop(f"per-run AI limit reached ({self.per_run})")

    def reserve(self) -> None:
        self._roll()
        self.d["day_calls"] += 1
        self.d["hours"][self._hour_key()] += 1
        self.run_calls += 1
        save_json(self.path, self.d)

    def pause(self, seconds: int, reason: str) -> None:
        self.d["paused_until"] = (utcnow() + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
        self.d["pause_reason"] = reason
        save_json(self.path, self.d)

    def ok(self) -> None:
        if self.d.get("errors"):
            self.d["errors"] = 0
            save_json(self.path, self.d)

    def failed(self) -> None:
        self.d["errors"] = self.d.get("errors", 0) + 1
        if self.d["errors"] >= 3:
            self.d["errors"] = 0
            self.pause(6 * 3600, "3 AI errors in a row")
        else:
            save_json(self.path, self.d)

    def summary(self) -> str:
        self._roll()
        return f"{self.d['day_calls']} of {self.daily} requests today"


def ai_settings() -> dict:
    return {
        "url": (os.environ.get("AI_BASE_URL") or "https://models.github.ai/inference").rstrip("/") + "/chat/completions",
        "model": os.environ.get("AI_MODEL") or "openai/gpt-4o-mini",
        "token": os.environ.get("AI_API_KEY") or os.environ.get("GITHUB_TOKEN") or "",
    }


def ai_chat(budget: Budget, system: str, user: str, *, max_tokens: int = 1800,
            want_json: bool = False, temperature: float = 0.2) -> str:
    cfg = ai_settings()
    if not cfg["token"]:
        raise AIStop("no AI token available")
    body = {"model": cfg["model"], "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json",
               "Accept": "application/json"}
    retried = False
    while True:
        budget.check()
        wait = budget.min_gap - (time.monotonic() - budget.last_call)
        if budget.last_call and wait > 0:
            time.sleep(wait)
        budget.reserve()
        try:
            r = http(cfg["url"], "POST", headers, json.dumps(body).encode("utf-8"), timeout=150)
            budget.last_call = time.monotonic()
        except HttpError as e:
            budget.last_call = time.monotonic()
            if e.status == 429:
                try:
                    ra = int(e.headers.get("retry-after", "0"))
                except ValueError:
                    ra = 0
                budget.pause(min(max(ra, 900), 86400), "rate limit (HTTP 429)")
                raise AIStop("AI rate limit reached (HTTP 429)")
            if e.status in (401, 403):
                budget.pause(6 * 3600, "access denied")
                raise AIStop(f"AI access denied (HTTP {e.status}) - check workflow permission 'models: read'")
            if e.status == 400 and not retried:
                low = e.body.lower()
                if "max_completion_tokens" in low and "max_tokens" in body:
                    body["max_completion_tokens"] = body.pop("max_tokens")
                elif "response_format" in low and "response_format" in body:
                    body.pop("response_format")
                elif "temperature" in low and "temperature" in body:
                    body.pop("temperature")
                else:
                    budget.failed()
                    raise AIError(f"AI request rejected: {e.body[:200]}")
                retried = True
                continue
            if (e.status >= 500 or e.status == 0) and not retried:
                retried = True
                time.sleep(8)
                continue
            budget.failed()
            raise AIError(f"AI request failed: HTTP {e.status} {e.body[:160]}")
        try:
            j = json.loads(r.text)
            choice = j["choices"][0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError):
            budget.failed()
            raise AIError("AI answer had an unexpected format")
        if not content.strip():
            budget.failed()
            raise AIError("AI answer was empty")
        budget.ok()
        if finish == "length":
            if want_json:
                raise AIError("AI answer was cut off (too long)")
            content += "\n[TRUNCATED - answer was cut off]"
        return content


# --------------------------------------------------------------------------- e-mail

def mail_cfg() -> dict:
    user = os.environ.get("MAIL_USER", "").strip()
    pw = re.sub(r"\s+", "", os.environ.get("MAIL_PASS", ""))
    return {"user": user, "pw": pw, "to": (os.environ.get("MAIL_TO") or user).strip(),
            "host": os.environ.get("SMTP_HOST") or "smtp.gmail.com",
            "port": int(os.environ.get("SMTP_PORT") or 465)}


def mail_configured() -> bool:
    c = mail_cfg()
    return bool(c["user"] and c["pw"] and c["to"])


def _smtp(c):
    return smtplib.SMTP_SSL(c["host"], c["port"], context=ssl.create_default_context(), timeout=40)


def smtp_check() -> tuple[bool, str]:
    if not mail_configured():
        return False, "MAIL_USER / MAIL_PASS are not set"
    c = mail_cfg()
    try:
        with _smtp(c) as s:
            s.login(c["user"], c["pw"])
        return True, "ok"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail rejected the login - MAIL_PASS must be a 16-character Gmail APP password"
    except Exception as e:  # noqa: BLE001
        return False, f"e-mail connection failed: {type(e).__name__}"


def send_mail(subject: str, body: str, attachments: list | None = None) -> None:
    c = mail_cfg()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = c["user"], c["to"], clean_header(subject, 200)
    msg.set_content(body)
    for name, data, mime in attachments or []:
        mt, st = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=mt, subtype=st, filename=name)
    with _smtp(c) as s:
        s.login(c["user"], c["pw"])
        s.send_message(msg)


# --------------------------------------------------------------------------- encryption (backup)

def _fernet(password: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=300_000)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


def encrypt(password: str, data: bytes) -> bytes:
    salt = os.urandom(16)
    return b"JA1" + salt + _fernet(password, salt).encrypt(data)


def decrypt(password: str, blob: bytes) -> bytes:
    if blob[:3] != b"JA1":
        raise ValueError("not a Job Agents backup file")
    return _fernet(password, blob[3:19]).decrypt(blob[19:])


# --------------------------------------------------------------------------- minimal .docx writer

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _esc(s: str) -> str:
    s = _BAD_XML.sub("", s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_docx(text: str) -> bytes:
    """Plain, ATS-safe Word file: no tables, no text boxes, no images.

    Markup: '# ' = title, '## ' = section heading, '- ' = bullet, blank line = gap.
    """
    paras = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        size, bold, ind, prefix, after = 21, False, 0, "", 60
        if line.startswith("# "):
            line, size, bold, after = line[2:], 30, True, 80
        elif line.startswith("## "):
            line, size, bold, after = line[3:].upper(), 23, True, 60
        elif re.match(r"^\s*[-*\u2022]\s+", line):
            line, prefix, ind = re.sub(r"^\s*[-*\u2022]\s+", "", line), "\u2022\t", 360
        rpr = f'<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr>'
        ppr = f'<w:pPr><w:spacing w:before="{120 if bold else 0}" w:after="{after}"/>' + \
              (f'<w:ind w:left="{ind}" w:hanging="260"/>' if ind else "") + "</w:pPr>"
        content = f'<w:r>{rpr}<w:t xml:space="preserve">{_esc(prefix + line)}</w:t></w:r>' if (line or prefix) else ""
        paras.append(f"<w:p>{ppr}{content}</w:p>")
    body = "".join(paras) + ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                              '<w:pgMar w:top="1020" w:right="1020" w:bottom="1020" w:left="1020" '
                              'w:header="500" w:footer="500" w:gutter="0"/></w:sectPr>')
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>{body}</w:body></w:document>'
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# --------------------------------------------------------------------------- fact check (no AI)

_CERTS = ["PMP", "PRINCE2", "LEED", "BREEAM", "CIOB", "RICS", "NEBOSH", "IOSH", "PMI", "ISO 9001", "ISO 14001",
          "ISO 45001", "FIDIC", "MBA", "PhD", "Ph.D", "MSc", "M.Sc", "MEng", "CEng", "PE license", "Six Sigma",
          "CAPM", "AutoCAD", "Revit", "BIM", "SAP", "Primavera", "MS Project"]


def _numbers(t: str) -> set[str]:
    out = set()
    for m in re.finditer(r"\d[\d.,]*\d|\d", t):
        out.add(m.group(0).replace(",", "").rstrip("."))
    return out


def fact_check(generated: str, *sources: str) -> list[str]:
    """Flag things in generated text that are not in the CV / posting."""
    src = "\n".join(sources)
    low_src = src.lower()
    warns = []
    nums = sorted(n for n in (_numbers(generated) - _numbers(src))
                  if not (len(n) == 4 and n.startswith(("19", "20"))) and n not in {"1", "2", "3", "4", "5", "0"})
    if nums:
        warns.append("Numbers not found in your CV or the posting (check them): " + ", ".join(nums[:12]))
    for c in _CERTS:
        if re.search(r"(?<![A-Za-z])" + re.escape(c) + r"(?![A-Za-z])", generated) and c.lower() not in low_src:
            warns.append(f"'{c}' appears but is not in your CV - remove it unless you really hold it")
    urls = set(re.findall(r"https?://[^\s)>\]]+", generated))
    bad = [u for u in urls if u.lower().rstrip("/.,") not in low_src]
    if bad:
        warns.append("Links not found in your CV or the posting: " + ", ".join(sorted(bad)[:4]))
    adds = len(re.findall(r"\[ADD", generated))
    if adds:
        warns.append(f"{adds} [ADD ...] placeholder(s) to fill in with real facts before sending")
    if "[TRUNCATED" in generated:
        warns.append("The AI answer was cut off - rerun this job")
    return warns


def keyword_coverage(keywords: list[str], text: str) -> tuple[int, list[str]]:
    """Share of keywords found (case-insensitive, whole-ish words) in text."""
    kws = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    if not kws:
        return 0, []
    low = text.lower()
    missing = [k for k in kws if k.lower() not in low]
    return round(100 * (len(kws) - len(missing)) / len(kws)), missing
