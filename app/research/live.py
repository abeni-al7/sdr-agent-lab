"""Live company research from free, keyless public sources.

- website:  the company's own pages (home, then /about), only where robots.txt allows us
- news:     Google News RSS search for the company name, last 90 days
- jobs:     public job-board APIs (Greenhouse, Lever, Ashby); no key, built for this use
- profile:  Wikidata (industry, employee count, headquarters), matched on the official website

Everything here is untrusted outside data. Callers must scan web text before a model sees it.
Network safety: only public http(s) hosts on ports 80/443, every redirect re-checked, small timeouts
and size caps, so a user-supplied domain cannot make the server fetch internal addresses.
"""
import html
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "SDRAgentLab/1.0 (research bot; +https://github.com/abeni-al7/sdr-agent-lab)"
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ResearchError(Exception):
    """A source could not be used (blocked, unsafe, not found or down)."""


# ---------------------------------------------------------------- safe fetching

def normalize_domain(value: str) -> str:
    d = (value or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split("?")[0].split("#")[0]
    d = d.split("@")[-1].split(":")[0].removeprefix("www.").rstrip(".")
    if not _DOMAIN.match(d):
        raise ResearchError(f"'{value}' is not a valid public domain name")
    return d


def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ResearchError(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ResearchError(f"{host} resolves to a non-public address; refusing to fetch it")


def safe_get(url: str, *, accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
             max_bytes: int = MAX_BYTES) -> httpx.Response:
    """GET a public URL. Each redirect hop is validated; the body is capped."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en", "Accept-Encoding": "gzip, deflate"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.hostname or p.port not in (None, 80, 443) or p.username:
                raise ResearchError(f"unsafe URL: {url}")
            _public_host(p.hostname)
            with client.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        break
                hdrs = {k: v for k, v in r.headers.items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
                return httpx.Response(r.status_code, headers=hdrs, content=body[:max_bytes], request=r.request)
    raise ResearchError("too many redirects")


_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


def allowed_by_robots(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    cached = _robots_cache.get(base)
    if not cached or time.time() - cached[0] > 3600:
        rp = RobotFileParser()
        try:
            r = safe_get(base + "/robots.txt", accept="text/plain", max_bytes=300_000)
            if r.status_code in (401, 403):
                rp.parse(["User-agent: *", "Disallow: /"])
            elif r.status_code >= 400:
                rp.parse([])
            else:
                rp.parse(r.text.splitlines())
        except (ResearchError, httpx.HTTPError):
            rp.parse([])  # robots.txt unreachable: treat as no rules, the page fetch will tell
        cached = (time.time(), rp)
        _robots_cache[base] = cached
    return cached[1].can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------- website

_DROP = re.compile(r"<(script|style|noscript|svg|template|iframe)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_BLOCK = re.compile(r"</?(p|div|section|article|header|footer|li|ul|ol|h[1-6]|br|tr|table|main|nav)\b[^>]*>", re.I)
_TAG = re.compile(r"<(?!!--)[^>]+>")
_META = re.compile(r"<meta\s+[^>]*(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content=[\"']([^\"']*)[\"']", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)


def _keep_comment(m: re.Match) -> str:
    """Build markers and code in comments are dropped. Prose comments are kept for the injection scan."""
    inner = m.group(1)
    prose = re.findall(r"[A-Za-z]{2,}", inner)
    return m.group(0) if len(prose) >= 5 and not re.search(r"[<>{}]|=\s*[\"']", inner) else " "


def html_to_text(raw: str) -> str:
    """Readable text. HTML comments are kept on purpose so the injection scan can see and remove them."""
    title = _TITLE.search(raw)
    metas = [m for tag in _META.findall(raw) for m in _CONTENT.findall(tag)]
    body = _COMMENT.sub(_keep_comment, _DROP.sub(" ", raw))
    body = _BLOCK.sub("\n", body)
    body = _TAG.sub(" ", body)
    body = html.unescape(body)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in body.split("\n")]
    lines = [ln for ln in lines if len(ln) > 2]
    head = []
    if title:
        head.append(html.unescape(re.sub(r"\s+", " ", title.group(1))).strip())
    head += [html.unescape(m).strip() for m in metas[:1]]
    seen, out = set(), []
    for ln in head + lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln if ln.endswith((".", "!", "?", ":")) else ln + ".")
    return "\n".join(out)


def fetch_website(domain: str, max_chars: int = 5000) -> dict:
    """Home page plus the first about page that exists. Returns {"text", "urls", "skipped"}."""
    domain = normalize_domain(domain)
    texts, urls, skipped = [], [], []
    for path in ("/", "/about", "/about-us", "/company"):
        if len(urls) >= 2 or sum(len(t) for t in texts) > max_chars:
            break
        url = f"https://{domain}{path}"
        if not allowed_by_robots(url):
            skipped.append(f"{url} (robots.txt)")
            continue
        try:
            r = safe_get(url)
        except (ResearchError, httpx.HTTPError) as exc:
            skipped.append(f"{url} ({exc.__class__.__name__})")
            if path == "/":
                try:  # some sites only answer on www
                    r = safe_get(f"https://www.{domain}/")
                except (ResearchError, httpx.HTTPError):
                    continue
            else:
                continue
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
            skipped.append(f"{url} (HTTP {r.status_code})")
            continue
        final = str(r.request.url) if r.request else url
        if path != "/" and final.rstrip("/") in [u.rstrip("/") for u in urls]:
            continue  # /about redirected to the home page
        texts.append(html_to_text(r.text))
        urls.append(final)
    if not texts:
        raise ResearchError(f"could not read {domain}: " + "; ".join(skipped[:3]))
    return {"text": "\n".join(texts)[:max_chars], "urls": urls, "skipped": skipped}


# ---------------------------------------------------------------- news

def search_news(company: str, domain: str, topic: str = "", days: int = 90, limit: int = 5) -> list[dict]:
    """Recent headlines that name the company, from Google News RSS (free, no key).
    `topic` (for example "CRM" or "payments") separates the company from other uses of its name."""
    topic = re.sub(r"[^\w\s-]", "", topic or "").strip()[:40]
    q = f'"{company}" {topic} when:{days}d'.replace("  ", " ")
    url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = safe_get(url, accept="application/rss+xml,application/xml", max_bytes=800_000)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (ResearchError, httpx.HTTPError, ET.ParseError):
        return []
    # Case-sensitive for "Close" (not "close"); a brand typed in lowercase ("lemlist") matches any case.
    flags = 0 if any(ch.isupper() for ch in company) else re.I
    named = re.compile(rf"(?<![\w-]){re.escape(company)}(?![\w-])|{re.escape(domain)}", flags)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        if not named.search(title):
            continue
        try:
            date = parsedate_to_datetime(item.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            date = ""
        out.append({"title": html.unescape(title), "source": source, "date": date, "url": item.findtext("link") or ""})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- jobs

SALES_WORDS = ("sales", "sdr", "bdr", "account executive", "business development", "revenue", "growth", "partnership")


def _slugs(company: str, domain: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]", "", company.lower())
    stem = domain.split(".")[0].lower()
    dashed = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    return list(dict.fromkeys(s for s in (stem, base, dashed) if s))


def search_jobs(company: str, domain: str) -> dict | None:
    """Open roles from the company's public job board, if it uses Greenhouse, Lever or Ashby."""
    for slug in _slugs(company, domain):
        boards = (
            ("Greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
             lambda d: [j.get("title", "") for j in d.get("jobs", [])]),
            ("Lever", f"https://api.lever.co/v0/postings/{slug}?mode=json",
             lambda d: [j.get("text", "") for j in d] if isinstance(d, list) else []),
            ("Ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
             lambda d: [j.get("title", "") for j in d.get("jobs", [])]),
        )
        for board, url, titles_of in boards:
            try:
                r = safe_get(url, accept="application/json", max_bytes=3_000_000)
                if r.status_code != 200:
                    continue
                titles = [t for t in titles_of(r.json()) if t]
            except (ResearchError, httpx.HTTPError, ValueError):
                continue
            if not titles:
                continue
            sales = [t for t in titles if any(w in t.lower() for w in SALES_WORDS)]
            return {"board": board, "total": len(titles), "sales_roles": sales[:5], "sales_count": len(sales),
                    "date": time.strftime("%Y-%m-%d")}
    return None


# ---------------------------------------------------------------- company profile

def _wd(params: dict) -> dict:
    r = safe_get("https://www.wikidata.org/w/api.php?" + "&".join(f"{k}={quote(str(v))}" for k, v in params.items()),
                 accept="application/json", max_bytes=2_000_000)
    r.raise_for_status()
    return r.json()


def _claim_values(entity: dict, prop: str) -> list:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append((v, c))
    return out


def company_profile(company: str, domain: str) -> dict | None:
    """Industry, employees and HQ from Wikidata, only when the entity's official website is this domain."""
    try:
        hits = _wd({"action": "wbsearchentities", "search": company, "language": "en", "type": "item",
                    "limit": 5, "format": "json"}).get("search", [])
        if not hits:
            return None
        ents = _wd({"action": "wbgetentities", "ids": "|".join(h["id"] for h in hits),
                    "props": "claims|labels", "languages": "en", "format": "json"}).get("entities", {})
    except (ResearchError, httpx.HTTPError, ValueError):
        return None
    for qid in (h["id"] for h in hits):
        e = ents.get(qid, {})
        sites = [v for v, _ in _claim_values(e, "P856") if isinstance(v, str)]
        if not any(normalize_domain_safe(s) == domain for s in sites):
            continue
        employees = None
        emp = _claim_values(e, "P1128")
        if emp:
            def when(item):
                q = item[1].get("qualifiers", {}).get("P585", [{}])[0].get("datavalue", {}).get("value", {})
                return q.get("time", "")
            v, _ = max(emp, key=when)
            try:
                employees = int(float(str(v.get("amount", "0")).lstrip("+")))
            except ValueError:
                employees = None
        ids = [v.get("id") for p in ("P452", "P159") for v, _ in _claim_values(e, p) if isinstance(v, dict)]
        labels = {}
        if ids:
            try:
                got = _wd({"action": "wbgetentities", "ids": "|".join(ids[:20]), "props": "labels",
                           "languages": "en", "format": "json"}).get("entities", {})
                labels = {k: v.get("labels", {}).get("en", {}).get("value") for k, v in got.items()}
            except (ResearchError, httpx.HTTPError, ValueError):
                pass
        industries = [labels.get(v.get("id")) for v, _ in _claim_values(e, "P452") if labels.get(v.get("id"))]
        hq = next((labels.get(v.get("id")) for v, _ in _claim_values(e, "P159") if labels.get(v.get("id"))), None)
        return {"wikidata_id": qid, "industry": ", ".join(industries[:3]) or None, "employees": employees, "hq": hq}
    return None


def normalize_domain_safe(value: str) -> str | None:
    try:
        return normalize_domain(value)
    except ResearchError:
        return None
