#!/usr/bin/env python3
"""Build a static ICRA 2026 schedule explorer.

Sources:
- gisbi-kim/icra2026-explorer output/papers.json for accepted technical papers.
- ICRA 2026 official workshop/tutorial page for workshop metadata and links.
- Linked workshop pages, plus a small number of likely program/schedule/accepted-paper
  subpages, for workshop internal talk/poster/paper text.

The generated index.html embeds schedule data and references local static assets.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
TMP = Path("/tmp/icra2026_build")
TMP.mkdir(parents=True, exist_ok=True)

EXPLORER_REPO = "https://github.com/gisbi-kim/icra2026-explorer.git"
EXPLORER_DIR = TMP / "icra2026-explorer"
PAPERCEPT_BASE = "https://ras.papercept.net/conferences/conferences/ICRA26/program/"
WORKSHOPS_URL = "https://2026.ieee-icra.org/workshops-and-tutorials/"
RASEVENTS_URL = "https://rasevents.org/event?id=167&actionMenu=sessions"
PROGRAM_AT_A_GLANCE_URL = "https://2026.ieee-icra.org/program-at-a-glance/"
KEYNOTE_SESSIONS_URL = "https://2026.ieee-icra.org/program/keynote-sessions/"
PLENARY_SESSIONS_URL = "https://2026.ieee-icra.org/attend/plenary-sessions/"
KEYNOTE_TUTORIALS_URL = "https://2026.ieee-icra.org/attend/keynote-tutorials/"
PANEL_SESSIONS_URL = "https://2026.ieee-icra.org/program/panels/"
INDUSTRY_KEYNOTES_URL = "https://2026.ieee-icra.org/program/industry-keynotes/"
RAS_EVENTS_URL = "https://2026.ieee-icra.org/ras-events/"
FLOORPLAN_PAGE_URL = "https://2026.ieee-icra.org/partners/conference-floorplan/"
FLOORPLAN_IMAGE_PATH = "assets/icra2026-floorplan.png"
FLOORPLAN_PDF_PATH = "assets/icra2026-floorplan.pdf"
VISITOR_COUNTER_BADGE_URL = (
    "https://hitscounter.dev/api/hit?"
    "url=https%3A%2F%2Fminwoo0611.github.io%2Ficra2026-schedule%2F"
    "&label=Visitor&icon=github&color=%23126c73"
)

DAY_TO_PAGE = {
    "Sunday": "ICRA26_ContentListWeb_1.html",
    "Monday": "ICRA26_ContentListWeb_2.html",
    "Tuesday": "ICRA26_ContentListWeb_3.html",
    "Wednesday": "ICRA26_ContentListWeb_4.html",
    "Thursday": "ICRA26_ContentListWeb_5.html",
    "Friday": "ICRA26_ContentListWeb_6.html",
}

TIME_RE = re.compile(
    r"(?P<s>\b(?:[01]?\d|2[0-3])[:.][0-5]\d)\s*(?:-|–|—|to)\s*(?P<e>\b(?:[01]?\d|2[0-3])[:.][0-5]\d)",
    re.I,
)
SINGLE_TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b")
AMPM_TIME_RE = re.compile(r"\b(?P<h>1[0-2]|0?\d)(?:\s*:\s*(?P<m>[0-5]\d))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b", re.I)
AMPM_RANGE_RE = re.compile(
    r"\b(?P<sh>1[0-2]|0?\d)(?:\s*:\s*(?P<sm>[0-5]\d))?\s*"
    r"(?P<sampm>a\.?m\.?|p\.?m\.?)?\s*(?:-|–|—|to)\s*"
    r"(?P<eh>1[0-2]|0?\d)(?:\s*:\s*(?P<em>[0-5]\d))?\s*"
    r"(?P<eampm>a\.?m\.?|p\.?m\.?)\b",
    re.I,
)
TIME_ONLY_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
TIME_RANGE_ONLY_RE = re.compile(
    r"^(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:-|–|—|to)\s*(?:[01]?\d|2[0-3]):[0-5]\d$",
    re.I,
)
PDF_FILE_RE = re.compile(r"\b[\w.-]+\.pdf\b", re.I)
PDF_ONLY_RE = re.compile(r"^(?:drive,\s*)?[\w.-]+\.pdf$", re.I)
LINK_HINT_RE = re.compile(
    r"(program|programme|schedule|agenda|accepted|paper|poster|talk|speaker|session|proceedings|abstract)",
    re.I,
)
SUBPAGE_SKIP_RE = re.compile(
    r"(archive|dataset|/202[0-5]\b|202[0-5]\s|icra2[0-5]\b|ac2[0-5]\b|"
    r"abstract[-_\s]*submission|submission|call[-_\s]*for|call[-_\s]*poster)",
    re.I,
)
NOISE_RE = re.compile(
    r"(cookie|privacy|sponsor|organizer|organiser|contact|subscribe|registration|important dates|submission deadline|call for papers|call for posters)",
    re.I,
)
SUBMISSION_NOISE_RE = re.compile(
    r"(submitted papers|paper submission|notification of acceptance|camera-ready|deadline|cmt|travel grants?|eligibility|application:|double-blind|peer-review|under review|future submission|journal special issue|full papers? \(|extended abstracts?|page limit|accepted papers will be|will be invited)",
    re.I,
)
BIO_NOISE_RE = re.compile(
    r"^(he|she|they|we|our|this workshop|in this workshop)\b|professor|director|received (his|her|their)|ph\\.?d|biography",
    re.I,
)
CONTEXT_HEADING_RE = re.compile(
    r"(session|part|morning|afternoon|theme|track|method|learning|optimization|control|perception|planning|navigation|locali[sz]ation|mapping|robot|robotics|autonomy|break|poster|panel|talks?)",
    re.I,
)


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def fetch(url: str, *, timeout: int = 18) -> tuple[int | None, str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if "text" not in ctype and "html" not in ctype and "xml" not in ctype and not r.text.strip().startswith("<"):
            return r.status_code, r.url, ""
        r.encoding = r.encoding or "utf-8"
        return r.status_code, r.url, r.text
    except Exception as exc:  # noqa: BLE001 - crawler should continue
        return None, url, f"FETCH_ERROR: {exc}"


def normalize_text(s: str) -> str:
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def author_search_variants(author_text: str) -> str:
    variants = []
    for part in re.split(r";|\band\b", author_text or ""):
        name = re.sub(r"\([^)]*\)", "", part).strip(" ,")
        if not name:
            continue
        variants.append(name)
        if "," in name:
            last, first = [x.strip() for x in name.split(",", 1)]
            if first and last:
                variants.append(f"{first} {last}")
    return " ".join(variants)


def normalize_schedule_time_text(s: str) -> str:
    """Fix common Google Sites text extraction artifacts in times.

    Examples seen in workshop pages:
    - "1 0: 0 0" -> "10:00"
    - "1 7 :30 - 1 8 : 00" -> "17:30 - 18:00"
    - "16:0 0" -> "16:00"
    """
    s = normalize_text(s)
    s = re.sub(r"\b([0-2]?[0-9])\s*:\s*([0-5])\s+([0-9])\s*([ap])m\b", r"\1:\2\3\4m", s, flags=re.I)
    s = re.sub(r"\b([012])\s+([0-9])\s*:\s*([0-5])\s*([0-9])\b", r"\1\2:\3\4", s)
    s = re.sub(r"\b([012])\s+([0-9])\s*:\s*([0-5][0-9])\b", r"\1\2:\3", s)
    s = re.sub(r"\b([0-2]?[0-9])\s*:\s*([0-5])\s+([0-9])\b", r"\1:\2\3", s)
    s = re.sub(r"\b([0-2]?[0-9])\s*:\s*([0-5][0-9])\b", r"\1:\2", s)
    s = re.sub(r"\b([0-9]):([0-5][0-9])\b", r"0\1:\2", s)
    s = AMPM_RANGE_RE.sub(lambda m: "-".join(ampm_range_to_24h(m)), s)
    return s


def text_lines_from_soup(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style", "noscript", "svg", "img", "form"]):
        tag.decompose()
    chunks: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "div"]):
        text = normalize_text(tag.get_text(" ", strip=True))
        has_time = bool(TIME_RE.search(text) or SINGLE_TIME_RE.search(text) or AMPM_TIME_RE.search(text))
        if (12 <= len(text) <= 600) or (has_time and 4 <= len(text) <= 600):
            chunks.append(text)
    out: list[str] = []
    seen = set()
    for line in chunks:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def source_link_for_paper(paper: dict) -> str:
    page = DAY_TO_PAGE.get(paper.get("day", ""), "")
    session = (paper.get("session") or paper.get("code", "").split(".")[0]).lower()
    return urljoin(PAPERCEPT_BASE, page + (f"#{session}" if session else ""))


def ensure_explorer_repo() -> Path:
    if EXPLORER_DIR.exists():
        run(["git", "fetch", "--depth", "1", "origin"], cwd=EXPLORER_DIR)
        run(["git", "reset", "--hard", "origin/main"], cwd=EXPLORER_DIR)
    else:
        run(["git", "clone", "--depth", "1", EXPLORER_REPO, str(EXPLORER_DIR)])
    return EXPLORER_DIR


def load_github_papers(repo: Path) -> list[dict]:
    data = json.loads((repo / "output" / "papers.json").read_text(encoding="utf-8"))
    return data["papers"]


def parse_keywords(block_soup: BeautifulSoup) -> list[str]:
    text = normalize_text(block_soup.get_text(" ", strip=True))
    if "Keywords:" not in text:
        return []
    rest = text.split("Keywords:", 1)[1]
    rest = rest.split("Abstract:", 1)[0]
    return [normalize_text(x) for x in rest.split(",") if normalize_text(x)]


def parse_abstract(block_soup: BeautifulSoup) -> str:
    text = normalize_text(block_soup.get_text(" ", strip=True))
    if "Abstract:" not in text:
        return ""
    return normalize_text(text.split("Abstract:", 1)[1])


def parse_authors(block_soup: BeautifulSoup) -> list[dict]:
    authors = []
    for tr in block_soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) != 2:
            continue
        left = normalize_text(cells[0].get_text(" ", strip=True))
        right = normalize_text(cells[1].get_text(" ", strip=True))
        if not left or not right:
            continue
        if left.startswith(("Chair:", "Co-Chair:", "Keywords:", "Abstract:")):
            continue
        if "Add to My Program" in right:
            continue
        # Paper author rows have an AuthorIndex link in the left cell.
        if not cells[0].find("a", href=re.compile("AuthorIndex")):
            continue
        authors.append({"name": left, "aff": right})
    return authors


def fetch_papercept_day(day: str, page: str) -> str:
    url = urljoin(PAPERCEPT_BASE, page)
    status, final_url, text = fetch(url, timeout=30)
    if status != 200 or not text:
        raise RuntimeError(f"Could not fetch PaperCept day page {day}: {status} {final_url}")
    (TMP / page).write_text(text, encoding="utf-8")
    return text


def parse_papercept_current(github_papers: list[dict]) -> list[dict]:
    """Parse the current PaperCept day pages and use GitHub explorer as fallback."""
    backup = {p.get("code"): p for p in github_papers}
    days = [
        ("Tuesday", DAY_TO_PAGE["Tuesday"]),
        ("Wednesday", DAY_TO_PAGE["Wednesday"]),
        ("Thursday", DAY_TO_PAGE["Thursday"]),
    ]
    papers: list[dict] = []
    marker_re = re.compile(
        r'<tr class="sHdr">\s*<td nowrap><a name="(?P<sanchor>[^"]+)"><b>(?P<scode>[^<]+)</b>&nbsp;\s*(?P<stype>[^,<]+),\s*(?P<room>[^<]+)</td>.*?</tr>|'
        r'<tr class="pHdr"><td valign="bottom"><a name="(?P<panchor>[^"]+)">(?P<time>\d{2}:\d{2}-\d{2}:\d{2}),\s*Paper\s+(?P<pcode>[A-Za-z0-9.]+)</a>',
        re.S,
    )
    session_title_re = re.compile(r'<tr class="sHdr">\s*<td nowrap><a href="[^"]+"><b>(.*?)</b></a></td>', re.S)

    for day, page in days:
        text = fetch_papercept_day(day, page)
        matches = list(marker_re.finditer(text))
        current_session = {"session": "", "sessionTitle": "", "room": "", "anchor": ""}
        for i, m in enumerate(matches):
            if m.group("scode"):
                after = text[m.end() : m.end() + 1500]
                title_m = session_title_re.search(after)
                current_session = {
                    "session": normalize_text(m.group("scode")),
                    "sessionTitle": normalize_text(title_m.group(1)) if title_m else normalize_text(m.group("stype")),
                    "room": normalize_text(m.group("room")),
                    "anchor": normalize_text(m.group("sanchor")),
                }
                continue

            code = m.group("pcode")
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block_html = text[start:end]
            block_soup = BeautifulSoup(block_html, "html.parser")
            title_tag = block_soup.find("span", class_="pTtl")
            title = normalize_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
            if not title:
                continue
            gh = backup.get(code, {})
            authors = parse_authors(block_soup) or gh.get("authors") or []
            author_text = "; ".join(
                [f"{a.get('name','')} ({a.get('aff','')})" for a in authors[:8]]
            )
            keywords = parse_keywords(block_soup) or gh.get("keywords") or []
            abstract = parse_abstract(block_soup) or gh.get("abstract", "")
            time_slot = m.group("time")
            search_text = " ".join(
                [
                    title,
                    abstract,
                    " ".join(keywords),
                    author_text,
                    author_search_variants(author_text),
                    current_session.get("sessionTitle", ""),
                    current_session.get("room", ""),
                    code,
                ]
            )
            papers.append(
                {
                    "type": "paper",
                    "source": "PaperCept current program; GitHub explorer fallback",
                    "id": f"{day[:3]}-{code}",
                    "code": code,
                    "day": day,
                    "start": time_slot.split("-")[0],
                    "end": time_slot.split("-")[-1],
                    "time": time_slot,
                    "title": title,
                    "session": current_session.get("session"),
                    "sessionTitle": current_session.get("sessionTitle"),
                    "room": current_session.get("room"),
                    "authors": author_text,
                    "keywords": keywords,
                    "abstract": abstract,
                    "url": urljoin(PAPERCEPT_BASE, page + (f"#{m.group('panchor')}" if m.group("panchor") else "")),
                    "searchText": normalize_text(search_text).lower(),
                    "displayText": normalize_text(" ".join([title, abstract, " ".join(keywords), author_text])).lower(),
                }
            )
    return papers


def normalize_technical_papers_from_github(github_papers: list[dict]) -> list[dict]:
    papers = github_papers
    normalized = []
    for p in papers:
        authors = p.get("authors") or []
        author_text = "; ".join(
            [f"{a.get('name','')} ({a.get('aff','')})" for a in authors[:8]]
        )
        keywords = p.get("keywords") or []
        search_text = " ".join(
            [
                p.get("title", ""),
                p.get("abstract", ""),
                " ".join(keywords),
                author_text,
                author_search_variants(author_text),
                p.get("session_title", ""),
                p.get("room", ""),
                p.get("code", ""),
            ]
        )
        normalized.append(
            {
                "type": "paper",
                "source": "PaperCept / icra2026-explorer",
                "id": p.get("id"),
                "code": p.get("code"),
                "day": p.get("day"),
                "start": (p.get("time") or "").split("-")[0],
                "end": (p.get("time") or "").split("-")[-1],
                "time": p.get("time"),
                "title": p.get("title"),
                "session": p.get("session"),
                "sessionTitle": p.get("session_title"),
                "room": p.get("room"),
                "authors": author_text,
                "keywords": keywords,
                "abstract": p.get("abstract", ""),
                "url": source_link_for_paper(p),
                "searchText": normalize_text(search_text).lower(),
            }
        )
    return normalized


def load_workshop_table() -> list[dict]:
    status, final_url, text = fetch(WORKSHOPS_URL)
    if not text:
        raise RuntimeError(f"Could not fetch workshops page: {status} {final_url}")
    (TMP / "workshops.html").write_text(text, encoding="utf-8")
    soup = BeautifulSoup(text, "html.parser")
    items: list[dict] = []
    for table in soup.find_all("table"):
        headers = [h.get_text(" ", strip=True) for h in table.find_all("th")]
        day = "Monday" if any("Monday" in h for h in headers) else "Friday" if any("Friday" in h for h in headers) else ""
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue
            category = normalize_text(cells[0].get_text(" ", strip=True))
            if category not in {"Workshop", "Tutorial"}:
                continue
            title_cell = cells[1]
            a = title_cell.find("a", href=True)
            title = normalize_text(title_cell.get_text(" ", strip=True))
            duration = normalize_text(cells[2].get_text(" ", strip=True))
            block = normalize_text(cells[3].get_text(" ", strip=True))
            room = normalize_text(cells[4].get_text(" ", strip=True))
            if block == "MORNING":
                start, end = "09:00", "12:30"
            elif block == "AFTERNOON":
                start, end = "14:00", "17:30"
            else:
                start, end = "09:00", "17:30"
            url = a["href"] if a else ""
            items.append(
                {
                    "type": "workshop",
                    "source": "ICRA official workshop list",
                    "id": f"{day[:3].lower()}-ws-{len(items)+1:02d}",
                    "category": category,
                    "day": day,
                    "start": start,
                    "end": end,
                    "time": f"{start}-{end}",
                    "duration": duration,
                    "block": block,
                    "room": room,
                    "title": title,
                    "url": url,
                    "pages": [],
                    "presentations": [],
                    "crawlStatus": "not_crawled",
                    "searchText": normalize_text(" ".join([title, category, room, block, url])).lower(),
                }
            )
    return items


def same_site_or_safe(base: str, url: str) -> bool:
    b = urlparse(base)
    u = urlparse(url)
    if not u.netloc:
        return True
    return u.netloc == b.netloc


def discover_subpages(base_url: str, soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        label = normalize_text(a.get_text(" ", strip=True))
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urldefrag(urljoin(base_url, href))[0]
        if not full.startswith(("http://", "https://")):
            continue
        if not same_site_or_safe(base_url, full):
            continue
        hay = f"{label} {href}"
        if SUBPAGE_SKIP_RE.search(f"{label} {full}"):
            continue
        if LINK_HINT_RE.search(hay):
            urls.append(full)
    out: list[str] = []
    seen = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 8:
            break
    return out


def classify_line(line: str, context: str) -> str:
    low = line.lower()
    hay = f"{context} {line}".lower()
    if "lightning" in low:
        return "lightning"
    if "poster" in low:
        return "poster"
    if "accepted" in low or "paper" in low:
        return "paper"
    if "panel" in low:
        return "panel"
    if "spotlight" in low or "talk" in low:
        return "talk"
    if re.search(r"\b(break|lunch|coffee)\b", low):
        return "break"
    if (
        "presentation" in low
        or "speaker" in low
        or "keynote" in low
        or TIME_RE.search(line)
        or SINGLE_TIME_RE.search(line)
        or AMPM_TIME_RE.search(line)
    ):
        return "talk"
    if "schedule" in hay or "program" in hay or "agenda" in hay:
        return "program"
    return "mention"


def add_minutes(t: str, delta: int) -> str:
    h, m = [int(x) for x in t.split(":", 1)]
    total = h * 60 + m + delta
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def ampm_to_24h(match: re.Match) -> str:
    h = int(match.group("h"))
    m = int(match.group("m") or "00")
    ampm = match.group("ampm").lower()
    if ampm.startswith("p") and h != 12:
        h += 12
    if ampm.startswith("a") and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def clock_to_24h(hour: str, minute: str | None, ampm: str) -> str:
    h = int(hour)
    m = int(minute or "00")
    marker = ampm.lower()
    if marker.startswith("p") and h != 12:
        h += 12
    if marker.startswith("a") and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def ampm_range_to_24h(match: re.Match) -> tuple[str, str]:
    end_ampm = match.group("eampm")
    start_ampm = match.group("sampm") or end_ampm
    if not match.group("sampm") and end_ampm.lower().startswith("p"):
        sh = int(match.group("sh"))
        eh = int(match.group("eh"))
        if eh == 12 and 8 <= sh < 12:
            start_ampm = "am"
    start = clock_to_24h(match.group("sh"), match.group("sm"), start_ampm)
    end = clock_to_24h(match.group("eh"), match.group("em"), end_ampm)

    # Several workshop pages have obvious AM/PM typos such as
    # "10:30 pm to 11:00 am" for a morning coffee break. Prefer the only
    # interpretation that produces a normal workshop-slot duration.
    dur = duration_minutes(start, end)
    if dur is not None and dur > 6 * 60:
        alt_start = clock_to_24h(match.group("sh"), match.group("sm"), end_ampm)
        alt_dur = duration_minutes(alt_start, end)
        if alt_dur is not None and alt_dur <= 3 * 60:
            start = alt_start
    return start, end


def minutes_of_day(t: str) -> int | None:
    if not t or ":" not in t:
        return None
    try:
        h, m = [int(x) for x in t.split(":", 1)]
    except ValueError:
        return None
    return h * 60 + m


def duration_minutes(start: str, end: str) -> int | None:
    s = minutes_of_day(start)
    e = minutes_of_day(end)
    if s is None or e is None:
        return None
    if e < s:
        e += 24 * 60
    return e - s


def parse_single_clock(raw: str) -> str:
    text = normalize_schedule_time_text(raw)
    ampm = AMPM_TIME_RE.search(text)
    if ampm:
        return ampm_to_24h(ampm)
    sm = SINGLE_TIME_RE.search(text)
    return sm.group(0).replace(".", ":") if sm else ""


def fix_noon_crossing(start: str, end: str) -> tuple[str, str]:
    s = minutes_of_day(start)
    e = minutes_of_day(end)
    if s is None or e is None:
        return start, end
    if e < s and s >= 11 * 60 and e <= 6 * 60:
        end = add_minutes(end, 12 * 60)
    return start, end


def normalize_time_sequence(items: list[dict]) -> list[dict]:
    """Recover afternoon times written as 01:30, 02:30, ... after a noon row."""
    prev_start: int | None = None
    seen_noon = False
    out: list[dict] = []
    for item in items:
        copy = item.copy()
        start = copy.get("start", "")
        end = copy.get("end", "")
        if start and end:
            start, end = fix_noon_crossing(start, end)
            s = minutes_of_day(start)
            e = minutes_of_day(end)
            if s is not None:
                shifted_late = False
                if s >= 19 * 60:
                    start = add_minutes(start, -12 * 60)
                    if e is not None and e >= 19 * 60:
                        end = add_minutes(end, -12 * 60)
                    elif e is not None and e < 2 * 60:
                        end = add_minutes(end, 12 * 60)
                    s = minutes_of_day(start)
                    shifted_late = True
                should_shift = False
                if shifted_late:
                    should_shift = False
                elif s < 7 * 60 and (seen_noon or (prev_start is not None and prev_start >= 10 * 60)):
                    should_shift = True
                elif prev_start is not None and s < 7 * 60 and s < prev_start and s + 12 * 60 > prev_start:
                    should_shift = True
                if should_shift:
                    start = add_minutes(start, 12 * 60)
                    end = add_minutes(end, 12 * 60)
                    s = minutes_of_day(start)
                copy["start"] = start
                copy["end"] = end
                copy["time"] = f"{start}-{end}"
                if s is not None:
                    prev_start = s
                    if s >= 12 * 60:
                        seen_noon = True
            elif prev_start is not None:
                prev_start = prev_start
        out.append(copy)
    return out


def strip_pdf_filename(text: str) -> str:
    text = re.sub(r"\bdrive,\s*", "", text or "", flags=re.I)
    return normalize_text(PDF_FILE_RE.sub("", text))


def strip_leading_time(text: str) -> str:
    text = normalize_text(text)
    m = TIME_RE.search(text)
    if m and m.start() <= 2:
        return text[m.end() :].strip(" \t:-–—,|")
    sm = SINGLE_TIME_RE.search(text)
    if sm and sm.start() <= 2:
        return text[sm.end() :].strip(" \t:-–—,|")
    return text


def remove_embedded_time_for_title(text: str) -> str:
    text = normalize_text(text)
    m = TIME_RE.search(text)
    if not m:
        return text
    before = text[: m.start()].strip(" \t:-–—,|")
    after = text[m.end() :].strip(" \t:-–—,|")
    if after and (not before or re.fullmatch(r"(session|theme|track|part)\s*\w*", before, flags=re.I) or len(before) <= 30):
        return after
    return before or after or text


def split_schedule_title_speaker(line: str) -> tuple[str, str]:
    """Split schedule rows like `09:10-09:40 Speaker, "Talk title"`."""
    body = strip_leading_time(strip_pdf_filename(line))
    if not body:
        return strip_pdf_filename(line), ""
    body = remove_embedded_time_for_title(body)
    quote_match = re.search(r"^(?P<speaker>.*?)[\"“](?P<title>[^\"”]+)[\"”](?P<tail>.*)$", body)
    if quote_match:
        speaker = normalize_text(quote_match.group("speaker")).strip(" ,-–—")
        title = normalize_text(quote_match.group("title")).strip(" .")
        if speaker in {"-", "–", "—"}:
            speaker = ""
        if title:
            return title, speaker
    quote_match = re.search(r"^(?P<speaker>.+?)(?:\s*,\s*)?[\"“](?P<title>[^\"”]+)[\"”]\s*\.?$", body)
    if quote_match:
        speaker = normalize_text(quote_match.group("speaker")).strip(" ,")
        title = normalize_text(quote_match.group("title")).strip(" .")
        if speaker in {"-", "–", "—"}:
            speaker = ""
        if title:
            return title, speaker
    return body, ""


def compact_for_compare(text: str) -> str:
    return re.sub(r"\W+", "", normalize_text(text).lower())


def looks_like_speaker_line(text: str) -> bool:
    clean = normalize_text(text).strip()
    if not clean or len(clean) > 140:
        return False
    if TIME_RE.search(clean) or SINGLE_TIME_RE.search(clean) or AMPM_TIME_RE.search(clean):
        return False
    if is_public_presentation_noise(clean):
        return False
    if re.search(r"\b(session|topic|break|poster|lightning|panel discussion)\b", clean, re.I):
        return False
    # Speaker/author rows on workshop pages are usually name-like, often with
    # "and", commas, affiliations, or "(remote)".
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'.-]*", clean)
    return 1 <= len(words) <= 12


def split_title_speaker_from_neighbors(title: str, lines: list[str], idx: int) -> tuple[str, str]:
    """Use nearby repeated Google Sites rows to split `Title Speaker` strings."""
    full = normalize_text(title)
    full_key = compact_for_compare(full)
    if not full_key:
        return title, ""

    candidates: list[str] = []
    for nxt in lines[idx + 1 : idx + 8]:
        if TIME_RE.search(nxt) or SINGLE_TIME_RE.search(nxt) or AMPM_TIME_RE.search(nxt):
            if TIME_ONLY_RE.match(nxt) or TIME_RANGE_ONLY_RE.match(nxt):
                continue
            body = strip_leading_time(strip_pdf_filename(nxt))
            if compact_for_compare(body) == full_key:
                continue
            break
        clean = strip_pdf_filename(nxt)
        if not clean or is_public_presentation_noise(clean):
            continue
        if compact_for_compare(clean) == full_key:
            continue
        if 2 <= len(clean) <= 240:
            candidates.append(clean)
        if len(candidates) >= 5:
            break

    for left, right in zip(candidates, candidates[1:]):
        left_key = compact_for_compare(left)
        right_key = compact_for_compare(right)
        if not left_key or not right_key or left_key == full_key:
            continue
        if looks_like_speaker_line(right) and (
            full_key == left_key + right_key
            or (left_key in full_key and right_key in full_key and full_key.endswith(right_key))
        ):
            return left, right
    return title, ""


CAPITAL_RE = r"[A-ZÀ-ÖØ-ÞĀĂĄĆĈĊČĎĐĒĔĖĘĚĞĜĠĢĤĦĨĪĬĮİĴĶĹĻĽĿŁŃŅŇŊŌŎŐŒŔŖŘŚŜŞŠŢŤŦŨŪŬŮŰŲŴŶŸŹŻŽ]"
NAME_WORD_RE = rf"{CAPITAL_RE}[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'’.-]+"
FIRST_AUTHOR_NAME_RE = rf"{NAME_WORD_RE}(?:\s+[A-Z]\.)?\s+{NAME_WORD_RE}"
AUTHOR_NAME_RE = rf"{NAME_WORD_RE}(?:\s+(?:[A-Z]\.|{NAME_WORD_RE}))?\s+{NAME_WORD_RE}"


def split_likely_author_suffix(text: str) -> tuple[str, str]:
    """Split `Paper title Author One, Author Two` when a page omits columns."""
    clean = normalize_text(text)
    author_flex = rf"{NAME_WORD_RE}(?:\s+(?:[A-Z]\.|{NAME_WORD_RE})){{1,3}}"
    author_list_re = re.compile(rf"{author_flex}(?:,\s*{author_flex})+(?:,?\s+(?:and|&)\s+{author_flex})?$")
    bad_title_end = {"in", "of", "for", "from", "through", "via", "with", "and", "or", "the", "a", "an", "to", "on", "by"}
    non_name_starts = {
        "Environment",
        "Environments",
        "Robot",
        "Robots",
        "Robotics",
        "Perception",
        "Recognition",
        "Localization",
        "Localisation",
        "Mapping",
        "Navigation",
        "Learning",
        "Control",
        "Systems",
        "System",
        "Odometry",
        "Intelligence",
        "Benchmarking",
        "Framework",
        "Language",
        "Descriptions",
        "VLM",
    }
    for boundary in re.finditer(rf"\s(?={CAPITAL_RE})", clean):
        title = clean[: boundary.start()].strip(" ,.;:-–—")
        authors = clean[boundary.end() :].strip(" ,.;")
        if len(title.split()) < 3:
            continue
        if title.split()[-1].lower() in bad_title_end:
            continue
        if not author_list_re.fullmatch(authors):
            continue
        if authors.split()[0].strip(" ,.;:-–—") in non_name_starts:
            continue
        author_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ſ][A-Za-zÀ-ÖØ-öø-ÿĀ-ſ'’.-]*", authors)
        if len(author_words) > 24:
            continue
        return title, authors
    return clean, ""


def split_quoted_title_authors(text: str) -> tuple[str, str]:
    """Split lines like `"Paper title" Author One, Author Two`."""
    clean = normalize_text(strip_pdf_filename(text)).strip()
    m = re.match(r"^[\"“]\s*(?P<title>.+?)\s*[\"”]\s*,?\s*(?P<authors>.*)$", clean)
    if not m:
        return clean, ""
    title = normalize_text(m.group("title")).strip(" .")
    authors = normalize_text(m.group("authors")).strip(" ,.;")
    authors = re.sub(r"\s*\([^)]*paper presentation[^)]*\)\s*$", "", authors, flags=re.I).strip(" ,.;")
    return title, authors


def is_discardable_presentation_title(title: str) -> bool:
    clean = normalize_text(title)
    if not clean:
        return True
    if TIME_ONLY_RE.match(clean) or TIME_RANGE_ONLY_RE.match(clean):
        return True
    if PDF_ONLY_RE.match(clean):
        return True
    lower = clean.lower()
    if "accepted poster" in lower and any(
        marker in lower for marker in ["home", "competition", "archive", "dataset", "abstract submission", "more"]
    ):
        return True
    if "google sites" in lower and ("report abuse" in lower or "page details" in lower or "page updated" in lower):
        return True
    if lower.startswith(("call for poster", "go to [call", "if you have any inquiries")):
        return True
    if lower in {"calls for participation", "topics of interest", "call for abstracts", "call for research talks"}:
        return True
    if "workshop is designed to advance" in lower or "poster submission" in lower:
        return True
    if "below are the papers selected" in lower or "poster preparation guidelines" in lower:
        return True
    if "official ieee icra instructions" in lower or "following works have been accepted" in lower:
        return True
    if "all papers must follow the official ieee" in lower:
        return True
    if "authors of accepted abstracts will" in lower or "prepare a poster in" in lower:
        return True
    if "ieee international conference on robotics and automation" in lower or "vienna, austria" in lower:
        return True
    if "as more and more robots are deployed" in lower:
        return True
    if lower.startswith("2026 icra workshop"):
        return True
    if "the workshop will take place between" in lower or "planned schedule" in lower:
        return True
    if "poster mounting" in lower or "announcement of the best poster" in lower:
        return True
    if "best poster award" in lower or "please submit both by" in lower:
        return True
    if "accepted papers are listed below" in lower or lower.startswith("call for extended abstracts"):
        return True
    if lower.startswith("below is a tentative program"):
        return True
    if lower.startswith("share this:") or "opens in new window" in lower:
        return True
    if lower.startswith("in this session, posters of the following papers will be presented"):
        return True
    if lower.startswith("presentation of results of the break-out sessions"):
        return True
    if lower.startswith("this full-day workshop involves a combination"):
        return True
    if lower.startswith("date and time:"):
        return True
    return lower in {
        "accepted poster",
        "accepted poster list",
        "accepted posters",
        "call for posters",
        "demo & poster teasers",
        "poster session accepted poster",
        "poster teasers",
        "presentation",
        "presentations",
        "| demo & poster teasers",
    }


def grouped_slot_kind(line: str) -> str:
    lower = line.lower()
    if "poster" in lower and "teaser" in lower:
        return "lightning"
    if "lightning" in lower and ("poster" in lower or "talk" in lower):
        return "lightning"
    if "contributed presentation" in lower:
        return "paper"
    if "poster session" in lower and "accepted" not in lower:
        return "poster"
    return ""


def looks_like_grouped_paper_title(line: str) -> bool:
    text = strip_pdf_filename(line)
    if is_discardable_presentation_title(text):
        return False
    if TIME_RE.search(text) or SINGLE_TIME_RE.search(text):
        return False
    if re.search(r"(cookie|privacy|organizer|organiser|subscribe|important dates|submission deadline|call for papers)", text, re.I):
        return False
    if SUBMISSION_NOISE_RE.search(text) or BIO_NOISE_RE.search(text):
        return False
    lower = text.lower()
    if any(x in lower for x in ["google sites", "search this site", "skip to", "report abuse", "page updated"]):
        return False
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9()+/-]*", text)
    return len(words) >= 3 and (":" in text or "-" in text or any(len(w) >= 7 for w in words))


def is_schedule_group_heading(title: str) -> bool:
    clean = strip_pdf_filename(TIME_RE.sub("", title)).strip(" :-–—.\t").lower()
    return clean in {
        "poster lightning talks",
        "lightning talks",
        "poster session",
        "poster session and coffee break",
        "coffee break and poster session",
        "coffee break poster session and interaction",
        "coffee break + putting up posters/interaction",
        "coffee break & poster session",
        "demo & poster teasers",
        "| demo & poster teasers",
        "poster flash presentations (2 minutes each)",
    }


PUBLIC_PRESENTATION_NOISE_RE = re.compile(
    r"(submission|deadline|camera[-\s]*ready|openreview|template|page limit|format requirement|"
    r"submit paper|call for|important dates|please prepare|should be prepared|will accept submissions|"
    r"accepted abstracts will have|poster track submission|dual submission|paper length|style template|"
    r"below are the papers selected|get in touch with the authors|poster preparation guidelines|"
    r"official ieee icra instructions|demo\s*&\s*poster teasers|poster teasers|poster flash presentations|"
    r"all papers must follow the official ieee|authors of accepted abstracts will|prepare a poster in|"
    r"ieee international conference on robotics and automation|"
    r"vienna, austria|as more and more robots are deployed|^2026 icra workshop|"
    r"the accepted papers will be presented|poster boards fit|the workshop will take place between|planned schedule|"
    r"poster mounting|announcement of the best poster|best poster award|please submit both by|"
    r"please contact|contact us|for questions or inquiries|home speakers schedule|venue more home|news may|"
    r"october 9, 2022|lorem ipsum)",
    re.I,
)


def is_public_presentation_noise(title: str) -> bool:
    clean = normalize_text(title)
    lower = clean.lower()
    if is_discardable_presentation_title(clean):
        return True
    if PUBLIC_PRESENTATION_NOISE_RE.search(clean):
        return True
    if SINGLE_TIME_RE.search(strip_leading_time(clean)):
        return True
    if lower.startswith(("use the ieee", "final versions of accepted papers", "oral presentations:", "poster presentations:", "l ink to", "link to")):
        return True
    return False


def split_dense_schedule_segments(line: str) -> list[dict]:
    """Split compact schedule lines containing several timed entries."""
    clean = normalize_schedule_time_text(line)
    if SUBMISSION_NOISE_RE.search(clean) or PUBLIC_PRESENTATION_NOISE_RE.search(clean):
        return []
    out: list[dict] = []
    range_matches = list(TIME_RE.finditer(clean))
    if len(range_matches) >= 2:
        for i, match in enumerate(range_matches):
            start = match.group("s").replace(".", ":")
            end = match.group("e").replace(".", ":")
            start, end = fix_noon_crossing(start, end)
            next_start = range_matches[i + 1].start() if i + 1 < len(range_matches) else len(clean)
            title = normalize_text(clean[match.end() : next_start]).strip(" :-–—,")
            if 4 <= len(title) <= 260 and not is_discardable_presentation_title(title):
                out.append({"start": start, "end": end, "title": title, "time": f"{start}-{end}"})
        return normalize_time_sequence(out)

    ampm_matches = list(AMPM_TIME_RE.finditer(clean))
    if len(ampm_matches) >= 2:
        for i, match in enumerate(ampm_matches):
            start = ampm_to_24h(match)
            end = ampm_to_24h(ampm_matches[i + 1]) if i + 1 < len(ampm_matches) else add_minutes(start, 30)
            title_end = ampm_matches[i + 1].start() if i + 1 < len(ampm_matches) else len(clean)
            title = normalize_text(clean[match.end() : title_end]).strip(" :-–—,")
            if 4 <= len(title) <= 220 and not is_discardable_presentation_title(title):
                out.append({"start": start, "end": end, "title": title, "time": f"{start}-{end}"})
    return normalize_time_sequence(out)


def extract_presentations(lines: list[str], page_url: str) -> list[dict]:
    presentations: list[dict] = []
    context = ""
    active_group: dict | None = None
    accepted_list_kind = ""
    clean_lines = [normalize_schedule_time_text(line) for line in lines]
    for idx, line in enumerate(clean_lines):
        low = line.lower()
        dense_segments = split_dense_schedule_segments(line)
        if dense_segments:
            for segment in dense_segments:
                segment_line = f"{segment['time']} {segment['title']}"
                title, speaker = split_schedule_title_speaker(segment_line)
                if is_discardable_presentation_title(title):
                    continue
                presentations.append(
                    {
                        "kind": classify_line(segment_line, context),
                        "title": title,
                        "speaker": speaker,
                        "start": segment["start"],
                        "end": segment["end"],
                        "time": segment["time"],
                        "context": context,
                        "url": page_url,
                    }
                )
            active_group = None
            continue
        has_range = bool(TIME_RE.search(line))
        has_single_time = bool(SINGLE_TIME_RE.search(line) or AMPM_TIME_RE.search(line))
        if has_range or has_single_time:
            accepted_list_kind = ""
        elif (
            low.strip(" :") in {"accepted abstract", "accepted abstracts", "accepted poster", "accepted posters", "accepted papers"}
            or "following works have been accepted" in low
        ):
            context = "Accepted poster/demo contributions" if "poster" in low or "abstract" in low else "Accepted papers"
            accepted_list_kind = "poster" if "poster" in low or "abstract" in low else "paper"
            continue
        if (active_group or accepted_list_kind) and not has_range and not has_single_time:
            raw_title = strip_pdf_filename(line)
            title, speaker = split_quoted_title_authors(raw_title)
            if looks_like_grouped_paper_title(title):
                if is_discardable_presentation_title(title):
                    continue
                group = active_group or {"kind": accepted_list_kind, "start": "", "end": "", "time": "", "context": context}
                presentations.append(
                    {
                        "kind": group["kind"],
                        "title": title,
                        "speaker": speaker,
                        "start": group["start"],
                        "end": group["end"],
                        "time": group["time"],
                        "context": group["context"],
                        "url": page_url,
                    }
                )
                continue
            if accepted_list_kind and (SUBMISSION_NOISE_RE.search(line) or BIO_NOISE_RE.search(line) or NOISE_RE.search(line)):
                accepted_list_kind = ""
        if has_range or has_single_time:
            active_group = None
        upcoming_has_time = any(
            TIME_RE.search(x) or SINGLE_TIME_RE.search(x) or AMPM_TIME_RE.search(x)
            for x in clean_lines[idx + 1 : idx + 4]
        )
        if (
            not has_range
            and not has_single_time
            and upcoming_has_time
            and 4 <= len(line) <= 120
            and not NOISE_RE.search(line)
            and CONTEXT_HEADING_RE.search(line)
        ):
            context = line
            continue
        if LINK_HINT_RE.search(line) and len(line) < 140:
            context = line
        should_capture = False
        if has_range or has_single_time:
            should_capture = True
        if ("paper" in low or "poster" in low or "talk" in low or "presentation" in low or "speaker" in low) and not NOISE_RE.search(line):
            should_capture = True
        if not should_capture:
            continue
        if SUBMISSION_NOISE_RE.search(line) and not (has_range or has_single_time):
            continue
        if "award" in line.lower() and not (has_range or has_single_time):
            continue
        if BIO_NOISE_RE.search(line) and not (has_range or has_single_time):
            continue
        if TIME_ONLY_RE.match(line) or TIME_RANGE_ONLY_RE.match(line):
            if TIME_ONLY_RE.match(line):
                start = line
                if idx > 0:
                    prev_start, _prev_end, _prev_label = parse_time_range(clean_lines[idx - 1])
                    if prev_start == start and not TIME_ONLY_RE.match(clean_lines[idx - 1]):
                        continue
                end = ""
                collected: list[str] = []
                for nxt in clean_lines[idx + 1 : idx + 14]:
                    if TIME_RE.search(nxt) or SINGLE_TIME_RE.search(nxt) or AMPM_TIME_RE.search(nxt):
                        next_time = SINGLE_TIME_RE.search(nxt)
                        end = next_time.group(0).replace(".", ":") if next_time else ""
                        break
                    if is_public_presentation_noise(nxt) or re.match(r"call for|the accepted papers are listed", nxt, re.I):
                        break
                    if 8 <= len(nxt) <= 240 and not NOISE_RE.search(nxt):
                        collected.append(strip_pdf_filename(nxt))
                detail_lines = [
                    x
                    for x in collected
                    if 10 <= len(x) <= 220 and looks_like_grouped_paper_title(x) and not is_public_presentation_noise(x)
                ]
                first_collected = collected[0].lower() if collected else ""
                can_expand_details = (
                    detail_lines
                    and (len(detail_lines) >= 3 or "spotlight" in first_collected or "poster" in first_collected)
                    and not first_collected.startswith(("topic", "session"))
                )
                if can_expand_details:
                    if not end:
                        end = add_minutes(start, 30)
                    group_label = "" if first_collected in {x.lower() for x in detail_lines} else clean_slot_label(first_collected)
                    for detail in detail_lines:
                        detail_title, detail_speaker = split_likely_author_suffix(detail)
                        detail_kind = "spotlight" if len(detail_lines) >= 3 or "spotlight" in first_collected else classify_line(detail, context)
                        if detail_kind == "mention":
                            detail_kind = "paper"
                        presentations.append(
                            {
                                "kind": detail_kind,
                                "title": detail_title,
                                "speaker": detail_speaker,
                                "start": start,
                                "end": end,
                                "time": f"{start}-{end}",
                                "context": group_label or context,
                                "url": page_url,
                            }
                        )
                    continue
            parts = [line]
            for nxt in clean_lines[idx + 1 : idx + 4]:
                if TIME_RE.search(nxt) or SINGLE_TIME_RE.search(nxt):
                    break
                if 2 <= len(nxt) <= 180 and not NOISE_RE.search(nxt):
                    parts.append(nxt)
            line = " ".join(parts)
        line = strip_pdf_filename(line)
        if is_discardable_presentation_title(line) or PUBLIC_PRESENTATION_NOISE_RE.search(line):
            continue
        if len(line) < 8 or len(line) > 420:
            continue
        start, end, _time_label = parse_time_range(line)
        title, speaker = split_schedule_title_speaker(line)
        if not speaker:
            neighbor_title, neighbor_speaker = split_title_speaker_from_neighbors(title, clean_lines, idx)
            if neighbor_speaker:
                title = neighbor_title
                speaker = neighbor_speaker
        if not speaker:
            quoted_title, quoted_speaker = split_quoted_title_authors(title)
            if quoted_speaker:
                title = quoted_title
                speaker = quoted_speaker
        if not speaker:
            title, speaker = split_likely_author_suffix(title)
        if is_discardable_presentation_title(title) or is_public_presentation_noise(title):
            continue
        presentations.append(
            {
                "kind": classify_line(line, context),
                "title": title,
                "speaker": speaker,
                "start": start,
                "end": end,
                "time": f"{start}-{end}" if start and end else "",
                "context": context,
                "url": page_url,
            }
        )
        group_kind = grouped_slot_kind(line)
        if group_kind and start and end:
            group_context = strip_leading_time(strip_pdf_filename(line))
            if group_kind == "lightning" and "teaser" in group_context.lower():
                group_context = "Contributed lightning slot"
            active_group = {
                "kind": group_kind,
                "start": start,
                "end": end,
                "time": f"{start}-{end}",
                "context": group_context,
            }
    # Deduplicate and cap per workshop to keep the browser responsive.
    out: list[dict] = []
    seen = set()
    for item in normalize_time_sequence(presentations):
        key = (
            item.get("time", ""),
            re.sub(r"\W+", "", item.get("title", "").lower())[:160],
            re.sub(r"\W+", "", item.get("speaker", "").lower())[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= 80:
            break
    return out


def parse_time_range(raw: str) -> tuple[str, str, str]:
    text = normalize_schedule_time_text(raw)
    am = AMPM_RANGE_RE.search(text)
    if am:
        start, end = ampm_range_to_24h(am)
        return start, end, f"{start}-{end}"
    m = TIME_RE.search(text)
    if m:
        start = m.group("s").replace(".", ":")
        end = m.group("e").replace(".", ":")
        start, end = fix_noon_crossing(start, end)
        return start, end, f"{start}-{end}"
    start = parse_single_clock(text)
    if start:
        end = add_minutes(start, 30)
        return start, end, f"{start}-{end}"
    return "", "", ""


def heading_rank(tag) -> int:
    if not getattr(tag, "name", "") or not re.match(r"h[1-6]$", tag.name):
        return 7
    return int(tag.name[1])


def section_elements_after_heading(heading):
    rank = heading_rank(heading)
    for el in heading.next_elements:
        if el is heading:
            continue
        if getattr(el, "name", None) and re.match(r"h[1-6]$", el.name) and heading_rank(el) <= rank:
            break
        yield el


def extract_presentations_from_headings(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Extract accepted poster/paper entries where a heading is followed by authors and a PDF embed."""
    heading_text = " ".join(
        normalize_text(h.get_text(" ", strip=True))
        for h in soup.find_all(re.compile(r"^h[1-4]$"))
    )
    page_hint = f"{page_url} {heading_text}"
    url_has_accepted_list = re.search(r"accepted[-_/]?(poster|paper)|poster[-_/]session/accepted", page_url, re.I)
    heading_has_accepted_list = re.search(r"accepted\s+(poster|paper)\s+list|accepted\s+posters?", heading_text, re.I)
    if not (url_has_accepted_list or heading_has_accepted_list):
        return []

    items: list[dict] = []
    seen = set()
    for heading in soup.find_all(re.compile(r"^h[1-4]$")):
        title = strip_pdf_filename(heading.get_text(" ", strip=True))
        if not looks_like_grouped_paper_title(title):
            continue
        authors = ""
        pdf_name = ""
        scanned = 0
        for el in section_elements_after_heading(heading):
            scanned += 1
            if scanned > 500:
                break
            if not getattr(el, "name", None):
                continue
            attrs = " ".join(
                str(el.get(attr, ""))
                for attr in ["aria-label", "data-embed-download-url", "data-src", "href"]
            )
            pdf_match = PDF_FILE_RE.search(attrs)
            if pdf_match and not pdf_name:
                pdf_name = pdf_match.group(0)
            if el.name == "p" and not authors:
                text = strip_pdf_filename(el.get_text(" ", strip=True))
                if (
                    text
                    and text != title
                    and len(text) <= 220
                    and not is_discardable_presentation_title(text)
                    and not NOISE_RE.search(text)
                ):
                    authors = text
        if not pdf_name and not authors:
            continue
        key = re.sub(r"\W+", "", f"{title} {authors} {pdf_name}".lower())[:220]
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "kind": "poster" if "poster" in page_hint.lower() else "paper",
                "title": title,
                "speaker": authors,
                "paperId": pdf_name,
                "start": "",
                "end": "",
                "time": "",
                "context": authors,
                "url": page_url,
            }
        )
    return items


def extract_presentations_from_tables(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Extract structured workshop schedule and accepted-paper rows from HTML tables."""
    items: list[dict] = []
    paper_table_index = 0
    for table in soup.find_all("table"):
        special_row_keys: set[str] = set()
        for tr in table.find_all("tr"):
            cells_raw = tr.find_all(["th", "td"])
            if len(cells_raw) < 2:
                continue
            start, end, time_label = parse_time_range(cells_raw[0].get_text(" ", strip=True))
            if not start or not end:
                continue
            topic_strings = [
                strip_pdf_filename(normalize_text(part))
                for part in cells_raw[1].stripped_strings
                if normalize_text(part)
            ]
            if len(topic_strings) < 3 or not topic_strings[0].lower().startswith("spotlight talks"):
                continue
            special_row_keys.add(compact_for_compare(" ".join(topic_strings)))
            context_label = topic_strings[0]
            for title, authors in zip(topic_strings[1::2], topic_strings[2::2]):
                title = strip_pdf_filename(title)
                authors = strip_pdf_filename(authors)
                if is_discardable_presentation_title(title) or is_public_presentation_noise(title):
                    continue
                items.append(
                    {
                        "kind": "spotlight",
                        "title": title,
                        "speaker": authors,
                        "start": start,
                        "end": end,
                        "time": time_label,
                        "context": context_label,
                        "url": page_url,
                    }
                )
        scripted_items: list[dict] = []
        scripted_context = ""
        for tr in table.find_all("tr"):
            cells_raw = tr.find_all(["th", "td"])
            if len(cells_raw) < 2:
                continue
            time_values = re.findall(r"var\s+time\s*=\s*['\"](\d{1,2}:\d{2})['\"]", str(cells_raw[0]))
            text_parts = [
                normalize_text(part)
                for part in cells_raw[1].stripped_strings
                if normalize_text(part) and not normalize_text(part).startswith("var ")
            ]
            if len(time_values) >= 2 and text_parts:
                start = time_values[0] if len(time_values[0]) == 5 else f"0{time_values[0]}"
                end = time_values[-1] if len(time_values[-1]) == 5 else f"0{time_values[-1]}"
                if "robotmeetsranging.tech" in page_url:
                    start = add_minutes(start, 120)
                    end = add_minutes(end, 120)
                title = strip_pdf_filename(text_parts[0])
                speaker = ", ".join(text_parts[1:3])
                if not is_discardable_presentation_title(title):
                    kind = classify_line(title, scripted_context)
                    if kind == "mention":
                        kind = "talk"
                    scripted_items.append(
                        {
                            "kind": kind,
                            "title": title,
                            "speaker": speaker,
                            "start": start,
                            "end": end,
                            "time": f"{start}-{end}",
                            "context": scripted_context,
                            "url": page_url,
                        }
                    )
            elif text_parts:
                candidate_context = strip_pdf_filename(" ".join(text_parts[:2]))
                if 4 <= len(candidate_context) <= 140 and CONTEXT_HEADING_RE.search(candidate_context):
                    scripted_context = candidate_context
        if scripted_items:
            items.extend(scripted_items)
            continue

        rows = []
        for tr in table.find_all("tr"):
            cells = [normalize_schedule_time_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        header = [c.lower() for c in rows[0]]
        header_text = " ".join(header)

        if "time" in header_text and (
            "topic" in header_text
            or "title" in header_text
            or "speaker" in header_text
            or "session" in header_text
            or "event" in header_text
            or "activity" in header_text
        ):
            time_indices = [i for i, name in enumerate(header) if "time" in name]
            time_idx = next((i for i in time_indices if "start" in header[i]), time_indices[0] if time_indices else 0)
            end_time_idx = next((i for i in time_indices if i != time_idx and "end" in header[i]), None)
            title_idx = next(
                (
                    i
                    for i, name in enumerate(header)
                    if any(token in name for token in ["topic", "title", "talk", "presentation", "session", "event", "activity"])
                ),
                None,
            )
            speaker_idx = next(
                (i for i, name in enumerate(header) if any(token in name for token in ["speaker", "presenter", "name"])),
                None,
            )
            affiliation_idx = next((i for i, name in enumerate(header) if "affiliation" in name), None)
            table_items: list[dict] = []
            for cells in rows[1:]:
                if len(cells) < 2:
                    continue
                time_cell = cells[time_idx] if time_idx < len(cells) else cells[0]
                if end_time_idx is not None and end_time_idx < len(cells):
                    start = parse_single_clock(time_cell)
                    end = parse_single_clock(cells[end_time_idx])
                    start, end = fix_noon_crossing(start, end)
                    time_label = f"{start}-{end}" if start and end else ""
                else:
                    start, end, time_label = parse_time_range(time_cell)
                if not start:
                    continue
                if title_idx is not None and title_idx < len(cells):
                    topic = cells[title_idx]
                else:
                    ignored = {time_idx}
                    if end_time_idx is not None:
                        ignored.add(end_time_idx)
                    topic = " ".join(c for i, c in enumerate(cells) if i not in ignored)
                if compact_for_compare(topic) in special_row_keys:
                    continue
                speaker = cells[speaker_idx] if speaker_idx is not None and speaker_idx < len(cells) else ""
                affiliation = cells[affiliation_idx] if affiliation_idx is not None and affiliation_idx < len(cells) else ""
                title = strip_pdf_filename(topic or speaker or cells[0])
                if is_discardable_presentation_title(title) or is_public_presentation_noise(title):
                    continue
                lower_title = title.lower()
                kind = "talk"
                if "lightning" in lower_title:
                    kind = "lightning"
                elif "poster" in lower_title:
                    kind = "poster"
                elif "panel" in lower_title:
                    kind = "panel"
                elif "spotlight" in lower_title or "talk" in lower_title:
                    kind = "talk"
                elif "break" in lower_title or "lunch" in lower_title:
                    kind = "break"
                table_items.append(
                    {
                        "kind": kind,
                        "title": title,
                        "speaker": speaker,
                        "start": start,
                        "end": end,
                        "time": time_label,
                        "context": affiliation or speaker,
                        "url": page_url,
                    }
                )
            items.extend(normalize_time_sequence(table_items))
            continue

        if ("paper title" in header_text or "title" in header_text) and ("presentation" in header_text or "poster" in header_text or "id" in header_text):
            paper_table_index += 1
            for cells in rows[1:]:
                if len(cells) < 2:
                    continue
                paper_id = cells[0]
                presentation = cells[1] if len(cells) > 1 else ""
                title = strip_pdf_filename(cells[2] if len(cells) > 2 else cells[-1])
                if is_discardable_presentation_title(title) or len(title) < 8:
                    continue
                lower = presentation.lower()
                kind = "poster"
                if "lightning" in lower:
                    kind = "lightning"
                elif "talk" in lower:
                    kind = "talk"
                # Generic fallback: keep accepted paper/poster rows searchable over the full workshop.
                # For common split A/B poster tables, infer a rough slot from table order.
                start = end = time_label = ""
                if paper_table_index == 1:
                    if kind == "lightning":
                        start, end, time_label = "10:00", "10:20", "10:00-10:20"
                    else:
                        start, end, time_label = "10:30", "14:00", "10:30-14:00"
                elif paper_table_index == 2:
                    if kind == "lightning":
                        start, end, time_label = "15:00", "15:20", "15:00-15:20"
                    else:
                        start, end, time_label = "15:30", "16:30", "15:30-16:30"
                items.append(
                    {
                        "kind": kind,
                        "title": title,
                        "speaker": "",
                        "paperId": paper_id,
                        "start": start,
                        "end": end,
                        "time": time_label,
                        "context": f"{paper_id} · {presentation}",
                        "url": page_url,
                    }
                )
    return items


WOSRA_TALK_TIMES = {
    1: ("09:10", "09:35"),
    2: ("09:40", "10:05"),
    3: ("10:10", "10:35"),
    4: ("11:05", "11:30"),
    5: ("11:35", "12:00"),
    6: ("12:05", "12:30"),
}


def is_wosra_j_wosmars_page(page_url: str) -> bool:
    return "wosra.github.io/j-wosmars" in page_url


def extract_wosra_presentations(soup: BeautifulSoup, page_url: str) -> list[dict]:
    if not is_wosra_j_wosmars_page(page_url):
        return []
    items: list[dict] = []
    path = urlparse(page_url).path.rstrip("/")

    if path.endswith("/programme"):
        items.append(
            {
                "kind": "talk",
                "title": "Welcome and general presentation",
                "speaker": "Workshop organizers",
                "start": "09:00",
                "end": "09:05",
                "time": "09:00-09:05",
                "context": "Tentative agenda",
                "url": page_url,
            }
        )
        for li in soup.find_all("li"):
            strong = li.find("strong")
            em = li.find("em")
            if not strong or not em:
                continue
            label = normalize_text(strong.get_text(" ", strip=True))
            m = re.search(r"\[Talk\s*(\d+)\]\s*(.+)", label, re.I)
            if not m:
                continue
            talk_no = int(m.group(1))
            if talk_no not in WOSRA_TALK_TIMES:
                continue
            title = normalize_text(em.get_text(" ", strip=True)).strip(" .")
            speaker_name = normalize_text(m.group(2)).strip(" ,")
            full = normalize_text(li.get_text(" ", strip=True))
            affiliation = full.replace(label, "", 1).replace(title, "", 1).strip(" ,")
            speaker = ", ".join([x for x in [speaker_name, affiliation] if x])
            start, end = WOSRA_TALK_TIMES[talk_no]
            items.append(
                {
                    "kind": "talk",
                    "title": title,
                    "speaker": speaker,
                    "start": start,
                    "end": end,
                    "time": f"{start}-{end}",
                    "context": f"Talk {talk_no}",
                    "url": page_url,
                }
            )
        items.extend(
            [
                {
                    "kind": "spotlight",
                    "title": "Spotlight talks II: IEEE standards status",
                    "speaker": "",
                    "start": "16:05",
                    "end": "16:45",
                    "time": "16:05-16:45",
                    "context": "IEEE standards status",
                    "url": page_url,
                },
                {
                    "kind": "panel",
                    "title": "Panel discussion on Ontologies & Semantic Maps Standards for R&A",
                    "speaker": "Moderated by organizers",
                    "start": "16:50",
                    "end": "17:20",
                    "time": "16:50-17:20",
                    "context": "Tentative agenda",
                    "url": page_url,
                },
                {
                    "kind": "talk",
                    "title": "Closing remarks",
                    "speaker": "Workshop organizers",
                    "start": "17:25",
                    "end": "17:30",
                    "time": "17:25-17:30",
                    "context": "Tentative agenda",
                    "url": page_url,
                },
            ]
        )

    if path.endswith("/papers"):
        for p in soup.select("section.page__content p"):
            strong = p.find("strong")
            em = p.find("em")
            if not strong or not em:
                continue
            title = normalize_text(strong.get_text(" ", strip=True)).strip(" .")
            if not title or title.upper() in {"PAPERS", "NEWS"}:
                continue
            authors = normalize_text(p.get_text(" ", strip=True)).replace(title, "", 1).strip(" .")
            if is_public_presentation_noise(title):
                continue
            items.append(
                {
                    "kind": "spotlight",
                    "title": title,
                    "speaker": authors,
                    "start": "14:05",
                    "end": "15:30",
                    "time": "14:05-15:30",
                    "context": "Spotlight talks I (papers/extended abstracts)",
                    "url": page_url,
                }
            )
    return items


def curated_row(
    kind: str,
    title: str,
    start: str,
    end: str,
    *,
    speaker: str = "",
    context: str = "",
    url: str = "",
    paper_id: str = "",
) -> dict:
    row = {
        "kind": kind,
        "title": title,
        "speaker": speaker,
        "start": start,
        "end": end,
        "time": f"{start}-{end}",
        "context": context,
        "url": url,
    }
    if paper_id:
        row["paperId"] = paper_id
    return row


def curated_papers(kind: str, start: str, end: str, papers: list[tuple[str, str, str]], *, context: str, url: str) -> list[dict]:
    return [
        curated_row(kind, title, start, end, speaker=authors, context=context, url=url, paper_id=paper_id)
        for paper_id, title, authors in papers
    ]


MON_WS_01_URL = "https://www.mathworks.com/company/events/tradeshows/icra-2026-5136850.html"
MON_WS_03_URL = "https://cs.pomona.edu/~ajc/tutorials/simr-icra2026/"
MON_WS_04_URL = "https://tum-avs.github.io/ICRA2026_Workshop/"
MON_WS_04_PAPERS = [
    (
        "Paper 1",
        "V2V-GoT: Vehicle-to-Vehicle Cooperative Autonomous Driving with Multimodal Large Language Models and Graph-of-Thoughts",
        "Hsu-kuang Chiu, Ryo Hachiuma, Chien-Yi Wang, Yu-Chiang Frank Wang, Min-Hung Chen, Stephen F Smith",
    ),
    (
        "Paper 2",
        "Perceptual Motor Learning for Zero-Shot Generalization in Autonomous Lateral Control",
        "Elahe Delavari, John Moore, Junho Hong, Jaerock Kwon",
    ),
    (
        "Paper 3",
        "Scene2DENM: End-to-End DENM Generation from Traffic Video for Cooperative Autonomous Driving",
        "Kailin Tong, Baoyun Wang, Kanan Mujkic, Christoph Pilz, Xingcheng Zhou, Selim Solmaz, Jelena Rubesa-Zrim, Daniel Watzenig, Arno Eichberger, Bo Leng",
    ),
    (
        "Paper 4",
        "Semantic-Aware Hierarchical 3D Gaussian Representation for Autonomous Driving Simulation",
        "Chao Li, Chenpeng Yao, Chengju Liu, Qijun Chen",
    ),
    (
        "Paper 5",
        "Collaborative Multi-Agent Testing for Emergent Failure Discovery in Autonomous Driving Systems",
        "Ruizhen Gu, Konstantinos Koufos, Donghwan Shin, Vahid Garousi, Mehrdad Dianati",
    ),
    (
        "Paper 6",
        "AD4AD: Benchmarking Visual Anomaly Detection Models for Safer Autonomous Driving",
        "Fabrizio Genilotti, Gionata Grotto, Arianna Stropeni, Manuel Barusco, Francesco Borsatti, Davide Dalle Pezze, Gian Antonio Susto",
    ),
    (
        "Paper 7",
        "DOPE: Dynamic Obstacle Parking Environment for End-to-End Autonomous Parking in CARLA",
        "Min Hee Jo, Christian Juette, Alexey Vinel",
    ),
    (
        "Paper 8",
        "Towards Multi-Object-Tracking with Radar on a Fast Moving Vehicle: On the Potential of Processing Radar in the Frequency Domain",
        "Tim Hansen, Arturo Gomez Chavez, Ilya Shimchik, Andreas Birk",
    ),
    (
        "Paper 9",
        "Radar-Informed 3D Multi-Object Tracking under Adverse Conditions",
        "Bingxue Xu, Emil Hedemalm, Ajinkya Khoche, Patric Jensfelt",
    ),
    (
        "Paper 10",
        "SimForge: Generalization in Autonomous Driving through City-Scale Digital Twins and Simulation",
        "Sourang Sri hari, Dibyendusekhar Goswami, Michael Vu, Anuj Gupta, Mayank Gupta, Abhishek Shinde, Ayush Gupta",
    ),
    (
        "Paper 11",
        "Lane-Topology-Guided Motion Forecasting via Feasible Motion Primitive Selection",
        "Sangjin Han, Hoseong Jung, Jeongtae Her, Changhyun Choi, H. Jin Kim",
    ),
    (
        "Paper 12",
        "Towards a Fully Differentiable Framework for Autonomous Driving Based on Model-Structured Neural Networks",
        "Sabrina Ciuffoletti, Gioele DEFRANCESCO, Giovanni Scialla, Mattia Piazza, Sebastiano Taddei, Gastone Pietro Rosati Papini",
    ),
    (
        "Paper 13",
        "Self-Paced Curriculum Reinforcement Learning for Autonomous Superbike Racing in Simulation",
        "Luca Ghisi, Jacopo Essenziale, Carlo D'Eramo, Matteo Luperto",
    ),
]
MON_WS_04_SPOTLIGHT_PAPERS = [MON_WS_04_PAPERS[i] for i in [0, 5, 8, 10, 12]]

MON_WS_05_URL = "https://mobile-robotics-hub.github.io/workshop2026/"
MON_WS_05_SESSION_A = [
    ("P01", "When to Map? Adaptive Switching Between Localization and SLAM in Multi-Session Systems", ""),
    ("P02", "Frequency-Preserved Logit Distillation for Long-term Robot Perception", ""),
    ("P03", "Continual Online Backward-Compatible Learning for LiDAR Place Recognition in Adverse Weather", ""),
    ("P04", "Deep Multi-Agent Reinforcement Learning for Multi-Robot Social Navigation in Constrained Environments", ""),
    ("P05", "Edge Radar Material Classification Under Geometry Shifts", ""),
    ("P06", "In-context Adaptation of Place Recognition through Self-supervised Learning from Video", ""),
    ("P07", "Toward Embedded Vision-Language Perception for Long-Term Autonomous Robots via Training-Free Token Pruning", ""),
    ("P08", "GPU-Accelerated Semantic Embedded SLAM", ""),
    ("P09", "ROS 2 Implementation of Appearance-based Visual Teach and Repeat Navigation", ""),
    ("P10", "Multi-view 6D Pose Estimation of the Aerial Docking Device for Long-Term Drone Operation in Dynamic Environments", ""),
    ("P11", "COMPASS: Learning Global Spatial Context for Long-Range Robot Navigation", ""),
    ("P12", "Voxels: A Lightweight Simulation for Mobile Robotics", ""),
]
MON_WS_05_SESSION_B = [
    ("P13", "Overcoming Nature: Perception for Autonomous Navigation in Dense Vegetation", ""),
    ("P14", "Adaptive Gaussian Process-Based Sampling for Energy-Efficient Aquatic Sensing with Autonomous Surface Vessels", ""),
    ("P15", "Disturbance-Aware Underwater Visual-Inertial Odometry via Learned Dynamics and External Force Estimation", ""),
    ("P16", "VERTIFORMER: A Data-Efficient Multi-Task Transformer on Vertically Challenging Terrain", ""),
    ("P17", "Helhest: An Affordable and Resilient R&D Platform for Long-Term Autonomous Navigation in the Wild", ""),
    ("P18", "Towards 3D Karst Underwater Scene Reconstruction from Rotating Sonar Data", ""),
    ("P19", "An Open-Source LiDAR and Monocular Off-Road Autonomous Navigation Stack", ""),
    ("P20", "Building a Robust, Autonomous Pest-Control Vehicle for Real-World Agricultural Deployment", ""),
    ("P21", "State Corrected Predictive Preference Learning for Multimodal Robot Navigation on Uneven Terrain", ""),
    ("P22", "Vision-Language Modeling for Natural-Language Wheel Loader Assistance in Unstructured Construction Environments", ""),
    ("P23", "Energy-Aware NECO for Single-Pass Pixel-wise Out-of-Distribution Detection in Semantic Segmentation", ""),
    ("P24", "Extending Operational Mission Lifetimes of Free-Flying Space Robots via Hypernetwork-Based Multi-Task GNC Controller", ""),
]

MON_WS_06_URL = "https://sites.google.com/view/icra2026-workshop-robot-ethics/home/programme"
MON_WS_06_PAPER_TEASERS = [
    (
        "",
        "Clinicians' Perspectives on Safety, Ethical, and Legal Considerations for Home-Based Physical Rehabilitation Robots",
        "Vignesh Velmurugan and Farshid Amirabdollahian",
    ),
    (
        "",
        "Resilience Meets Autonomy: Governing Embodied AI in Critical Infrastructure",
        "Puneet Sharma and Christer Henrik Pursiainen",
    ),
    (
        "",
        "When Is It Ethical to Engineer Success? Calibrated Self-Efficacy Support for Assistive Robotics",
        "Tetsunari Inamura",
    ),
    ("", "Towards Responsible Verbal Guidance in Human-Robot Interaction", "Sinem Görmez"),
    (
        "",
        "The EU-OSHA online risk assessment (OiRA) tool for the automation of tasks: Human-centered deployment of AI-based systems and advanced robotics",
        "Patricia Helen Rosen and Sascha Wischniewski",
    ),
    (
        "",
        "Beneficent Intelligence as a Pluralistic Multi-Objective Framework for Robot Ethics",
        "Gaurav Dixit, Russell Perkins, Paul Robinette and Kagan Tumer",
    ),
    (
        "",
        "From Traffic Laws to Ethical Robot Behaviour: LLM-Augmented Formal Compliance Monitoring for Robot Assistants in Human Environments",
        "Kumar Manas",
    ),
    (
        "",
        "The Ethics of Humanoid Robots: A Functional Turn from Anthropomorphic Appearance to Affordance",
        "Arzu Formánek",
    ),
]
MON_WS_06_SESSION_2_PAPERS = [
    (
        "",
        "A Roadmap for Responsible Robotics",
        "Masoumeh Mansouri, Michael Milford, Raja Chatila, Séverin Lemaignan, Thomas M. Powers, Martin Magnusson, Nico Hochgeschwender et al",
    ),
    (
        "",
        "Consent Chain Degradation in Embodied Multi-Agent Systems: Bridging the Gap Between AI Agent Governance and Robot Ethics",
        "Mehmet Haklidir",
    ),
    ("", "Silicopathy: Generating Ethics through Pain in Embodied Artificial Systems", "Minoru Asada"),
    (
        "",
        "Fostering Public Acceptance of Robotics Through Inclusive HRI Experiments: An Empirical Study",
        "Gizem Ates Venås, Martin Fodstad Stølen and Erik Kyrkjebo",
    ),
]
MON_WS_06_SESSION_3_PAPERS = [
    (
        "",
        "The Ontological Incompatibility of Moral Benchmarking: Deconstruction of the Trolley Dilemma's Failure in Large Language Models through Kohlbergian Structuralism",
        "Cristina Brasi, Beatrice Seccomandi and Filippo Sanfilippo",
    ),
    ("", "Between Assistance and Manipulation: Towards an Ethical Framework for Emotion-Aware XAI", "Carolin Klute"),
    (
        "",
        "REBAR: Reference Ethical Benchmark for Autonomy Readiness",
        "Jonathan Diller, David Barnes, Rebekah Bogdanoff, Rhett Collier, Roddy Collins, Keith Fieldhouse, Yonatan Gefen, Cameron Johnson, Anuriha Kodali, Brad Kriel, Varun Murali, James Niehaus, Mish Sukharev, Joseph Vanpelt, Anthony Hoogs, Vijay Kumar and Arslan Basharat",
    ),
]
MON_WS_06_ALL_POSTERS = MON_WS_06_PAPER_TEASERS + MON_WS_06_SESSION_2_PAPERS + MON_WS_06_SESSION_3_PAPERS

MON_WS_08_URL = "https://rose-workshops.github.io/rose2026/"
MON_WS_10_URL = "https://active-perception-workshop.github.io/"
MON_WS_13_URL = "https://sites.google.com/unisi.it/human-augmentation/home-page"
MON_WS_15_URL = "https://www.profactor.at/en/events-en/circular-robotics-designing-sustainable-autonomy-for-a-finite-world-icra-2026-workshop/"
MON_WS_15_POSTERS = [
    ("", "Smart Robots Unlocking Hard-to-Reach Materials", "Dr Nabil Shaukat, School of Mechanical Engineering, University of Leeds, United Kingdom"),
    ("", "Robotic disassembly of EOL products / Automated EoL Triage", "Yongjing Wang, University of Birmingham, UK"),
    ("", "Robotic cell for unscrewing", "Pedro Dias, INESC TEC, Portugal"),
    ("", "Forrest: Bio-Inspired Design for Energy-Efficient, Long-Duration Humanoid Locomotion", "Pilar Gil, RoboTUM, Germany"),
]
MON_WS_15_PITCHES = [
    (
        "",
        "Towards Design-for-Robotics: Disassemblability-Driven Non-Destructive Disassembly of Home Appliances",
        "Takuya Kiyokawa, The University of Osaka, Japan",
    ),
    (
        "",
        "Circular Robotics through Design-for-Robotic-Disassembly and AI-Enhanced Perception",
        "Claudio Roberto Gaz, Kingston University London, United Kingdom",
    ),
    ("", "PEPR O2R Material, Architecture and Embodied Intelligence", "Quentin Peyron, Inria/Defrost, France"),
    (
        "",
        "LABUST initiatives in sustainability of robots and robots for sustainability",
        "Fausto Ferreira, University of Zagreb Faculty of Electrical Engineering and Computing, Croatia",
    ),
    ("", "SHEREC: Safe, Healthy and Environmental Ship Recycling", "Evren Samur, Bogazici University / HKTM, Turkiye"),
    ("", "Robotics eco label roboticsecolabel.com", "Bram Vanderborght, Vrije Universiteit Brussel and imec, Belgium"),
    ("", "IEEE U-RAS course on Sustainability Robotics", "Barbara Mazzolai, Italian Institute of Technology, Italy"),
]
MON_WS_17_URL = "https://dex-manipulation.github.io/icra2026/"
MON_WS_18_URL = "https://sites.google.com/view/embodied-ai-icra-26/"
MON_WS_19_URL = "https://sites.google.com/view/icra-2026-s2s-perception/"
MON_WS_20_URL = "https://awesomedigitaltwin.github.io/2026_ICRA.html"
MON_WS_21_URL = "https://www.ellipsis-venture.com/icra2026/"
MON_WS_23_URL = "https://xingxingzuo.github.io/MM-SpatialAI/"
MON_WS_24_URL = "http://www.mananlab.tech/workshop"
MON_WS_26_URL = "https://icra2026rm.github.io/schedule"
MON_WS_27_URL = "https://icra2026-rigorous-perception.github.io/"
MON_WS_28_URL = "https://sites.google.com/view/radar-robotics/"
MON_WS_31_URL = "https://sites.google.com/view/icra-2026-sdrl-workshop"
MON_WS_32_URL = "https://alejandrofontan.github.io/The-Good-Reviewer-ICRA26/"
MON_WS_33_URL = "https://sites.google.com/view/sft-front"
MON_WS_34_URL = "https://large-area-tactile-sensing.github.io/"
MON_WS_35_URL = "https://shanluo.github.io/ViTacWorkshops/vitac2026"
MON_WS_36_URL = "https://agrifoodroboticsworkshop.wordpress.com/schedule-icra2027/"
MON_WS_37_URL = "https://sites.google.com/robotics.utias.utoronto.ca/icra26-frontiers-optimization/schedule"
MON_WS_38_URL = "https://workshop-pbp2026.github.io/"
MON_WS_39_URL = "https://rl4il-icra.github.io/"
MON_WS_40_URL = "https://cr2-icra.github.io"

MON_WS_23_POSTER_1 = [
    ("", "LAPS: Improving Incremental LiDAR Mapping using Active Pooling and Sampling for Neural Distance Fields", ""),
    ("", "FlowHOI: Flow-based Semantics-Grounded Generation of Hand-Object Interactions for Dexterous Robot Manipulation", ""),
    ("", "Memory Over Maps: 3D Object Localization Without Reconstruction", ""),
    ("", "Online Adaptive Learning for Robust LiDAR Perception in High-Performance Autonomous Racing", ""),
    ("", "GaussianPrimitive: Shaping 3D Gaussian Primitives with Surface Mesh Constraints for Physically Plausible Simulation and Rendering", ""),
    ("", "RIMM: Multimodal Pre-training for Mobile Manipulation in the Wild", ""),
    ("", "MR-SLAM: Immersive Spatial Supervision for Multi-Robot Mapping via Mixed Reality", ""),
    ("", "Mind the Domain Gap: Multi-Modal Fusion for Off-road Navigation and Scene Understanding", ""),
]
MON_WS_23_POSTER_2 = [
    ("", "Exploring Bottlenecks in VLM-LLM Navigation: How 3D Scene Understanding Capability Impacts Zero-Shot VLN", ""),
    ("", "Efficient Feature-Free Initialization for Monocular Visual-Inertial Systems Using A Feed-Forward 3D Model", ""),
    ("", "DCReg: Decoupled Characterization for Efficient Degenerate LiDAR Registration", ""),
    ("", "RADIO-ViPE: Online Tightly Coupled Multi-Modal Fusion for Open-Vocabulary Semantic SLAM in Dynamic Environments", ""),
    ("", "VLA-RAIL: A Real-Time Asynchronous Inference Linker for VLA Models and Robots", ""),
    ("", "4D Latent Mapping for Mobile Manipulation Policy Learning", ""),
    ("", "FunFact: Building Probabilistic Functional 3D Scene Graphs via Factor-Graph Reasoning", ""),
    ("", "Pose-Anchored and Scale-Consistent Dense Mapping with Geometric Priors", ""),
    ("", "Domain Shift-Aware Training-Free Adaptation for Open-Vocabulary Segmentation in Robotic Perception", ""),
    ("", "ScanNet-SG: A Large-Scale Dataset for 3D Scene Graph Alignment", ""),
    ("", "Object-Level Change Detection via Semantic Correspondences Association in Multi-Session Mapping", ""),
    ("", "UniFField: A Generalizable Unified Neural Feature Field for Visual, Semantic, and Spatial Uncertainties in Any Scene", ""),
    ("", "Metric-Semantic Primitive Matching for Cross-View Robot Localization Beyond Urban Environments", ""),
]

MON_WS_27_NAV_POSTERS = [
    ("", "Perception Debt: Monitoring Safety-Margin Consumption in Embodied Autonomy", "Stavan Dholakia, Abhishek Singh, Aditya Gazta, Shivani Shukla"),
    ("", "One-Step Planner: Unified Observation and Decision-Making with Vision-Language Models", "Youngjae Yoo, Jae-Woo Choi, DohyungKim, Byoung-Tak Zhang"),
    ("", "COIN-BIEVR: 3D Intensity Mapping for Robust LiDAR-Inertial Odometry", "Patrick Pfreundschuh, Cedric Le Gentil, Roland Siegwart, Cesar Cadena"),
    ("", "In-context adaptation of place recognition through self-supervised learning from video", "Kiavash Jamshidi, Hermann Blum, Gülhan Şikaroğlu"),
    ("", "Language-Based Swarm Perception: Decentralized Person Re-Identification via Natural Language Descriptions", "Miquel Kegeleirs, Lorenzo Garattoni, Gianpiero Francesca, Mauro Birattari"),
    ("", "Extended Abstract: Adaptive LiDAR Inertial Odometry with an Ellipsoid Representation (EllipseLIO)", "Rowan Border, Margarita Chli"),
    ("", "Cross-Modal Benchmarking for Robotic Perception in Natural Environments", "David Hall, Joshua Knights, Mark Cox, Peyman Moghadam"),
    ("", "SUPER -- A Framework for Sensitivity-based Uncertainty-aware Performance and Risk Assessment in Visual Inertial Odometry", "Johannes A. Gaus, Daniel Haeufle, Woo-Jeong Baek"),
    ("", "Visual Layer Selection Matters for Egocentric VLM Perception", "Ruchen Liu, Yi Yang, Yiming Xu, Monika Sester, Bodo Rosenhahn"),
    ("", "Lensless Aerial Navigation in Dark", "Deepak Singh, Hudson Kortus, Jahnavi Prudhivi, Vivek Reddy Kasireddy, Nitin J. Sanket"),
    ("", "Spatially Stratified Distillation for Heterogeneous Radar Place Recognition", "Sagun Man Singh Shrestha, Abdelwahed Khamis, Saimunur Rahman, Peyman Moghadam"),
]
MON_WS_27_MANIP_POSTERS = [
    ("", "GroundedPlanBench: Spatially Grounded Long-Horizon Task Planning", "Sehun Jung, Hyunjee Song, Dong-Hee Kim, Reuben Tan, Jianfeng Gao, Yong Jae Lee, Donghyun Kim"),
    ("", "GAP: Geometric Anchor Pre-training for Data-Efficient Visuomotor Learning of Manipulation Tasks", "Davide Buoso, Andrea Protopapa, Stefano Di Carlo, Francesca Pistilli, Giuseppe Averta"),
    ("", "Input-Aware Routing of Image-to-3D Models for Robotic Manipulation", "Akash Anand, Aditya Agarwal, Leslie Pack Kaelbling"),
    ("", "Robust Pose Estimation through Failure Explanation and Mitigation", "Loris Schneider, Yitian Shi, Rosa Wolf, Carolin Brenner, Rudolph Triebel, Rania Rayyes"),
    ("", "Core-Agnostic Compliance Perception for Rigid-Deformable Coupled Objects using Vision-Based Tactile Sensing", "CanZhao, Yanghui Ding, Haonan Zhao, Yebao Hu, Daolin Ma"),
    ("", "U-VINDO: Underwater Visual-Inertial Odometry Enhanced with Robot Dynamics Predictions Powered by Port-Hamiltonian Neural ODE Networks", "Yazan Maalla, Sergey Kolyubin, Zein Alabedeen Barhoum"),
    ("", "Training-Free 6D Robot Pose Estimation with Neural Memory Objects", "Sebastian Jung, Leonard Klüpfel, Tjark Darius, Rudolph Triebel, Maximilian Durner"),
    ("", "Task-Relevant Depth Quality Metrics for Suction Grasping", "Shivansh Inamdar"),
    ("", "Compositional Neural Field Movement Primitives", "Ahmet Ercan Tekden, Yasemin Bekiroglu"),
    ("", "OSMa-Bench++: Toward Open-Ended Benchmarking of Semantic Mapping for Manipulation with Prompt-Generated Synthetic Scenes", "Regina Kurkova, Maxim Popov, Sergey Kolyubin"),
    ("", "IFG: Internet-Scale Guidance for Functional Grasping Generation", "Muxin Liu, Mingxuan Li, Kenneth Shaw, Deepak Pathak"),
    ("", "EVII: Measuring Early Visual Integration in VLM Reasoning", "Hakan Muluk, Ozgur S. Oguz"),
]

MON_WS_28_POSTERS = [
    ("", "Learning Spatial Structure from Pre-Beamforming Per-Antenna Range-Doppler Radar Data via Visibility-Aware Cross-Modal Supervision", ""),
    ("", "RAGE-XY: RADAR-Aided Longitudinal and Lateral Forces Estimation For Autonomous Race Cars", ""),
    ("", "Radar Radiance Fields", ""),
    ("", "The Viking Hill Dataset: Lidar-Radar-Camera Benchmark for Forest Scene Segmentation", ""),
    ("", "Towards Multi-Object-Tracking with Radar on a Fast Moving Vehicle: On the Potential of Processing Radar in the Frequency Domain", ""),
    ("", "Radar-Camera BEV Multi-Task Learning with Cross-Task Attention Bridge for Joint 3D Detection and Segmentation", ""),
    ("", "3DRO: Lidar-level SE(3) Direct Radar Odometry Using a 2D Imaging Radar and a Gyroscope", ""),
    ("", "Radar-Informed 3D Multi-Object Tracking under Adverse Conditions", ""),
    ("", "UNRIO: Uncertainty-Aware Velocity Learning for Radar-Inertial Odometry", ""),
    ("", "VLMaterial: Vision-Language Model-Based Camera-Radar Fusion for Physics-Grounded Material Identification", ""),
    ("", "Radar-Language-Model Captioning for Weather-Robust Scene Understanding", ""),
    ("", "Graph Theoretical Outlier Rejection for 4D Radar Registration in Feature-Poor Environments", ""),
    ("", "Towards Geometric and RCS Gaussian Modeling and Scan Matching for 4D Radar-Inertial Odometry", ""),
    ("", "SAGD: Structure-Aware Global Descriptor for Automotive Spinning Radar Place Recognition", ""),
    ("", "Radar Odometry Subject to High Tilt Dynamics of Subarctic Environments", ""),
]

MON_WS_31_PAPERS = [
    ("", "Learning on the Fly: Rapid Policy Adaptation via Differentiable Simulation.", "Jiahe Pan, Jiaxu Xing, Rudolf Reiter, Yifan Zhai, Elie Aljalbout, Davide Scaramuzza"),
    ("", "SplineTM: B-Spline Tire Modeling for Autonomous Racing.", "Piotr Kicki, Jan Węgrzynowski, Grzegorz Czechmanowski, Krzysztof Walas"),
    ("", "Learning Geometry-Aware Nonprehensile Pushing and Pulling with Dexterous Hands.", "Yunshuang Li, Yiyang Ling, Gaurav S. Sukhatme, Daniel Seita"),
    ("", "CRAFT: Video Diffusion for Bimanual Robot Data Generation.", "Jason Chen, I-Chun Arthur Liu, Gaurav S. Sukhatme, Daniel Seita"),
    ("", "Vid2Sid: Videos Can Help Close the Sim2Real Gap.", "Kevin Qiu, Yu Zhang, Marek Cygan, Josie Hughes"),
    ("", "Swim2Real: VLM-Guided System Identification for Sim-to-Real Transfer.", "Kevin Qiu, Kyle L. Walker, Mike Yan Michelis, Marek Cygan, Josie Hughes"),
    ("", "Whole-Body Mobile Manipulation using Offline Reinforcement Learning on Sub-optimal Controllers.", "Snehal Jauhri, Vignesh Prasad, Georgia Chalvatzaki"),
    ("", "Point Bridge: 3D Representations for Cross Domain Policy Learning.", "Siddhant Haldar, Lars Johannsmeier, Lerrel Pinto, Abhishek Gupta, Dieter Fox, Yashraj Narang, Ajay Mandlekar"),
    ("", "IFG: Internet-Scale Guidance for Functional Grasping Generation.", "Muxin Liu, Mingxuan Li, Kenneth Shaw, Deepak Pathak"),
    ("", "SynthLA: Synthetic Language--Action Policies for Zero-shot Real-world Manipulation via Structured Perception.", "Marco Maccarini, Angelo Moroncelli, Asad Ali Shahid, Samuele Mara, Mirko Nava, Loris Roveda"),
    ("", "MolmoB0T: Large-Scale Simulation Enables Zero-Shot Manipulation.", "Abhay Deshpande, Maya Guru, Rose Hendrix, Snehal Jauhri, Ainaz Eftekhar, Rohun Tripathi, Max Argus, Jordi Salvador, Haoquan Fang, Matthew Wallingford, Wilbert Pumacay, Yejin Kim, Quinn Pfeifer, Ying-Chun Lee, Piper Wolters, Omar Rayyan, Mingtong Zhang, Jiafei Duan, Karen Farley, Winson Han, Eli VanderBilt, Dieter Fox, Ali Farhadi, Georgia Chalvatzaki, Dhruv Shah, Ranjay Krishna"),
    ("", "FLASH: Fast Learning via GPU-Accelerated Simulation for High-Fidelity Deformable Manipulation in Minutes.", "Siyuan Luo, Bingyang Zhou, Xin Liu, Zhenhao Huang, Gang Yang, Jason Pho, Ziqiu Zeng, Fan Shi"),
    ("", "RF-DROPO: Data-Efficient Adaptive Domain Randomization for Zero-Shot Sim-to-Real Transfer in Soft Robotics.", "Andrea Protopapa, Gabriele Tiboni, Tatiana Tommasi, Raffaello Camoriano, Christian Duriez, Giuseppe Averta"),
    ("", "Dream to Fly: Model-Based Reinforcement Learning for Vision-Based Drone Flight.", "Angel Romero, Ashwin Shenai, Ismail Geles, Elie Aljalbout, Davide Scaramuzza"),
    ("", "Physically Consistent Humanoid Loco-Manipulation using Latent Diffusion Models.", "Ilyass Taouil, Haizhou Zhao, Angela Dai, Majid Khadiv"),
    ("", "Breaking the 3D Dataset Bottleneck: Fast Scalable Generation of Aligned 3D Assets from Scratch for Category 6D Pose Estimation and Robotic Grasping.", "Guillaume Duret, Danylo Mazurak, Florence Zara, Liming Chen, Jan Peters"),
    ("", "Emergent Dexterity Via Diverse Resets and Large-Scale Reinforcement Learning.", "Patrick Yin, Tyler Westenbroek, Zhengyu Zhang, Joshua Tran, Ignacio Dagnino, Eeshani Shilamkar, Numfor Mbiziwo-Tiapo, Simran Bagaria, Xinlei Liu, Galen Mullins, Andrey Kolobov, Abhishek Gupta"),
    ("", "HumanoidMimicGen: Data Generation for Loco-Manipulation via Whole-Body Planning and Adaptation.", "Kevin Lin, Ajay Mandlekar, Caelan Reed Garrett, Nikita Chernyadev, Yu Fang, Runyu Ding, Yuqi Xie, Linxi Fan, Yuke Zhu"),
]

MON_WS_35_PAPERS = [
    ("", "NeuralTouch: Leveraging Implicit Neural Descriptor for Precise Sim-to-Real Tactile Robot Control", "Yijiong Lin, chenghua lu, Max Yang, Efi Psomopoulou, Nathan F. Lepora"),
    ("", "TactileLab: Efficient Shear-Sensitive Tactile Simulation for Dexterous Sim2Real Robotic Manipulation", "Yijiong Lin, Nathan F. Lepora"),
    ("", "roto 2.0: The Robot Tactile Olympiad", "Elle Miller, Jayaram Reddy, Ayush Deshmukh, Trevor McInroe, David Abel, Oisin Mac Aodha, Sethu Vijayakumar"),
    ("", "Automatic Physically-Based Sim2Real for Tactile Images through Differentiable Path-Tracing Rendering", "Guillaume Duret, Anna Samsonenko, Florence Zara, Jan Peters, Liming Chen"),
    ("", "Real-Time Simulation of Deformable Tactile Sensors and Objects in Robotic Grasping using Graph Neural Networks with Inductive Biases", "Guillaume Duret, Frederik Heller, Danylo Mazurak, Tim Schneider, Alap Kshirsagar, Florence Zara, Jan Peters, Liming Chen"),
    ("", "3D deformable surface reconstruction from visual and tactile input with geometric prior", "Ioan Laurentiu Popa, Tudor Brezae, Paul-Stelian Sucală, Konievic Robert-Anton, Levente Tamas"),
    ("", "GelSLAM: A Real-time, High-Fidelity, and Robust 3D Tactile SLAM System", "Hung-Jui Huang, Mohammad Amin Mirzaee, Michael Kaess, Wenzhen Yuan"),
    ("", "Characterisation of a Monolithic 3D-printed Tactile Sensor Using an SSIM-based Analysis", "XIAOQING GUO, Nathan F. Lepora, Efi Psomopoulou"),
    ("", "Grip force regulation via low-dimensional incipient slip estimation", "Laurence Willemet, Giuseppe Vitrani, Michael Wiertlewski"),
    ("", "One-touch friction estimation at the onset of grasp", "Giuseppe Vitrani, Laurence Willemet, Michael Wiertlewski"),
    ("", "Nerves of Plastic: A Transparent Approach To Distributed Tactile Sensing for Safer Robots", "Laura E. Butcher, Christopher J. Ford, Nathan F. Lepora, Efi Psomopoulou"),
    ("", "Low-Cost Gating-Based Vision and Proprioception Fusion for Object Property Classification", "Jiaming Zhu, Kaitao Meng"),
    ("", "Development of a High-Speed Event Vision-Based Roller Tactile Sensor for Large-Surface Inspection", "Akram Khairi, Hussain Sajwani, Yahya Zweiri"),
    ("", "Training Tactile Sensors to Learn Force Sensing from Each Other", "Zhuo Chen, Nathan Lepora, Lorenzo Jamone, Jiankang Deng, Shan Luo"),
    ("", "Learning Heterogeneous Tactile Representations with Graph Neural Networks for Dexterous Manipulation", "Tai Yamada, Satoshi Funabashi, Steven Oh, Pranav Ponnivalavan, Kazutaka Omori, Tetsuya Ogata, Shigeki SUGANO"),
    ("", "ViTacGen: Robotic Pushing with Vision-to-Touch Generation", "Zhiyuan Wu, Shan Luo"),
    ("", "ViTac-Tracing: Visual-Tactile Imitation Learning of Deformable Object Tracing", "Yongqiang Zhao, Shan Luo"),
    ("", "UniVTAC: A Unified Simulation Platform for Visuo-Tactile Manipulation Data Generation, Learning, and Benchmarking", "Baijun Chen, Weijie Wan, Tianxing Chen, Xianda Guo, Congsheng Xu, Yuanyang Qi, Haojie Zhang, Longyan Wu, Tianling Xu, Zixuan Li, Yizhe Wu, Rui Li, Xiaokang Yang, Ping Luo, Wei Sui, Yao Mu"),
    ("", "EleTac: Pneumatic Elephant Trunk-Inspired Soft Gripper with Vision-Based Tactile Sensing", "Tuan Tai Nguyen, Xuyang Zhang, Quan Khanh Luu, Shan Luo, Van Ho"),
]
MON_WS_35_BEST_PAPERS = [
    ("", "EleTac: Pneumatic Elephant Trunk-Inspired Soft Gripper with Vision-Based Tactile Sensing", "Tuan Tai Nguyen"),
    ("", "UniVTAC: A Unified Simulation Platform for Visuo-Tactile Manipulation Data Generation, Learning, and Benchmarking", "Baijun Chen"),
    ("", "Training Tactile Sensors to Learn Force Sensing from Each Other", "Zhuo Chen"),
]

MON_WS_36_ORAL_1 = [
    ("", "Efficient View Planning Guided by Previous-Session Reconstruction for Repeated Plant Monitoring", "S. Pan, L. Lobefaro, M. Taherkhani, X. Huang, R. Menon, C. Stachniss, M. Bennewitz"),
    ("", "Fruit3DGS: a general Fruit counting and localization framework leveraging semantic-guided 3D Gaussian Splatting and contrastive learning", "S. Mara, A. Moroncelli, M. Maccarini, L. Roveda"),
    ("", "Autonomous Vineyard Exploration using LiDAR Sensing: A Field Evaluation on Legged-Wheeled Quadruped Robot", "S. Sweeney, H. Li, M. Ziegltrum, D. Koumoutsou, A. M. Delfaki, T. Padir, D. Kanoulas"),
]
MON_WS_36_POSTER_1 = [
    ("", "A cable-driven parallel robot for whitefly monitoring in greenhouses", "D. Martinez, L. Wurtz, P. Huertas, M. Elkairouh, M. Boutayeb, S. Viollet"),
    ("", "Harvest Complexity: A Metric for Evaluating Crop Architectures for Robotic Harvesting", "K. M. F. James, G. Cielniak"),
    ("", "Benchmarking Anomaly Detection for Agricultural Robots Using Proprioceptive Sensor Data", "J. Cox, E. Smith, I. Hroob, L. Guevara, M. Hanheide, G. Cielniak"),
    ("", "ChamDog: A Legged Mobile Manipulator for Autonomous Korean Melon Harvesting in Vertical Cultivation", "H. Park, S. Ha, D. Lee, D. Shin, C. H. Baek, M. Kang, H. K. Suh"),
    ("", "Attention can be almost all you need for semantic visual odometry in orchards", "T. T. Santos, D. Bharti, L. Gebler"),
    ("", "Autonomous Mechanical Control of the Brown Marmorated Stink Bug in Orchards", "L. Frering, G. Steinbauer-Wagner, M. Hartbauer"),
    ("", "Find the Fruit: Zero-Shot Sim2Real RL for Occlusion-Aware Plant Manipulation", "N. Subedi, H.-J. Yang, D. K. Jha, S. Sarkar"),
    ("", "TEMPO-VINE: A Multi-Temporal Sensor Fusion Dataset for Localization and Mapping in Vineyards", "M. Martini, M. Ambrosio, J. Vilella-Cantos, A. Navone, M. Chiaberge"),
    ("", "Loop closure grasping: Topological transformations enable strong, gentle, and versatile grasps", "K. Barhydt, O. G. Osele, S. Kodali, C. du Pasquier, C. M. Hartquist, H. Asada, A. Okamura"),
]
MON_WS_36_ORAL_2 = [
    ("", "AgriGen: Large-Scale Scene Generation Framework for Photorealistic Agricultural Robotics Simulation", "U. Bajpai, S. Tleiji, C. Pradalier, S. Aravecchia"),
    ("", "Efficient Domain-Specific Foundation Models for Agriculture: Beyond General-Purpose SSL Weights", "G. Flück, U. Govindarajan, C.-C. Fu, O. Denas, I. Susmelj"),
    ("", "SG-DOR: Learning Scene Graphs with Direction-Conditioned Occlusion Reasoning for Interventions in Dense Foliage", "R. Menon, N. Mueller-Goldingen, S. Pan, G. K. Chenchani, M. Bennewitz"),
]
MON_WS_36_POSTER_2 = [
    ("", "Middleware Matters: ROS 2 Dispatcher for Scalable Data Pipelines in Agri Robotics", "M. Krupka, K. Ćwian, A. Sopata, J. Pilarski, P. Skrzypczynski, R. Wrembel"),
    ("", "Biodiversity in the Loop: Can Field Robots Learn Ecologically Aware Management", "L. Troesken, D. A. Duecker"),
    ("", "Design and Comparative Evaluation of Scissor-Type Gripper Architectures for Precision Robotic Strawberry Harvesting", "S. E. Arnaud, G. Cielniak, S. A. Cardenas"),
    ("", "Calibration-Informative Region Selection for Online LiDAR-Camera Calibration in Agricultural Environments", "R. de Silva, G. Cielniak"),
    ("", "Droneulator: A Portable UAV Simulator for Agricultural Workflows with RotorPy and Godot 4", "J. Swindell, M. Lowen, M. Popovic, R. Polvara"),
    ("", "Simultaneous Tree Localization and Templating for Planning Arm Motion During Robotic Harvesting", "M. Rosette, J. Davidson"),
    ("", "Diffusion Policy for Tactile-Guided Reactive Manipulation in Cluttered Agricultural Environments", "N. H. Parayil, T. Peynot, C. Lehnert"),
    ("", "Autonomous Robotic Platform for Agricultural Robot Fleet Operations: System Design, Validation, and Field Deployment", "A. Deb, K. Kim, T. B. Free, D. Yang, W. R. Roach, P. A. Galiasso, D. Arnold, D. Cappelleri"),
    ("", "Language Enabled Hierarchical Scene Graphs For Precision Agriculture Autonomy", "A. Mukuddem, P. Amayo, A. Speed-Andrews"),
    ("", "An Annotation-to-Detection Framework for Autonomous and Robust Vine Trunk Localization in the Field by Mobile Agricultural Robots", "D. Chatziparaschis, E. Scudiero, B. Sams, K. Karydis"),
]

MON_WS_39_ORALS = [
    ("L1", "Robometer: Scaling General-Purpose Robotic Reward Models via Trajectory Comparisons", ""),
    ("L2", "SERNF: Sample-Efficient Real-World Dexterous Policy Fine-Tuning via Action-Chunked Critics and Normalizing Flows", ""),
    ("L3", "Simple Recipe Works: Vision-Language-Action Models are Natural Continual Learners with Reinforcement Learning", ""),
    ("L4", "Failure-Aware RL: Reliable Offline-to-Online Reinforcement Learning with Self-Recovery for Real-World Manipulation", ""),
    ("L5", "Mini Diffuser: Accelerating Diffusion Policy Optimization via Two-Level Minibatching", ""),
    ("L6", "KhGRL: Kernelized human-Guided Reinforcement Learning", ""),
    ("L7", "Online Planning with Offline Pretrained All-in-One World Model", ""),
    ("L8", "Teacher-Student Representational Alignment for Reinforcement Learning-driven Imitation Learning", ""),
    ("L9", "ReinforceGen: Hybrid Skill Policies with Automated Data Generation and Reinforcement Learning", ""),
    ("L10", "Turning the Dial: Bridging Behavior Cloning and Reinforcement Learning via Timestep Modulation", ""),
    ("L11", "Tune to Learn: How Controller Gains Shape Robot Policy Learning", ""),
]
MON_WS_39_POSTERS = [
    ("P1", "Critic Architecture Matters: Dual vs. Unified Critics for Humanoid Loco-Manipulation", ""),
    ("P2", "Negative Energy as Reward: Optimizing Beyond Demonstrations in Offline Goal-Conditioned Control", ""),
    ("P3", "Beyond Imitation: Reinforcement Learning-Based Sim-Real Co-Training for VLA Models", ""),
    ("P4", "Online Fine-Tuning of Pretrained Controllers for Autonomous Driving via Real-Time Recurrent RL", ""),
    ("P5", "Coherent Off-Policy Improvement of Large Behaviour Models with Learned Rewards", ""),
    ("P6", "Behavior Cloning of MPC for 3-DOF Robotic Manipulators", ""),
    ("P7", "Climb with SHERPA: Heuristic-Guided Reinforcement Learning via Segmented Experience Relay", ""),
    ("P8", "Can Agents Learn Safe Behavior From Non-Preferred Demonstrations?", ""),
    ("P9", "When Life Gives You BC, Make Q-functions: Extracting Q-values from Behavior Cloning for On-Robot Reinforcement Learning", ""),
    ("P10", "Imitation from Videos: Monocular 3D Motion Estimation for Agile Quadruped Locomotion", ""),
    ("P11", "Vision-Language-Action Jump-Starting for Reinforcement Learning Robotic Agents", ""),
    ("P12", "Hi-CoLA: High Confidence Lower Bound Approximation Based Reinforcement Learning for Flex-Route Transit Operation Control", ""),
    ("P13", "Graph-Based Reward Learning and Automatic Subtask Discovery for Long-Horizon Manipulation", ""),
]

MON_WS_40_POSTERS = [
    ("", "GPU-Accelerated Hydroelastic Contact via Signed Distance Fields", "Lennart Röstel, Yang Liu, Jessica Yin, Miguel Angel Zamora Mora, Miles Macklin, Philipp Reist, Tobias Widmer"),
    ("", "Compliant Sphere Lattice Contact: Distributed Contact Modeling for Sphere-Based Robot Representations", "Nataliya Nechyporenko, Ava Abderezaei, Alessandro Roncone"),
    ("", "Tune to Learn: How Controller Gains Shape Robot Policy Learning", "Antonia Bronars, Younghyo Park, Pulkit Agrawal"),
    ("", "Distributionally Robust Control via Stein Variational Inference for Contact-Rich Manipulation", "Hrishikesh Sathyanarayan, Victor Vantilborgh, Harish Ravichandar, Tom Lefebvre, Ian Abraham"),
    ("", "Don't Break the Egg: Branch-Rejoining Trajectory Optimization under Contact Timing Uncertainties", "Zhuocheng Zhang, Haizhou Zhao, Xudong Sun, Aaron M. Johnson, Majid Khadiv"),
    ("", "On Surprising Effects of Risk-Aware Domain Randomization for Contact-Rich Sampling-based Predictive Control", "Sergio Esteban, Junheng Li, Vince Kurtz, Aaron Ames"),
    ("", "Components of Contact: An Analysis of Contact-Implicit Control Strategies on Manipulation Primitives", "Grey Sarmiento, Michael Posa"),
    ("", "Self-Supervised Multisensory Pretraining for Contact-Rich Robot Reinforcement Learning", "Rickmer Krohn, Vignesh Prasad, Gabriele Tiboni, Georgia Chalvatzaki"),
    ("", "ImplicitRDP: An End-to-End Visual-Force Diffusion Policy with Structural Slow-Fast Learning", "Wendi Chen, Han Xue, Yi Wang, Fangyuan Zhou, Jun Lv, Yang Jin, Shirun Tang, Chuan Wen, Cewu Lu"),
    ("", "Contact-Rich Collaborative Path Clearing via Physics-Informed Corridor Search", "Zili Tang, Meng Guo, Zishao Qiao, Zongyuan Li"),
    ("", "Mind the Control Gap: Robot Learning from Mismatched Data", "Kushal Kedia"),
    ("", "Contact-Aware Probabilistic Reconstruction for Contact-Rich Manipulation", "Aditya Kamireddypalli, Joao Moura, Russell Buchanan, Matias Mattamala, Sethu Vijayakumar, Subramanian Ramamoorthy"),
    ("", "Curriculum-Aware Diffusion Distillation: Representing Contact Phases for Long-Horizon Door Manipulation", "Zhe Han, Bingjie Chen, Zihan Wang, Yizhe Li, Guoping Pan, Yi Cheng, Houde Liu"),
    ("", "Singulating an item from a pallet layer: Dual-arm manipulation with minimalistic end effectors by means of sampling-based MPC", "Pavlos Theodosiadis, Alessandro Saccon, Thiago D. Simão"),
    ("", "Learning Legged MPC with Smooth Neural Surrogates", "Samuel A. Moore, Easop Lee, Boyuan Chen"),
    ("", "STRIDE: Structured Lagrangian and Stochastic Residual Dynamics via Flow Matching", "Ganga Nair B, Prakrut Kotecha, Shishir Kolathaya"),
    ("", "Foot-terrain Deformation based Parameters Identification of MuJoCo for Simulation of Bipedal Locomotion on Deformable Terrain", "Sunil Gora, Ashish Dutta"),
    ("", "Novel Algorithms for Smoothly Differentiable and Efficiently Vectorizable Contact Manifold Construction", "Onur Beker, Andreas René Geist, Anselm Paulus, Georg Martius"),
    ("", "Predictive Safety Filters for Contact Rich Quadruped Locomotion", "Aditya Shirwatkar, Aaron M. Johnson, Majid Khadiv, Shishir Kolathaya"),
    ("", "Hierarchical Scene Graphs and Contact-Aware Behavior Trees for Learning and Executing Bimanual Manipulation", "Kumar Manas, Franziska Herbert"),
    ("", "Plasticity is Friction: Towards Controlling Deformables with Contact-Implicit MPC", "Bibit Bianchini, Michael Posa"),
    ("", "Learning Whole-Body Quadrupedal Pushing Across Geometry and Physics Variation", "Ebasa Temesgen, Dhyan Thakkar, Sarah Boelter, Greta Brown, Maria Gini"),
    ("", "Beyond Binary: Sim-to-Real Dexterous Manipulation with Physics-Aware Contact Representation", "Jiahe Pan, Stelian Coros, Jitendra Malik, Toru Lin"),
    ("", "Complementarity by Construction: A Lie-Group Approach to Solving Quadratic Programs with Linear Complementarity Constraints", "Arun L Bishop, Micah Reich, Zac Manchester"),
    ("", "Contact-Staged Stand-up Learning for Humanoids under Shifted Center-of-Mass Conditions", "Sangbeom Park, Seunghyun Lee, Dongkyu Lee"),
    ("", "Discovery of Dynamic Loco-manipulation Behaviors", "Michal Ciebielski, Haizhou Zhao, Aaron M. Johnson, Majid Khadiv"),
    ("", "Safe Contact-Rich Control under Smoothed Implicit Contact Dynamics", "Haegu Lee, Yitaek Kim, Christoffer Sloth"),
    ("", "PolyUMI: Visual + Auditory + Tactile Manipulation Platform for Imitation Learning", "Conor Wood Hayes"),
]

FRI_WS_41_URL = "https://mech.vub.ac.be/multibody/ICRA_conference_workshop.htm"
FRI_WS_42_URL = "https://robotmeetsranging.tech/"
FRI_WS_43_URL = "http://www.reproducibleroboticsresearch.org/20YearsBenchRRR_ICRA2026/program.html"
FRI_WS_44_URL = "https://www.cvl.iis.u-tokyo.ac.jp/EHR2026/index.php?id=workshop-program"
FRI_WS_45_URL = "https://sites.google.com/andrew.cmu.edu/icra26-3rd-unconv-robots/home"
FRI_WS_46_URL = "https://neurodesign-in-hri.webflow.io/"
FRI_WS_47_URL = "https://motionpredictionicra2026.github.io/"
FRI_WS_48_URL = "https://angelafiska.github.io/icra2026-automation-workshop/"
FRI_WS_49_URL = "https://aerial-robotics-workshop-icra.com/agenda/"
FRI_WS_50_URL = "https://icra-beyond-teleop.github.io/"
FRI_WS_51_URL = "https://sites.google.com/vt.edu/icra2026-learning-hri/"
FRI_WS_52_URL = "https://nfr-icra2026.com/"
FRI_WS_53_URL = "https://sites.google.com/view/icra26-workshop-medical-robot"
FRI_WS_54_URL = "https://sites.google.com/unisi.it/ws-extrememanipulation"
FRI_WS_55_URL = "https://sites.google.com/view/robotic-for-aging-society/homepage/"
FRI_WS_56_URL = "https://norlab-ulaval.github.io/icra_workshop_field_robotics/"
FRI_WS_57_URL = "https://icra2026vlapipeline.github.io/"
FRI_WS_58_URL = "https://geometric-robotics.github.io/icra-2026-workshop/"
FRI_WS_60_URL = "https://sites.google.com/view/humaninspiredrobotmanipulation"
FRI_WS_61_URL = "https://ariamhub.com/ocraim/"
FRI_WS_62_URL = "https://icra2026-planetary-robotics.github.io/"
FRI_WS_63_URL = "https://sites.google.com/view/react-climate-robotics/program"
FRI_WS_64_URL = "https://sites.google.com/view/roboarch-icra26/schedule"
FRI_WS_65_URL = "https://www.robotac.eu/robotac-2026"
FRI_WS_66_URL = "https://sites.google.com/view/icra2026-priors-map-workshop/program"
FRI_WS_67_URL = "https://mars-eai.github.io/ICRA-SCI-MARS-Webpage/"
FRI_WS_68_URL = "https://www.dynsyslab.org/icra2026-workshop-on-semantics-for-reliable-robot-autonomy/"
FRI_WS_70_URL = "https://sites.google.com/view/tailored-to-move-icra2026/schedule"
FRI_WS_71_URL = "https://uncertainty-in-robotics.github.io/program.html"
FRI_WS_72_URL = "https://space-robotics-workshop.github.io/"
FRI_WS_73_URL = "https://manipulation-robustness.github.io/icra2026/"
FRI_WS_74_URL = "https://xplore-workshop.github.io/schedule/"

FRI_WS_42_PAPERS = [
    ("", "Simultaneous Localization and Calibration (SLAC) for Cooperative Radio Navigation: When Is It Possible?", ""),
    ("", "UWB Multi Way Ranging by Relative Clock Rate Estimation and Decentralized Time Division Multiple Access (MWR-RCRE-DTDMA)", ""),
    ("", "Loosely Coupled Factor Graph Optimization for Pseudolite-Augmented Navigation", ""),
    ("", "Enhancing Graph-Based SLAM in GNSS-Denied environments by leveraging leg odometry", ""),
    ("", "SignalSkymask: Skymask Reconstruction Using Only GNSS Information via Deep Learning in Urban Canyons", ""),
    ("", "LSTM-Based Quantile Regression for Dynamic Zenith Wet Delay Overbounding", ""),
    ("", "Towards Absolute Accuracy Evaluation of RTK-SLAM in GNSS-Degraded Environments", ""),
    ("", "You Only Train Once: Deep Metric Learning Framework for Fingerprint-based Indoor Position Recognition", ""),
    ("", "SHT-V2X: A Real-World Dataset and Benchmark for V2X-Assisted Vehicle Localisation in GNSS-Denied Tunnels", ""),
    ("", "Route-Constrained Robust Fusion Estimation for MEMS/GNSS Integrated Navigation of Unmanned Ground Vehicles in GNSS Degraded Environments", ""),
    ("", "Towards Cooperative OSA Systems: Multilateration of WiFi FTM Responders Using GNSS References", ""),
    ("", "A Decentralized LiDAR-SLAM System with Certifiably Optimal Pose Graph Optimization", ""),
    ("", "GNSS-ROS-Standardization: An Open-Source Universal Bridge for GNSS Raw Observations in ROS2", ""),
    ("", "Bridging the Indoor-Outdoor Gap: Cross-Technology Ranging for Seamless Robot Navigation", ""),
    ("", "UWBPX4Sim: Ultra-Wideband simulator for multi-robot applications", ""),
    ("", "Feature-Level Geometric LiDAR--Visual--Inertial Odometry for Seamless Autonomy", ""),
    ("", "Trajectory Alignment for Robust Global Localization in GNSS-Degraded Forest Environments", ""),
    ("", "Interpreting RTK-Fixed Reliability via Sampling-Based Posterior Distribution of Integer Ambiguities", ""),
    ("", "UWB Meets Crazyflow: Simulating Degraded Feedback at Scale for Aerial Robotics", ""),
    ("", "Outdoor Ground Truth Estimation via GNSS Carrier Phase and Factor Graph Optimization", ""),
]

FRI_WS_52_PAPERS = [
    ("", "Neuromorphic Monocular Depth Estimation with Uncertainty Modeling", ""),
    ("", "Event-Based 3D Analysis of Pigeon Flocks: A High-Frequency Dataset for Perception-Driven Swarm Robotics", ""),
    ("", "Benchmarking Recurrent Event-Based Object Detection for Industrial Multi-Class Recognition on MTEvent", ""),
    ("", "Relative State Estimation using Event-Based Propeller Sensing", ""),
    ("", "AdaFuse-Det: Adaptive Cross-Modal Fusion of Event Cameras and Low-Light RGB Imagery for Robust Object Detection", ""),
    ("", "Design of a Real-time Asynchronous Monocular Odometry for Planetary Exploration", ""),
    ("", "NightSight: Passive Computation for Navigation in Dark Using Events", ""),
    ("", "EventShiftFlow: Towards Hardware-efficient FPGA-based Flow Estimation", ""),
]

FRI_WS_49_MORNING_POSTERS = [
    ("", "PolyFly: Polytopic Optimal Planning for Safe Collision-Free Cable-Suspended Aerial Payload Transportation", "Haechan Mark Bong and Giovanni Beltrame"),
    ("", "Learning Safe, Agile Flight for Aerial Cinematography using Egocentric Videos", "Peter Breuer, Jiaxu Xing, Angel Romero, Yunfan Ren and Davide Scaramuzza"),
    ("", "Agile Flight Emerges from Multi-Agent Competitive Racing", "Vineet Pasumarti, Lorenzo Bianchi and Antonio Loquercio"),
    ("", "CALOS: Control-Affine Lyapunov On-manifold Safety Layer for Safe Deep Reinforcement Learning for Quadrotors", "Fabrizio Cesareo, Sebastiano Mengozzi, Nicola Mimmo and Andrea Acquaviva"),
    ("", "Distributed Multi-Robot Ergodic Coverage Control for Estimating Time-Varying Spatial Processes", "Mattia Mantovani, Mattia Catellani and Lorenzo Sabattini"),
    ("", "Estimating Camera Extrinsics Using a Corner-Based Extended Kalman Filter for Autonomous Drone Racing", "Robin Ferede, Christophe De Wagter and Guido C.H.E. de Croon"),
    ("", "VR-Supervised Autonomy for Safe Aerial Manipulation Operations", "Dimitris Chaikalis, MianTao Zhao and Salua Hamaza"),
]
FRI_WS_49_AFTERNOON_POSTERS = [
    ("", "Safe Aerial 3D Path Planning for Autonomous UAVs using Magnetic Potential Fields", ""),
    ("", "Superhuman Safe and Agile Racing through Multi-Agent Reinforcement Learning", "Ismail Geles, Leonard Bauersfeld, Markus Wulfmeier and Davide Scaramuzza"),
    ("", "Learning on the Fly: Rapid Policy Adaptation via Differentiable Simulation", ""),
    ("", "A Squirrel-Inspired Drone with Enhanced Stability, Agility and Maneuverability via Whole-body Morphing", "Liming Zheng, Alexander van Zuijlen and Salua Hamaza"),
    ("", "LMPath: Language-Mediated Priors and Path Generation for Aerial Exploration", "Jonathan A. Diller, Fernando Cladera, Camillo J. Taylor and Vijay Kumar"),
    ("", "RVC-NMPC: Nonlinear Model Predictive Control with Reciprocal Velocity Constraints for Mutual Collision Avoidance in Agile UAV Flight", "Vít Krátký, Robert Pěnička, Parakh Manoj Gupta, Ondřej Procházka and Martin Saska"),
    ("", "Configuration-Adaptive Flight on Overactuated Tiltable-Quadrotors via Reinforcement Learning", "Wentao Zhang and Moju Zhao"),
    ("", "Tree Shaking with Aerial Manipulators: Model, Control, Analysis and Validation", "A. González-Morgado, E. Cuniato, G. Heredia, A. Ollero, R. Siegwart and M. Tognon"),
]

FRI_WS_53_POSTER_1 = [
    ("", "STUPID Robot: Safe Task Understanding and Planning with Interactive Dialog for Robotic HIFU Systems", "Xiu Zhang, Alessandro Piccolo, Silvia Buratti, Junling Fu, Alessandro Diodato, Selene Tognarelli, Arianna Menciassi"),
    ("", "Deformable State Estimation for Autonomous Surgical Tissue Retraction Under Partial Observability", "Everest Yang, Skye Thompson, George D. Konidaris"),
    ("", "A Study on a Semi-Autonomous Surgical Assistant Collaborative Robot for Laparoscopic Surgery", "Taehoon Kim, Youqiang Zhang, Jongchan Mun, Sangrok Jin"),
    ("", "Bio-Inspired 3D-Printable Continuum Robot: A Tendon-Driven Continuum Endoscope - Preliminary Cadaveric Trials for Colonoscopy", "Md Modassir Firdaus, Madhu Vadali, Branesh M. Pillai, Chumpon Wilasrusmee"),
    ("", "Egocentric Learning for Bimanual Surgical Manipulation", "Eugenia Mawuenya Akpo, Shirui Lyu, Nicholas Raison, Christos Bergeles, Letizia Gionfrida"),
    ("", "Physics-Informed CT-to-US Translation via Biomechanical Strain and Conditional Diffusion Models", "Yizhao Qian, Jiayuan Luo, Li Liu"),
    ("", "Toward Reliable Control of Two Section Continuum Robots", "Ivan Adi Kuncara, Ayoung Hong"),
    ("", "Vision-Based Soft-Tissue Deformation Tracking for Adaptive Surgical Robot Behavior", "Gabriele Furnari, Elisa Iovene, Federica Ferraguti"),
    ("", "Assessment of Workload and Ergonomics in Simulated Robot-Assisted Partial Nephrectomy", "Elisa Iovene, Deborah Cattafesta, Anna Emilia Candela, Vittorio Cuculo, Sara Moccia, Elena De Momi, Federica Ferraguti"),
    ("", "A perspective on the use of Foundation Models in Surgical Data Science", "Deborah Cattafesta, Mariachiara Di Cosmo, Federica Ferraguti, Simona Tiribelli, Sara Moccia"),
    ("", "EndoDDC: Learning Sparse to Dense Reconstruction for Endoscopic Robotic Navigation via Diffusion Depth Completion", "Yinheng Lin, Yiming Huang, Beilei Cui, Long Bai, Huxin Gao, Hongliang Ren, Jiewen Lai"),
    ("", "Anatomical Landmark-Guided Deep Reinforcement Learning for Autonomous Gastric Navigation", "Haoxuan Wu, Sishen Yuan, Haitao Gao, Zhen Li, Xiuli Zuo, Hongliang Ren"),
    ("", "Learning to Cut: A Structure-Aware Transformer for Autonomous Surgical Tumor Removal", "Francesco Bigi, Gabriele Furnari, Elisa Iovene, Andrea Pupa, Cristian Secchi, Federica Ferraguti"),
    ("", "Registration After Completion: Pose-Robust Completion for Sparse Partial Point Set Registration in Computer-Assisted Orthopedic Surgery", "Xinzhe Du, Shixing Ma, Shuwei Shao, Max Q.-H. Meng, Zhe Min"),
]
FRI_WS_53_POSTER_2 = [
    ("", "Real-Time Frame-Level Confidence Estimation for Stereo Disparity in Endoscopic Imaging", "Laura Cruciani, Sara Martuscelli, Matteo Magnani, Anna Emilia Candela, Elena De Momi"),
    ("", "AnkleSurg: A Reinforcement Learning Environment for Robot Ankle Fracture Reduction Surgery", "Catherine Abraham, Sean R. Anderson, Sanjeev Madan, Molly Kennedy, Sanja Dogramadzi"),
    ("", "Automatic Generation of Virtual Fixtures from Digital Twin for Teleoperated Lung Ultrasound", "Davide Nardi, Edoardo Lamon, Daniele Fontanelli, Matteo Saveriano, Luigi Palopoli"),
    ("", "A Multifunctional Capsule and Magnetic Navigation Platform for Controlled Actuation and Task Execution in GI Environments", "Razan Abu-Shaera, Shivam Gupta, Veerash Palanichamy, Onaizah Onaizah"),
    ("", "Towards Autonomous Tape Handling for Robotic Wound Redressing", "Xiao Liang, Lu Shen, Peihan Zhang, Soofiyan Atar, Florian Richter, Michael Yip"),
    ("", "Feedback Matters: Augmenting Autonomous Dissection with Visual and Topological Feedback", "Chung-Pang Wang, Changwei Chen, Xiao Liang, Soofiyan Atar, Florian Richter, Michael Yip"),
    ("", "Strategy-Supervised Autonomous Laparoscopic Camera Control via Event-Driven Graph Mining", "Keyu Zhou, Yahao Wu, Shunlei Li"),
    ("", "Continuously Multistable Mixed-Folded Tubes", "Sagi Senderovich, Ezra Ben-Abu, Shai Elbaz, Nadav Zemah, Amir D. Gat"),
    ("", "From Scanning Guidelines to Action: A Robotic Ultrasound Agent with LLM-Based Reasoning", "Yuan Bi, Yiping Zhou, Pei Liu, Feng Li, Zhongliang Jiang, Nassir Navab"),
    ("", "Bioinspired Kirigami Capsule Robot for Minimally Invasive Gastrointestinal Biopsy", "Ruizhou Zhao, Yichen Chu, Shuwei Zhao, Wenchao Yue, Raymond Shing-Yan Tang, Hongliang Ren"),
    ("", "See, Plan, Cut: MPC-Based Autonomous Volumetric Robotic Laser Surgery with OCT Guidance", "Ravi Prakash, Vincent Y. Wang, Arpit Mishra, Devi Yuliarti, Pei Zhong, Ryan P. McNabb, Patrick J. Codd, Leila J. Bridgeman"),
    ("", "DAISS: Phase-Aware Imitation Learning for Dual-Arm Robotic Ultrasound-Guided Interventions", "Feng Li, Pei Liu, Yuan Bi, Zhongliang Jiang, Nassir Navab"),
    ("", "Goal-Conditioned Reinforcement Learning for Autonomous Target Reaching in Intracardiac Catheter Navigation", "Angela Peloso, Giulio Bella, Elena De Momi"),
    ("", "Anatomy-Guided Autonomous Robotic Ultrasound Imaging: Learning Cross-Sectional Geometry Information", "Bo Wang, Giancarlo Ferrigno, Elena De Momi, Junling Fu"),
]

FRI_WS_54_POSTERS = [
    ("", "Evolving Cooperative Underwater Robotics: Transitioning from Rigid Structures to Deformable Net Manipulation", "Salvador López-Barajas, Alejandro Solis, Antonio Morales, Raúl Marín and Pedro J. Sanz"),
    ("", "Prior-Guided Grasp Admissibility for Safe Landmine Manipulation", "Alessandra Miuccio, Florian Lebecque, Emile Le Flécher, Charles Hamesse, Nikolaos Tsiogkas, Renaud Detry, Rob Haelterman"),
    ("", "Shared Autonomy for Robotic Manipulation in Explosive Ordnance Disposal: Insights from the GENIUS Project", "Vitor Bueno and Maxim Vochten"),
    ("", "Unifying Task-Aware Robotic Approach, Grasp, and Post-Grasp Manipulation as a Closed-Chain Mechanism", "Janak Panthi, Farshid Alambeigi, and Mitch Pryor"),
    ("", "Loop closure grasping: Topological transformations enable strong, gentle, and versatile grasps", "Kentaro Barhydt, O. Godson Osele, Sreela Kodali, Cosima du Pasquier, Chase M. Hartquist, H. Harry Asada, and Allison M. Okamura"),
    ("", "Towards Dexterous Underwater Manipulation with Small-scale Robots: External Wrench Estimation and Interaction Detection", "Moritz Graf and Daniel Duecker"),
    ("", "Lightweight Base-Frame Calibration for Two Mobile Manipulators with Minimal Human Intervention", "Yuval Arbiv, Michael M. Bilevich, Dror Livnat, Dan Halperin"),
]

FRI_WS_62_POSTERS = [
    ("", "Anchor-oriented Multi-Robot Coverage without Global Localization", "Aiman Munir, Ehsan Latif, Ramviyas Parasuraman"),
    ("", "Human-oriented Interactive Exploration and Supervision with Limited Communication", "Zhuoli Tian, Yuyang Zhang, Jinsheng Wei, Meng Guo"),
    ("", "COPE: Robustifying Collaborative SLAM through Multi-Stage Pose-Graph Optimization", "José Pedro, Roberto C. Sundin, David Umsonst, Patric Jensfelt"),
    ("", "Data-driven Feature Tracking for Event Cameras", "Nico Messikommer, Carter Fang, Mathias Gehrig, Davide Scaramuzza"),
    ("", "Towards Auto-Generated Ground Truth for Evaluation of Perception Systems in Agriculture", "Jan Christoph Krause, Mark Niemeyer, Janosch Bajorath, Naeem Iqbal, Joachim Hertzberg"),
    ("", "Multi-FEAT: Multi-Feature Edge AlignmenT for Targetless Camera-LiDAR Calibration", "Bichi Zhang, Holger Caesar, Raj Thilak Rajan"),
    ("", "Towards Event-Based Satellite Docking: A Photometrically Accurate Low-Earth Orbit Hardware Simulation", "Nuwan Munasinghe, Cedric Le Gentil, Jack Naylor, Mikhail Asavkin, Donald G. Danserea, Teresa Vidal-Calleja"),
    ("", "Distributed Coverage Control for Spatial Processes Estimation with Noisy Observations", "Mattia Mantovani, Federico Pratissoli, Lorenzo Sabattini"),
]

FRI_WS_64_LIGHTNING = [
    ("", "TinySDP: Real Time Semidefinite Optimization for Certifiable and Agile Edge Robotics", "Ishaan Mahajan, Jon Arrizabalaga, Andrea Grillo, Fausto Vega, James Anderson, Zachary Manchester, Brian Plancher"),
    ("", "Coroutine Scheduling in Task and Motion Planning", "Clayton Ramsey and Lydia Kavraki"),
    ("", "pAORRTC: GPU-Parallel Almost-Surely Asymptotically Optimal Planning", "Chih Huang, Zachary Kingston and Brian Plancher"),
    ("", "Sharc-Drive: Hardware-Aware Simulation for Real-Time Autonomous Driving", "Yasin Sonmez, Shengmin Liu, Tyler Brady, Yash Mathur, Malavikha Sudarshan and Murat Arcak"),
    ("", "End-to-End Hardware-Algorithm Co-Design for High-Rate FPGA-Accelerated MPC on Tiny Drones", "Andrea Grillo and Brian Plancher"),
    ("", "Hardware-Aware Co-Design for High-Frequency Visual MPC on Edge SoCs", "Prithvi Singh"),
    ("", "PyRoFFI: Accelerating Foreign Kinematics Kernels", "Sai Coumar, Weihang Guo and Zachary Kingston"),
    ("", "FPGA-Based Hardware Acceleration of Contrast Maximization for Event Vision", "Marcin Kowalczyk and Tomasz Kryjak"),
    ("", "cuPIQP: GPU-Accelerated Proximal Interior-Point Solvers for General and Multistage QP", "Fenglong Song, Roland Schwan and Colin Jones"),
    ("", "Open SPITE", "Yulie Arad, Nancy Amato, Marco Morales Aguirre, James Motes and Marta Markowicz"),
    ("", "A New RoboArch Cosimulation Framework for Resource-Constrained Multi-Robot Teams", "Derin Ozturk, Zhantong Qiu, Jason Lowe-Power and Christopher Batten"),
]
FRI_WS_64_EXTRA_POSTERS = [
    ("", "Accelerating Real-Robot Deep Reinforcement Learning via Branched Symmetries", "Ryan Vander Stelt, Cleiver Ruiz-Martinez, Blake Hull and Juan Rojas"),
    ("", "Provably Accurate Fixed-Point Arithmetic for Robotics Accelerators", "Alp Eren Yilmaz, Lillian Pentecost, Thomas Bourgeat, Brian Plancher and Sabrina Neuman"),
    ("", "ClustViT: Clustering-based Token Merging for Semantic Segmentation", "Fabio Montello, Ronja Güldenring and Lazaros Nalpantidis"),
    ("", "GPU-Accelerated Collaborative Optimal Transport for Scalable Multi-agent Motion Control", "Ying Zhang, An Thai Le and Meng Guo"),
    ("", "Model-Structured Neural Networks on Edge Hardware: A Preliminary Study", "Giovanni Maria Francesco La Scala, Sebastiano Taddei, Francesco Baroni, Gioele Defrancesco, Filippo Faccini and Gastone Pietro Rosati Papini"),
    ("", "Neuromorphic Underwater Navigation: Complementing DVLs with Stereo Event Cameras", "Hayat Rajani, Valerio Franchi and Nuno Gracias"),
    ("", "Towards Massively Parallel Motion Planning with Inverse Dynamics", "Ioannis Tsikelis and Enrico Mingo Hoffman"),
    ("", "Multi-Robot SPITE: Accelerating Multi-Robot Motion Planning via Hierarchical Swept-Volume Approximations", "Marta Markowicz, Sarah Dowden, James Motes, Marco Morales and Nancy Amato"),
]

FRI_WS_72_SPOTLIGHT_1 = [
    ("", "A Dual-Simulator Framework for Physics Based Locomotion and Vision Based Navigation of Planetary Rovers", ""),
    ("", "Towards Low-Gravity Planetary Exploration: Reinforcement Learning for Quadrupedal Walking, Jumping, and Attitude Control", ""),
    ("", "Soft Deployable Airless Wheel for Lunar Lava Tube Exploration", ""),
    ("", "Differentiable Co-Optimization of Mass-Limited Legged Robots Across Variable Gravity", ""),
    ("", "Toward Density-Aware Granular Loco-Manipulation for Obstacle-Aided Mobility on Steep Slopes", ""),
    ("", "Autonomous Soft Growing Robots for Lunar Pit and Lava Tube Exploration", ""),
    ("", "Design of Supernumerary Robotic Limbs for the Augmentation of Astronauts Performing Partial-Gravity Extra-Vehicular Activities (EVAs)", ""),
    ("", "Manipulation Challenges of Robotic Lunar Construction With Regolith-Filled Sandbags", ""),
    ("", "A Dexterous and Compliant Gripper With Soft Hydraulic Actuation for Microgravity Manipulation", ""),
    ("", "Autonomous Bulldozer for Lunar Terrain Manipulation", ""),
    ("", "GIRAF - Greatly Increased Reach for Adaptive Fieldwork", ""),
    ("", "Woven-based Compliant Soft Gripper for Space Debris Removal Missions", ""),
    ("", "The iMETRO Dynamic Simulation: An Open-Source Simulator for Intravehicular Space Robotics Research", ""),
    ("", "Learning to Detumble: Adaptive Post-Capture Stabilization of Uncooperative Space Debris", ""),
    ("", "Parallel Thruster-Propeller Control for Emulating Spacecraft Proximity Dynamics on a Free-Flyer", ""),
]
FRI_WS_72_SPOTLIGHT_2 = [
    ("", "A Hybrid AI-assisted Hazard Detection and Avoidance Pipeline for Autonomous Lunar Landing", ""),
    ("", "Gremlin: AI-Based Adversarial Stress-Testing for Autonomous Space Systems", ""),
    ("", "Bioelectric-Inspired Potential Games for Decentralized Multi-Agent Cave Exploration with Information Homeostasis", ""),
    ("", "SpaceTry: An LLM-Assisted Simulation Testbed for Space Mission Autonomy Engineering", ""),
    ("", "Large Exploration by Small Payloads: Underwater Deployables via Robot Swarms", ""),
    ("", "What Breaks Monocular SLAM in Microgravity? An Initial Benchmark on Rotation-Dominant Astrobee ISS Sequences", ""),
    ("", "Equivariant Neural Inertial Odometry for Microgravity Space Robotics", ""),
    ("", "Safety-Bounded Space Robot Navigation via Vision-Language Model Integration", ""),
    ("", "STELLAR: Sample-Efficient Test-Time Adaptation of Scene Representations for Planetary Navigation under Compute Constraints", ""),
    ("", "TMF-Net: Multi-modal Transformer Fusion for Relative Pose Estimation of Non-Cooperative Targets", ""),
    ("", "From SLAM Drift to Goal Drift: Measuring Coordination Sensitivity on Lunar Analogue Terrain", ""),
    ("", "Map-Free Monocular Visual Localization via Conic Geometric Cues for Astrobee Free-Flyers", ""),
    ("", "Learning Physically Informed Maps for Off-world Autonomous Scientific Discovery", ""),
    ("", "Vision-Based Relative State Estimation for Non-Cooperative Spacecraft Using Deep Learning and Adaptive Kalman Filtering", ""),
    ("", "TinyML for On-Board Earth Observation: From Ultra-Low-Power MCUs to In-Sensor Computing", ""),
]


CURATED_WORKSHOP_PRESENTATIONS = {
    # Manually read from the MathWorks tutorial agenda.
    "mon-ws-01": [
        curated_row("talk", "Design a UAV Digital Twin and Mission Simulation", "14:10", "14:50", speaker="Ronal George, MathWorks", url=MON_WS_01_URL),
        curated_row("talk", "Apply Model-Based Design with Integrated Verification and Validation in Simulink", "14:50", "15:30", speaker="To Be Announced", url=MON_WS_01_URL),
        curated_row("poster", "Break", "15:30", "16:00", url=MON_WS_01_URL),
        curated_row("talk", "PX4 and ArduPilot Autopilot Deployment with HIL", "16:00", "16:40", speaker="Arun Mathamkode, MathWorks", url=MON_WS_01_URL),
        curated_row("panel", "Future Directions and Closing the Sim-to-Real Gap", "16:40", "17:30", speaker="Roberto G. Valenti, Giuseppe Loianno, Skydio guest, Ronal George, Moiz Khan", url=MON_WS_01_URL),
    ],
    # Manually read from https://sites.google.com/view/icra-2026-tutorial-geometry/.
    "mon-ws-02": [
        curated_row("talk", "Warm-up on Riemannian geometry for robotics", "09:00", "09:45", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Tutorial session: Riemannian latent variable models", "09:45", "10:30", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Practical session: Riemannian latent variable models", "11:00", "11:45", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "The geometry of locomotion", "11:45", "12:30", speaker="Ross Hatton", context="Invited talk", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Tutorial session: Riemannian generative models", "14:00", "14:45", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Practical session: Riemannian generative models", "14:45", "15:30", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Leveraging geometric symmetry in robotic policy learning", "16:00", "16:45", speaker="Dian Wang", context="Invited talk", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
        curated_row("talk", "Geometric algebra, Riemannian geometry, and quantum computing for robotics modeling, learning and control", "16:45", "17:15", speaker="Eduardo Bayro-Corrochano", context="Invited talk", url="https://sites.google.com/view/icra-2026-tutorial-geometry/"),
    ],
    # Source publishes tutorial format but no timed agenda; split over the official ICRA morning tutorial block.
    "mon-ws-03": [
        curated_row("talk", "Survey of modern simulation techniques and tools for robotics research", "09:00", "10:30", context="Published tutorial format; source has no internal times", url=MON_WS_03_URL),
        curated_row("talk", "Hands-on reinforcement-learning simulation and cross-environment deployment", "11:00", "12:30", context="Published tutorial format; source has no internal times", url=MON_WS_03_URL),
    ],
    # Manually read from https://tum-avs.github.io/ICRA2026_Workshop/.
    "mon-ws-04": [
        curated_row("talk", "Distinguished Talk 1", "14:15", "14:45", speaker="Hongyang Li", url=MON_WS_04_URL),
        curated_row("talk", "From Benchmarks to Real-World Autonomous Driving", "14:45", "15:15", speaker="Cristina Olaverri-Monreal", url=MON_WS_04_URL),
        *curated_papers("spotlight", "15:15", "15:30", MON_WS_04_SPOTLIGHT_PAPERS, context="Spotlight Session (5 featured spotlights)", url=MON_WS_04_URL),
        *curated_papers("poster", "15:30", "16:00", MON_WS_04_PAPERS, context="Poster Session (13 accepted papers)", url=MON_WS_04_URL),
        curated_row("talk", "Democratizing Autonomous Driving", "16:00", "16:30", speaker="Kashyap Chitta", url=MON_WS_04_URL),
        curated_row("talk", "Embodied Reasoning for Out-of-Distribution Reliability in Autonomy", "16:30", "17:00", speaker="Marco Pavone / Milan Ganai", url=MON_WS_04_URL),
        curated_row("talk", "Distinguished Talk 5", "17:15", "17:45", speaker="Felix Fent", url=MON_WS_04_URL),
    ],
    # Manually read from https://mobile-robotics-hub.github.io/workshop2026/.
    "mon-ws-05": [
        curated_row("talk", "Everywhere and Everywhen: Progress on Long-Term Localization with Radar", "09:00", "09:30", speaker="Timothy Barfoot", url=MON_WS_05_URL),
        curated_row("talk", "Learning Robust and Generalizable Features for Long-term Localization", "09:30", "10:00", speaker="Xieyuanli Chen", url=MON_WS_05_URL),
        *curated_papers("lightning", "10:00", "10:20", MON_WS_05_SESSION_A[:5], context="Lightning Talks (3 min/pers)", url=MON_WS_05_URL),
        curated_row("talk", "Startup Presentation", "10:20", "10:30", speaker="RideScan", context="supported by IEEE RAS TABxStartups", url=MON_WS_05_URL),
        *curated_papers("poster", "10:30", "11:00", MON_WS_05_SESSION_A, context="Poster Session A setup", url=MON_WS_05_URL),
        curated_row("talk", "Long-Term LiDAR Localization in the Wild: From Foundation Models to Ultra-Lightweight Features", "11:00", "11:30", speaker="Ayoung Kim", url=MON_WS_05_URL),
        curated_row("talk", "Learning Robust Robot Perception in Unknown Environments", "11:30", "12:00", speaker="Marija Popović", url=MON_WS_05_URL),
        curated_row("talk", "Startup Presentation", "12:00", "12:10", speaker="Raise Robotics", context="supported by IEEE RAS TABxStartups", url=MON_WS_05_URL),
        *curated_papers("poster", "12:10", "14:00", MON_WS_05_SESSION_A, context="Poster Session A continued", url=MON_WS_05_URL),
        curated_row("talk", "Multi-robot Mapping in Maritime Environments", "14:00", "14:30", speaker="Teresa Vidal Calleja", url=MON_WS_05_URL),
        curated_row("talk", "Tough Physical AI for Task Automation in Harsh Environments", "14:30", "15:00", speaker="Kazunori Ohno", url=MON_WS_05_URL),
        *curated_papers("lightning", "15:00", "15:20", MON_WS_05_SESSION_B[:5], context="Lightning Talks (3 min/pers)", url=MON_WS_05_URL),
        curated_row("talk", "Competition Promotion", "15:20", "15:30", speaker="ATEC (sponsor)", url=MON_WS_05_URL),
        *curated_papers("poster", "15:30", "16:30", MON_WS_05_SESSION_B, context="Poster Session B", url=MON_WS_05_URL),
        curated_row("panel", "Interactive Panel Discussion", "16:30", "17:00", speaker="All speakers", url=MON_WS_05_URL),
    ],
    # Manually read from https://sites.google.com/view/icra2026-workshop-robot-ethics/home/programme.
    "mon-ws-06": [
        curated_row("talk", "Session 1: Ethical, Legal and Technical Challenges and Considerations", "09:10", "09:20", speaker="Jim Torresen and co-organizers", url=MON_WS_06_URL),
        curated_row("talk", "Encouraging Human Challenge through Robotic Illusions: Motivation, Self-Efficacy, and White Lies", "09:20", "09:50", speaker="Yasuhisa Hirata, Tohoku University, Japan", context="Session 1 invited talk", url=MON_WS_06_URL),
        *curated_papers("lightning", "09:50", "10:30", MON_WS_06_PAPER_TEASERS, context="Poster teasers (3-4 minutes each)", url=MON_WS_06_URL),
        curated_row("talk", "KnowledgeVerse AI", "09:50", "10:30", speaker="Vijay Nadadur", context="StartUp company presentation", url=MON_WS_06_URL),
        curated_row("talk", "Robots as Welfare Technologies and Actors in Home and Healthcare - challenges and opportunities.", "11:00", "11:30", speaker="Diana S. Lindblom, Yueh-Hsuan Weng", context="Organiser talks and interactive session", url=MON_WS_06_URL),
        *curated_papers("paper", "11:30", "12:30", MON_WS_06_SESSION_2_PAPERS, context="Session 2 contributed presentation and interaction", url=MON_WS_06_URL),
        curated_row("talk", "What Could Possibly Go Wrong? A Case Study in Responsible Robotics", "13:30", "14:00", speaker="Alan Winfield, University of the West of England (UWE), Bristol, UK", context="Session 3 invited talk", url=MON_WS_06_URL),
        curated_row("talk", "Red and Blue teaming session", "14:00", "14:40", speaker="Praminda Caled-Solly", context="Session 3", url=MON_WS_06_URL),
        *curated_papers("paper", "14:40", "15:30", MON_WS_06_SESSION_3_PAPERS, context="Session 3 contributed presentation", url=MON_WS_06_URL),
        *curated_papers("poster", "15:30", "16:00", MON_WS_06_ALL_POSTERS, context="Poster session and interaction", url=MON_WS_06_URL),
        curated_row("talk", "Human-First Innovation for Physical AI: Principles for Human-Robot Interaction in the Age of AI", "16:00", "16:30", speaker="Toshie Takahashi, Waseda University, Japan", context="Session 4 invited talk", url=MON_WS_06_URL),
        curated_row("talk", "Diversity and International Perspectives", "16:30", "17:15", speaker="Selected speakers", context="Session 4 interactive session", url=MON_WS_06_URL),
    ],
    # Manually read from https://rose-workshops.github.io/rose2026/.
    "mon-ws-08": [
        curated_row("talk", "Keynote 1: Designing Navigation Frameworks on ROS 2", "09:00", "09:25", speaker="Francisco Martín Rico", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 1: From Simulation to Reality: Autonomous 3D Exploration with DAEP on Heterogeneous Robots", "09:25", "09:40", speaker="Emil Wiman, Mariusz Wzorek, Piotr Rudol, Tommy Persson and Mattias Tiger", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 2: RANT: Ant-Inspired Multi-Robot Rainforest Exploration Using Particle-Filter Localisation and Virtual Pheromone Coordination", "09:40", "09:55", speaker="Ameer Alhashemi, Layan Abdulhadi, Karam Abuodeh, Tala Baghdadi and Suryanarayana Datla", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 3: CogNav: Human-Inspired Collision Avoidance Strategy", "09:55", "10:10", speaker="Mahsa Nikmard, Patrizio Pelliccione and Gianlorenzo D'Angelo", url=MON_WS_08_URL),
        curated_row("talk", "Keynote 2: The CO-HAND Project: An Autonomous Industrial PoC with TIAGo Pro", "10:10", "10:35", speaker="Michela Cavuoto", url=MON_WS_08_URL),
        curated_row("poster", "Coffee-break/Posters", "10:35", "11:00", url=MON_WS_08_URL),
        curated_row("talk", "Keynote 3: Open-source software for learning-based robot control: from simulation to real-world deployment", "11:00", "11:25", speaker="Angela Schoellig", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 4: CrossMaps: a Confidence-Aware Open-Vocabulary Semantic Mapping for Rover Navigation", "11:25", "11:40", speaker="Jan-Niklas Klein, Sona Ghahremani, Christian Medeiros Adriano and Holger Giese", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 5: ORICF - Open Robotics Inference and Control Framework", "11:40", "11:55", speaker="Andrés Meseguer Valenzuela and Luis Miguel Bartolín Arnau", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 6: A Swarm Algorithm for Following Formations applied to Crazyflie Drones", "11:55", "12:10", speaker="Oliver Kosak, Philipp Kastenmüller, Fabian Schwaiger, Vinzenz Malke and Wolfgang Reif", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 7: ROS2 Connect: A new ROS2 over WAN Solution", "12:10", "12:25", speaker="Daniel Schott, Lakshminarasimhan Srinivasan, Christian Herrmann and Andreas Nüchter", url=MON_WS_08_URL),
        curated_row("talk", "Keynote 4: Engineering of Norm-aware Self-Adaptive Robots using RoboStar", "14:00", "14:25", speaker="Ana Cavalcanti", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 8: Enabling Runtime Reconfiguration in Multimodal Teleoperation Systems", "14:25", "14:40", speaker="Luuk Lenders, Thijs Bink, Douwe Dresscher, Kenan Niu, Jan van Erp and Jan Broenink", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 9: A Software Architecture for ROS2-CNC Interoperability: Automated Collision-Free Robot Motion Planning and Deterministic Execution", "14:40", "14:55", speaker="Samed Ajdinovic, Matthias Marquart, Benjamin Kaiser, Siddieq Mansour, Andreas Wortmann, Oliver Riedel and Alexander Verl", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 10: tf2_rs: Bringing tf2 to Rust", "14:55", "15:10", speaker="Théo Engels, Antonio Paolillo and Ken Hasselmann", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 11: Wizard or Example? Supporting Robot Program Reuse Discovery Across Expertise Levels", "15:10", "15:25", speaker="Yoganata Kristanto, Mina Alipour, Miguel Campusano and Aljaz Kramberger", url=MON_WS_08_URL),
        curated_row("poster", "Coffee-break/Posters", "15:30", "16:00", url=MON_WS_08_URL),
        curated_row("talk", "Keynote 5: Do LLMs understand ROS computation graphs?", "16:00", "16:25", speaker="Ivano Malavolta", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 12: Are We Welcome Here? A Preliminary Study of Newcomer Onboarding in the ROS Ecosystem", "16:25", "16:40", speaker="Juliana Freitas, Elijah Phifer, Nabila Fairuz and Felipe Fronchetti", url=MON_WS_08_URL),
        curated_row("talk", "Work-in-progress 13: The Walking Packages: A Survival Analysis of ROS Repositories", "16:40", "16:55", speaker="Juliana Freitas, Elijah Phifer and Felipe Fronchetti", url=MON_WS_08_URL),
    ],
    # Manually read from https://active-perception-workshop.github.io/.
    "mon-ws-10": [
        curated_row("talk", "Early-Career Talk", "09:00", "09:15", speaker="Marina Aoyama", context="University of Edinburgh", url=MON_WS_10_URL),
        curated_row("talk", "Early-Career Talk", "09:15", "09:30", speaker="Jun Yang", context="Epson Canada", url=MON_WS_10_URL),
        curated_row("spotlight", "Abstracts Spotlight", "09:30", "09:45", context="1-minute presentation per accepted poster", url=MON_WS_10_URL),
        curated_row("poster", "Coffee Break & Poster Session", "09:45", "10:15", url=MON_WS_10_URL),
        curated_row("talk", "Active Visuo-Tactile Intelligence for Robotics", "10:15", "10:45", speaker="Mohsen Kaboli", url=MON_WS_10_URL),
        curated_row("talk", "A Programming Language for Interactive Perception and Robust Behavior in the Real World", "10:45", "11:15", speaker="Oliver Brock", url=MON_WS_10_URL),
        curated_row("talk", "Exploring & perceiving material properties", "11:15", "11:45", speaker="Katja Dörschner-Boyaci", url=MON_WS_10_URL),
        curated_row("talk", "From active touch to robot dexterity", "13:45", "14:15", speaker="Nathan Lepora", url=MON_WS_10_URL),
        curated_row("talk", "Application-Driven Active Perception", "14:15", "14:45", speaker="Tirthankar Bandyopadhyay", url=MON_WS_10_URL),
        curated_row("spotlight", "Abstracts Spotlight", "14:45", "15:00", context="1-minute presentation per accepted poster", url=MON_WS_10_URL),
        curated_row("poster", "Coffee Break & Poster Session", "15:00", "15:30", url=MON_WS_10_URL),
        curated_row("talk", "Learning to Look, Probing to Understand: Active Perception via Intervention", "15:30", "16:00", speaker="Jeannette Bohg", url=MON_WS_10_URL),
        curated_row("talk", "Early-Career Talk", "16:00", "16:15", speaker="Adrian Röfer", context="University of Freiburg", url=MON_WS_10_URL),
        curated_row("talk", "Early-Career Talk", "16:15", "16:30", speaker="Irmak Guzey", context="New York University", url=MON_WS_10_URL),
        curated_row("panel", "Panel Discussion: Active Perception in the Real World", "16:30", "17:15", url=MON_WS_10_URL),
    ],
    # Manually read from https://sites.google.com/unisi.it/human-augmentation/home-page.
    "mon-ws-13": [
        curated_row("talk", "DEMO SESSION", "14:05", "14:30", speaker="S. Rossi, M. Pozzi, B. Brogi", url=MON_WS_13_URL),
        curated_row("talk", "Sensorimotor augmentation through wearable interfaces", "14:30", "14:45", speaker="D. Prattichizzo", context="SESSION I", url=MON_WS_13_URL),
        curated_row("talk", "Robotic augmentation for spinal cord injured individuals", "14:45", "15:00", speaker="A. Oliviero", context="SESSION I", url=MON_WS_13_URL),
        curated_row("talk", "Muscular null space control for robotic augmentation", "15:00", "15:15", speaker="A. D'Avella", context="SESSION I", url=MON_WS_13_URL),
        curated_row("talk", "Q&A", "15:15", "15:30", context="SESSION I", url=MON_WS_13_URL),
        curated_row("talk", "I. Nisky", "16:00", "16:15", context="SESSION II", url=MON_WS_13_URL),
        curated_row("talk", "User Agency and Load Sharing in Semi-Autonomous Control of Robotic Hands", "16:15", "16:30", speaker="J. Starke", context="SESSION II", url=MON_WS_13_URL),
        curated_row("talk", "A predictive coding framework for safe and versatile control of supernumerary robotic limbs", "16:30", "16:45", speaker="E. Burdet", context="SESSION II", url=MON_WS_13_URL),
        curated_row("talk", "Toward an Era of Exchanging Capabilities", "16:45", "17:00", speaker="M. Inami", context="SESSION II", url=MON_WS_13_URL),
        curated_row("talk", "Superlimbs for space applications", "17:00", "17:15", speaker="H.H. Asada", context="SESSION II", url=MON_WS_13_URL),
        curated_row("talk", "Introduction to ASPIRE program", "17:15", "17:20", speaker="M. Inami", url=MON_WS_13_URL),
        curated_row("panel", "ROUND TABLE DISCUSSION", "17:20", "17:30", url=MON_WS_13_URL),
    ],
    # Manually read from the Profactor event page.
    "mon-ws-15": [
        curated_row("talk", "Keynote 1: Building Sustainable Supply Chains in Robotics: Learning from the Electronics Sector", "14:10", "14:30", speaker="a. Prof. Dr. André Martinuzzi (Vienna University of Economics and Business, AT)", url=MON_WS_15_URL),
        curated_row("talk", "Keynote 2: Robots as endangered species", "14:30", "15:00", speaker="Prof. José Halloy (Université Paris Cité, FR)", url=MON_WS_15_URL),
        curated_row("panel", "Panel discussion", "15:00", "15:30", url=MON_WS_15_URL),
        *curated_papers("poster", "15:30", "16:00", MON_WS_15_POSTERS, context="Poster Presentation", url=MON_WS_15_URL),
        *curated_papers("talk", "16:00", "16:35", MON_WS_15_PITCHES, context="Short pitches (5 min each)", url=MON_WS_15_URL),
        curated_row("talk", "Collaborative roadmap: shared actions & next steps", "16:35", "17:30", url=MON_WS_15_URL),
    ],
    # Manually read from https://connected-robots.com.
    "mon-ws-16": [
        curated_row("talk", "Digital Twins and Cloud-Based Automation", "12:40", "13:00", speaker="Ken Goldberg", context="Keynote Talk 1", url="https://connected-robots.com"),
        curated_row("talk", "6G Networks and Cybersecurity for Connected Autonomous Systems", "13:00", "13:20", speaker="Prachi Sachdeva", context="Keynote Talk 2", url="https://connected-robots.com"),
        curated_row("talk", "Towards Bootstrapping Dexterous Manipulation with Cloud Robotics", "13:20", "13:40", speaker="Florian Pokorny", context="Keynote Talk 3", url="https://connected-robots.com"),
        curated_row("talk", "Communication Technologies for Robotics", "13:40", "14:00", speaker="Yoshinori Kitatsuji", context="Keynote Talk 4", url="https://connected-robots.com"),
        curated_row("paper", "Network-Aware Robotics Solutions", "14:15", "15:15", speaker="Authors of contributed papers", context="Technical sessions", url="https://connected-robots.com"),
        curated_row("panel", "Expert Panel: Discussion on Challenges and Opportunities in Connected Autonomous Robotic Systems", "15:15", "16:00", speaker="Ken Goldberg, Prachi Sachdeva, Florian Pokorny, Yoshinori Kitatsuji, Denis Efimov", url="https://connected-robots.com"),
        curated_row("panel", "Future Directions in Connected Autonomous Robotic Systems", "16:00", "16:30", speaker="Organizers", context="Closing panel", url="https://connected-robots.com"),
    ],
    # Manually read from https://dex-manipulation.github.io/icra2026/.
    "mon-ws-17": [
        curated_row("talk", "Invited talk", "09:00", "09:25", speaker="Pulkit Agrawal", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "09:25", "09:50", speaker="Rika Antonova", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "09:50", "10:15", speaker="Georgia Chalvatzaki", url=MON_WS_17_URL),
        curated_row("spotlight", "Spotlight talks", "10:15", "10:30", context="Accepted papers and industry spotlight", url=MON_WS_17_URL),
        curated_row("poster", "Coffee & Poster", "10:30", "11:15", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "11:15", "11:40", speaker="Jie Song", url=MON_WS_17_URL),
        curated_row("panel", "Panel Discussion 1", "11:40", "12:25", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "13:30", "13:55", speaker="Robert Katzschmann", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "13:55", "14:20", speaker="Jeannette Bohg", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "14:20", "14:45", speaker="Maria Bauza Villalonga", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "14:45", "15:10", speaker="Nathan Lepora", url=MON_WS_17_URL),
        curated_row("spotlight", "Spotlight talks", "15:10", "15:30", context="Accepted papers and industry spotlights", url=MON_WS_17_URL),
        curated_row("poster", "Coffee & Poster", "15:30", "16:10", url=MON_WS_17_URL),
        curated_row("talk", "Invited talk", "16:10", "16:35", speaker="Matei Ciocarlie", url=MON_WS_17_URL),
        curated_row("panel", "Panel Discussion 2", "16:35", "17:20", url=MON_WS_17_URL),
    ],
    # Manually read from https://sites.google.com/view/embodied-ai-icra-26/.
    "mon-ws-18": [
        curated_row("talk", "Telepresence Speed-Dating Activity", "09:10", "09:30", url=MON_WS_18_URL),
        curated_row("talk", "Multisensory Immersion and Biosensing in XR", "09:30", "10:00", speaker="Tiago Henrique Falk (INRS, University of Québec)", context="Invited Talk", url=MON_WS_18_URL),
        curated_row(
            "poster",
            "Demo & Poster Teasers",
            "10:00",
            "10:30",
            speaker="Zhongyu Li (TEC); Marc-Antoine Moinnereau (Kaption); Gijs den Butter (SenseGlove + Haption); Joshua Joseph (XSens); Andrei Battistel (Enchanted Tools); Nìcolas Figueroa (NFM Robotics); Luc Schoot Uiterkamp (University of Twente)",
            url=MON_WS_18_URL,
        ),
        curated_row("poster", "Live Telerobotic Demonstrations", "10:30", "11:15", context="Refreshment & Coffee Break", url=MON_WS_18_URL),
        curated_row("talk", "Telerobotics for CERN: 10 Years of Operations in Hazardous Zones", "11:15", "11:45", speaker="Eloise Matheson (CERN)", context="Invited Talk", url=MON_WS_18_URL),
        curated_row("talk", "NeuraLoop: A High-Bandwidth Bidirectional Wearable Interface", "11:45", "12:15", speaker="Strahinja Dosen (Aalborg University)", context="Invited Talk", url=MON_WS_18_URL),
        curated_row("panel", "Panel", "12:15", "12:45", url=MON_WS_18_URL),
    ],
    # Manually read from https://sites.google.com/view/icra-2026-s2s-perception/.
    "mon-ws-19": [
        curated_row("talk", "Fast, Efficient, and Robust: Perception and Localisation for GPS-Denied Robotics", "08:40", "09:10", speaker="Tobias Fischer", context="Session 1", url=MON_WS_19_URL),
        curated_row("spotlight", "Underwater 3D Reconstruction by Interleaving Multimodal SLAM and Incremental Gaussian Splatting", "09:10", "09:15", speaker="Daniel Yang", context="Spotlight 1", url=MON_WS_19_URL),
        curated_row("talk", "Towards Robust Multi-Agent Underwater Localization and Coastal Semantic Mapping", "09:15", "09:45", speaker="Josh Mangelson", context="Session 1", url=MON_WS_19_URL),
        curated_row("spotlight", "Rapid and Physically-Based Gaussian Splatting of Unknown Space Objects in Low Earth Orbit", "09:45", "09:50", speaker="Tae Ha Park", context="Spotlight 2", url=MON_WS_19_URL),
        curated_row("talk", "Spatial Perception in Marine, Orbital, and Planetary Domains", "09:50", "10:20", speaker="Teresa Vidal-Calleja", context="Session 1", url=MON_WS_19_URL),
        curated_row("poster", "Poster Session 1", "10:20", "11:15", context="Coffee Break", url=MON_WS_19_URL),
        curated_row("talk", "Neuromorphic Perception and Computing for Space Applications", "11:15", "11:45", speaker="Tat-Jun Chin", context="Session 2", url=MON_WS_19_URL),
        curated_row("spotlight", "NeuSLAM: Dense Visual SLAM on Edge Devices", "11:45", "11:50", speaker="Aniket Gupta", context="Spotlight 3", url=MON_WS_19_URL),
        curated_row("talk", "Resilient Perception for Field Robotics in Harsh Maritime Environments", "11:50", "12:20", speaker="Annette Stahl", context="Session 2", url=MON_WS_19_URL),
        curated_row("talk", "Industry Spotlight", "13:30", "13:50", url=MON_WS_19_URL),
        curated_row("spotlight", "Early Career Spotlight", "13:50", "14:00", speaker="Grigory Solomatov", url=MON_WS_19_URL),
        curated_row("talk", "Toward Interplanetary Foundation Models: Can AI Drive a Mars Rover?", "14:00", "14:30", speaker="Hiro Ono", context="Session 3", url=MON_WS_19_URL),
        curated_row("spotlight", "FootRecon: Quadrupedal Terrain Reconstruction from Sparse Foot Contacts with Geometric Prior", "14:30", "14:35", speaker="Yujin Park", context="Spotlight 4", url=MON_WS_19_URL),
        curated_row("talk", "Redundancy Under Scarcity: Resourceful Multi-Modal Perception from Subterranean to Celestial", "14:35", "15:05", speaker="Shehryar Khattak", context="Session 3", url=MON_WS_19_URL),
        curated_row("poster", "Poster Session 2", "15:05", "16:00", context="Coffee Break", url=MON_WS_19_URL),
        curated_row("panel", "Panel", "16:00", "16:45", url=MON_WS_19_URL),
    ],
    # Manually read from https://awesomedigitaltwin.github.io/2026_ICRA.html.
    "mon-ws-20": [
        curated_row("talk", "World Model Driven Embodied Intelligence", "09:30", "10:00", speaker="Hengshuang Zhao (The University of Hong Kong)", context="Keynote", url=MON_WS_20_URL),
        curated_row("talk", "Accelerate Physical AI with Simulation-Centric Data Engine", "10:00", "10:30", speaker="Steve Xie (Lightwheel)", context="Keynote", url=MON_WS_20_URL),
        curated_row("talk", "Physical Grounding of Generative Digital Twins for Robotics", "11:00", "11:30", speaker="Jiajun Wu (Stanford)", context="Keynote", url=MON_WS_20_URL),
        curated_row("talk", "Removing the Barriers to Simulation Adoption with Automated Environment Construction and Synthetic Data Generation", "11:30", "12:00", speaker="Ajay Mandlekar (NVIDIA)", context="Keynote", url=MON_WS_20_URL),
        curated_row("spotlight", "Spotlight talks", "12:00", "12:30", url=MON_WS_20_URL),
        curated_row("poster", "Poster session", "12:30", "14:00", context="Lunch", url=MON_WS_20_URL),
        curated_row("talk", "Digital Twins for Embodied AI: Advancing the Frontier of Realism and Interaction", "14:00", "14:30", speaker="Manolis Savva (SFU)", context="Keynote", url=MON_WS_20_URL),
        curated_row("talk", "Keynote", "14:30", "15:00", speaker="Ingmar Posner (Oxford)", url=MON_WS_20_URL),
        curated_row("talk", "Real2Render2Real: Scaling Robot Data Without Dynamics Simulation or Robot Hardware", "15:00", "15:30", speaker="Ken Goldberg (UC Berkeley)", context="Keynote", url=MON_WS_20_URL),
        curated_row("talk", "Keynote", "16:00", "16:30", speaker="Oier Mees (Microsoft)", url=MON_WS_20_URL),
        curated_row("panel", "Discussion", "16:30", "17:30", speaker="Hengshuang Zhao, Steve Xie, Ajay Mandlekar, Jiajun Wu, Manolis Savva, Ingmar Posner, Ken Goldberg, Oier Mees", url=MON_WS_20_URL),
    ],
    # Manually read from https://www.ellipsis-venture.com/icra2026/.
    "mon-ws-21": [
        curated_row("talk", "Quick Exercise: Crossing the Desert with a Bottle of Water", "09:10", "09:20", url=MON_WS_21_URL),
        curated_row("talk", "ATEC Robotics Competition Presentation", "09:20", "09:30", speaker="Prof. Li", url=MON_WS_21_URL),
        curated_row("talk", "How investors evaluate robotics; hot now vs 2028", "09:30", "09:45", speaker="Dr. Robert MacKenzie", context="Talk 1", url=MON_WS_21_URL),
        curated_row("talk", "Case A: A visiting startup", "09:45", "10:15", url=MON_WS_21_URL),
        curated_row("talk", "Talk 2", "11:00", "11:20", url=MON_WS_21_URL),
        curated_row("talk", "Case B", "11:20", "11:45", url=MON_WS_21_URL),
        curated_row("talk", "Case C", "11:45", "12:10", url=MON_WS_21_URL),
        curated_row("talk", "Summary", "12:20", "12:30", speaker="Robert MacKenzie", url=MON_WS_21_URL),
    ],
    # Manually read from https://xingxingzuo.github.io/MM-SpatialAI/.
    "mon-ws-23": [
        curated_row("talk", "Invited Talk 1", "09:10", "09:40", speaker="Andrew Davison", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 2", "09:40", "10:10", speaker="Dezhen Song", url=MON_WS_23_URL),
        *curated_papers("lightning", "10:10", "10:30", MON_WS_23_POSTER_1[:4], context="Contributed Paper Session & Poster Lightning Talks 1", url=MON_WS_23_URL),
        *curated_papers("poster", "10:30", "11:00", MON_WS_23_POSTER_1, context="Morning Coffee & Poster Session", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 3", "11:00", "11:30", speaker="Margarita Chli", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 4", "11:30", "12:00", speaker="Alex Wong", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 5", "12:00", "12:30", speaker="Hermann Blum", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 6", "14:00", "14:30", speaker="Timothy D. Barfoot", url=MON_WS_23_URL),
        *curated_papers("lightning", "14:30", "15:10", MON_WS_23_POSTER_2[:8], context="Contributed Paper Session & Poster Lightning Talks 2", url=MON_WS_23_URL),
        curated_row("talk", "Sponsors & TABxStartups Introductions", "15:10", "15:30", context="6 x ~3 min", url=MON_WS_23_URL),
        *curated_papers("poster", "15:30", "16:00", MON_WS_23_POSTER_2, context="Afternoon Coffee & Poster Session", url=MON_WS_23_URL),
        curated_row("talk", "Invited Talk 7", "16:00", "16:30", speaker="Sebastian Scherer", url=MON_WS_23_URL),
        curated_row("panel", "Panel Discussion", "16:30", "17:00", url=MON_WS_23_URL),
    ],
    # Manually read from the SPA bundle at mananlab.tech/workshop.
    "mon-ws-24": [
        curated_row("talk", "Safe AI: from benchmarking to understanding model behavior", "09:00", "09:30", speaker="Prof. Mykola Pechenizkiy", url=MON_WS_24_URL),
        curated_row("talk", "Embodied Interactive Intelligence Towards Autonomous Driving", "09:30", "10:00", speaker="Prof. Nan Ma", url=MON_WS_24_URL),
        curated_row("talk", "Multimodal Dexterous Hand Manipulation for Human-Robot Interaction", "10:00", "10:30", speaker="Prof. Bin Fang", url=MON_WS_24_URL),
        curated_row("poster", "Coffee Break and Poster Session", "10:30", "11:00", context="Schedule image present on source page", url=MON_WS_24_URL),
        curated_row("talk", "Conversational AI in Industrial Human-Robot Interaction", "11:00", "11:30", speaker="Prof. Chen Li", url=MON_WS_24_URL),
        curated_row("talk", "Visual-Linguistic Multimodal Understanding in Robots", "11:30", "12:00", speaker="Dr. Zhixuan Wu", url=MON_WS_24_URL),
    ],
    # Manually read from https://sites.google.com/view/sustainability-robotics/schedule.
    # The Google Sites DOM duplicates each row as both a combined row and separate
    # time/event fragments, so the generic crawler splits the first keynote into
    # "Keynote Talks: Dr" plus a duplicate full row. Keep the human-verified rows.
    "mon-ws-25": [
        {
            "kind": "talk",
            "title": "Keynote Talks",
            "speaker": "Dr. Asya Ilgün, Prof. Drews Paulo, Dr. Laura Margheri",
            "start": "09:00",
            "end": "09:40",
            "time": "09:00-09:40",
            "context": "",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
        {
            "kind": "panel",
            "title": "Panel Discussion",
            "speaker": "",
            "start": "09:40",
            "end": "10:00",
            "time": "09:40-10:00",
            "context": "",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
        {
            "kind": "poster",
            "title": "Poster Presentation",
            "speaker": "",
            "start": "10:00",
            "end": "10:30",
            "time": "10:00-10:30",
            "context": "",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
        {
            "kind": "talk",
            "title": "Keynote Talks",
            "speaker": "Prof. Josie Hughes, Prof. Cecilia Laschi, Prof. Zufferey",
            "start": "11:00",
            "end": "11:40",
            "time": "11:00-11:40",
            "context": "Encourage connection establishment between ECR & Seniors",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
        {
            "kind": "panel",
            "title": "Panel discussion",
            "speaker": "",
            "start": "12:00",
            "end": "12:10",
            "time": "12:00-12:10",
            "context": "",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
        {
            "kind": "panel",
            "title": "Round tables",
            "speaker": "",
            "start": "12:10",
            "end": "13:00",
            "time": "12:10-13:00",
            "context": "",
            "url": "https://sites.google.com/view/sustainability-robotics/schedule",
        },
    ],
    # Manually read from https://icra2026rm.github.io/schedule.
    "mon-ws-26": [
        curated_row("talk", "Gil Weinberg Keynote", "09:00", "10:00", speaker="Gil Weinberg", url=MON_WS_26_URL),
        curated_row("talk", "Introduction to Robotic Musicianship", "10:00", "10:30", url=MON_WS_26_URL),
        curated_row("talk", "Hands-on Workshop Subgroups", "10:30", "12:00", url=MON_WS_26_URL),
        curated_row("panel", "Robotic Musicianship Panel", "13:00", "14:00", url=MON_WS_26_URL),
        curated_row("talk", "Interact with Robotic Musicians", "14:00", "15:00", url=MON_WS_26_URL),
        curated_row("talk", "Discussion and Networking", "15:00", "16:00", url=MON_WS_26_URL),
    ],
    # Manually read from https://icra2026-rigorous-perception.github.io/.
    "mon-ws-27": [
        curated_row("talk", "Rigorous perception for single- and multi-robot systems: are we there yet?", "09:00", "09:30", speaker="Margarita Chli", context="Topic 1: Perception in Navigation", url=MON_WS_27_URL),
        curated_row("talk", "Invited talk", "09:30", "10:00", speaker="Xiaolong Wang", context="Topic 1: Perception in Navigation", url=MON_WS_27_URL),
        *curated_papers("spotlight", "10:00", "10:15", MON_WS_27_NAV_POSTERS[:3], context="Spotlight Talks", url=MON_WS_27_URL),
        *curated_papers("poster", "10:15", "11:00", MON_WS_27_NAV_POSTERS, context="Coffee Break and Poster Session", url=MON_WS_27_URL),
        curated_row("talk", "Invited talk", "11:00", "11:30", speaker="Kostas Alexis", context="Topic 1: Perception in Navigation", url=MON_WS_27_URL),
        curated_row("panel", "Roundtable Discussion", "11:30", "12:00", context="Topic 1: Perception in Navigation", url=MON_WS_27_URL),
        curated_row("talk", "From Modular Robotics Pipelines to Vision-Language-Action Systems: Lessons from Real-World Manipulation", "14:00", "14:30", speaker="Yu Xiang", context="Topic 2: Perception in Manipulation", url=MON_WS_27_URL),
        curated_row("talk", "Structured Robot Learning for Rigorous Manipulation: From Perception to Action", "14:30", "15:00", speaker="Georgia Chalvatzaki", context="Topic 2: Perception in Manipulation", url=MON_WS_27_URL),
        *curated_papers("spotlight", "15:00", "15:15", MON_WS_27_MANIP_POSTERS[:3], context="Spotlight Talks", url=MON_WS_27_URL),
        *curated_papers("poster", "15:15", "16:00", MON_WS_27_MANIP_POSTERS, context="Poster Session", url=MON_WS_27_URL),
        curated_row("talk", "Invited talk", "16:00", "16:30", speaker="Juxi Leitner", context="Topic 2: Perception in Manipulation", url=MON_WS_27_URL),
        curated_row("panel", "Roundtable Discussion", "16:30", "17:00", context="Topic 2: Perception in Manipulation", url=MON_WS_27_URL),
    ],
    # Manually read from https://sites.google.com/view/radar-robotics/.
    "mon-ws-28": [
        curated_row("talk", "Pushing the Limits of Radar-Centric Perception", "09:10", "09:40", speaker="Daniel Casado Herraez (CARIAD SE)", url=MON_WS_28_URL),
        curated_row("talk", "Radar Sensing Applications in Industrial Mining Environments", "09:40", "10:10", speaker="Masrur Doostdar (Xtonomy)", url=MON_WS_28_URL),
        *curated_papers("poster", "10:10", "10:40", MON_WS_28_POSTERS, context="Coffee break & poster session", url=MON_WS_28_URL),
        curated_row("talk", "Shifting Input Paradigms in 4D Radar AI: From Point Clouds to Raw ADC.", "10:40", "11:10", speaker="Dong-Hee Paek (KAIST)", url=MON_WS_28_URL),
        *curated_papers("lightning", "11:10", "11:50", MON_WS_28_POSTERS, context="Poster lightning talks", url=MON_WS_28_URL),
        curated_row("talk", "Radar Gaussian Splatting for High-Fidelity Data Synthesis and 3D Reconstruction of Autonomous Driving Scenes", "13:10", "13:40", speaker="Pou-Chun Kung (University of Michigan)", url=MON_WS_28_URL),
        curated_row("talk", "Other Modalities Matter: Unlocking the Potential of 4D Radar via Multi-Modal Fusion and Cross-Modal Learning", "13:40", "14:10", speaker="Prof. Liang Hu (Harbin Institute of Technology)", url=MON_WS_28_URL),
        curated_row("talk", "Imaging radar: new type of eyes for robots", "14:10", "14:40", speaker="Gor Hakobyan (Waveye)", url=MON_WS_28_URL),
        *curated_papers("poster", "14:40", "15:10", MON_WS_28_POSTERS, context="Coffee break & poster session", url=MON_WS_28_URL),
        curated_row("talk", "Competition results & presentation from winning team(s)", "15:15", "15:35", url=MON_WS_28_URL),
        curated_row("talk", "Radars for Autonomous Driving, Theory vs Practice", "15:35", "16:05", speaker="Donnie Smith (Waymo)", url=MON_WS_28_URL),
        curated_row("panel", "Roundtable discussion", "16:05", "16:40", url=MON_WS_28_URL),
    ],
    # Manually read from https://sites.google.com/view/icra-2026-sdrl-workshop.
    "mon-ws-31": [
        curated_row("talk", "Invited talk", "09:10", "09:35", speaker="Justin Carpentier (INRIA Paris)", context="Morning Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "09:35", "10:00", speaker="Ming Lin (University of Maryland & Amazon)", context="Morning Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "10:00", "10:25", speaker="Jiajun Wu (Stanford University)", context="Morning Session", url=MON_WS_31_URL),
        *curated_papers("poster", "10:25", "10:55", MON_WS_31_PAPERS, context="Coffee Break and Poster Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "10:55", "11:20", speaker="Sergey Zakharov (Toyota Research Institute)", context="Morning Session", url=MON_WS_31_URL),
        curated_row("panel", "Panel Discussion", "11:20", "11:50", context="Morning Session", url=MON_WS_31_URL),
        *curated_papers("lightning", "11:50", "12:30", MON_WS_31_PAPERS, context="Poster Flash Presentations (2 minutes each)", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "13:40", "14:05", speaker="Angela Schoellig (Technical University of Munich)", context="Afternoon Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "14:05", "14:30", speaker="Jason Peng (Simon Fraser University & NVIDIA)", context="Afternoon Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "14:30", "14:55", speaker="Mansur Arief (KFUPM)", context="Afternoon Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "14:55", "15:20", speaker="Hugo Talbot (INRIA Nancy)", context="Afternoon Session", url=MON_WS_31_URL),
        *curated_papers("poster", "15:20", "15:55", MON_WS_31_PAPERS, context="Coffee Break and Poster Session", url=MON_WS_31_URL),
        curated_row("talk", "Invited talk", "15:55", "16:20", speaker="Maurizio Chiaramonte (Meta)", context="Afternoon Session", url=MON_WS_31_URL),
        curated_row("panel", "Panel Discussion", "16:20", "16:50", context="Afternoon Session", url=MON_WS_31_URL),
    ],
    # Manually read from https://alejandrofontan.github.io/The-Good-Reviewer-ICRA26/.
    "mon-ws-32": [
        curated_row("talk", "Overview of the Review Process", "09:05", "09:20", speaker="Aude Billard", url=MON_WS_32_URL),
        curated_row("talk", "How to review for doctoral students", "09:20", "09:40", speaker="Serena Ivaldi", url=MON_WS_32_URL),
        curated_row("talk", "AUTOLAB Advice on Reviewing Papers", "09:40", "10:00", speaker="Ken Goldberg", url=MON_WS_32_URL),
        curated_row("panel", "Panel I: Generative AI in the Review Process", "10:00", "10:30", speaker="Jeannette Bohg", url=MON_WS_32_URL),
        curated_row("talk", "Young Reviewers Program (YRP)", "11:00", "11:15", speaker="Marta Lorenzini", url=MON_WS_32_URL),
        curated_row("panel", "Panel II: Editors and AE Selection, IROS-ICRA Transfer, and Community Engagement", "11:15", "11:45", url=MON_WS_32_URL),
        curated_row("panel", "Panel III: Double Blind and Social Media", "11:45", "12:15", url=MON_WS_32_URL),
        curated_row("talk", "The Good Reviewer: Shaping up the future of peer-review process", "12:15", "12:30", url=MON_WS_32_URL),
    ],
    # Manually read from https://sites.google.com/view/sft-front.
    "mon-ws-33": [
        curated_row("talk", "1st Talk", "14:00", "14:30", speaker="Kyoungchul Kong (KAIST)", url=MON_WS_33_URL),
        curated_row("talk", "2nd Talk", "14:30", "14:50", speaker="Raye Yeow (NUS)", url=MON_WS_33_URL),
        curated_row("talk", "3rd Talk", "14:50", "15:10", speaker="Heike Vallery (RWTH Aachen)", url=MON_WS_33_URL),
        curated_row("talk", "4th Talk", "15:10", "15:30", speaker="KyuJin Cho (SNU)", url=MON_WS_33_URL),
        curated_row("poster", "Coffee Break & Award Ceremony", "15:30", "16:00", url=MON_WS_33_URL),
        curated_row("talk", "Young Speaker Talks", "16:00", "16:30", url=MON_WS_33_URL),
        curated_row("talk", "Invited talk", "16:30", "16:50", speaker="Haoyong Yu (NUS)", url=MON_WS_33_URL),
        curated_row("talk", "Invited talk", "16:50", "17:10", speaker="Allison Okamura (Stanford)", url=MON_WS_33_URL),
        curated_row("panel", "Panel Discussion", "17:10", "17:30", url=MON_WS_33_URL),
    ],
    # Manually read from https://large-area-tactile-sensing.github.io/.
    "mon-ws-34": [
        curated_row("talk", "Skin-Inspired Tactile Sensor Design and Fabrication", "14:05", "14:30", speaker="Prof. Zhenan Bao (Stanford University)", context="Keynote Talk 1", url=MON_WS_34_URL),
        curated_row("talk", "Keynote Talk 2", "14:30", "14:55", speaker="Prof. Yiyue Luo (University of Washington)", url=MON_WS_34_URL),
        curated_row("talk", "Recent Progress of Electronic Skins for Soft Robotics and Healthcare", "14:55", "15:20", speaker="Prof. Takao Someya (University of Tokyo)", context="Keynote Talk 3", url=MON_WS_34_URL),
        curated_row("spotlight", "Poster Flash Talk", "15:20", "15:30", speaker="Poster Authors", url=MON_WS_34_URL),
        curated_row("talk", "Industrial Talk", "15:50", "16:00", speaker="Sponsors", url=MON_WS_34_URL),
        curated_row("talk", "Deployment of Large-area Electronic Skin on Humanoid Robots", "16:00", "16:25", speaker="Prof. Gordon Cheng (TU Munich)", context="Keynote Talk 4", url=MON_WS_34_URL),
        curated_row("talk", "Keynote Talk 6", "16:25", "16:50", speaker="Prof. Kyungseo Park (DGIST)", url=MON_WS_34_URL),
        curated_row("talk", "Keynote Talk 5", "16:50", "17:15", speaker="Prof. Giorgio Cannata (University of Genova)", url=MON_WS_34_URL),
        curated_row("talk", "Keynote Talk 7", "17:15", "17:40", speaker="Prof. Rebecca Kramer-Bottiglio (Yale University)", url=MON_WS_34_URL),
    ],
    # Manually read from https://shanluo.github.io/ViTacWorkshops/vitac2026.
    "mon-ws-35": [
        curated_row("talk", "Multimodal Haptic Intelligence", "09:10", "09:30", speaker="Katherine J. Kuchenbecker", context="Session 1: Hardware Intelligence & Tactile Simulation", url=MON_WS_35_URL),
        curated_row("talk", "New contact sensors for new manipulators: design, integration and applications", "09:30", "09:50", speaker="Matei Ciocarlie", context="Session 1: Hardware Intelligence & Tactile Simulation", url=MON_WS_35_URL),
        curated_row("talk", "Force-Aware Policy Learning: From Sim-to-Real to Sim-Plus-Real", "09:50", "10:10", speaker="Ireti Akinola", context="Session 1: Hardware Intelligence & Tactile Simulation", url=MON_WS_35_URL),
        *curated_papers("spotlight", "10:10", "10:25", MON_WS_35_BEST_PAPERS, context="Best Papers Presentations", url=MON_WS_35_URL),
        *curated_papers("poster", "10:25", "10:45", MON_WS_35_PAPERS, context="Coffee Break and Poster Session", url=MON_WS_35_URL),
        curated_row("talk", "Industry Speakers", "10:45", "11:10", speaker="Vsim, Sharpa, Dobot, Daimon, and Xense", context="5 min each", url=MON_WS_35_URL),
        curated_row("talk", "Mind the Gap: between the Mind and the World", "11:10", "11:30", speaker="Yan Wu", context="Session 2: Robot Dexterity with Visuo-Tactile Fusion", url=MON_WS_35_URL),
        curated_row("talk", "The role of tactile when doing robotic manipulation at scale", "11:30", "11:50", speaker="Maria Bauza", context="Session 2: Robot Dexterity with Visuo-Tactile Fusion", url=MON_WS_35_URL),
        curated_row("panel", "Future of Visual-Tactile Intelligence", "11:50", "12:25", url=MON_WS_35_URL),
    ],
    # Manually read from the Agri-Food Robotics workshop schedule page.
    "mon-ws-36": [
        curated_row("talk", "Agricultural Field Robotics", "09:10", "09:35", speaker="Salah Sukkarieh", context="Keynote", url=MON_WS_36_URL),
        *curated_papers("paper", "09:35", "10:05", MON_WS_36_ORAL_1, context="Oral talks", url=MON_WS_36_URL),
        *curated_papers("lightning", "10:05", "10:35", MON_WS_36_POSTER_1, context="Poster pitches", url=MON_WS_36_URL),
        *curated_papers("poster", "10:35", "11:05", MON_WS_36_POSTER_1, context="Coffee break + poster session1", url=MON_WS_36_URL),
        curated_row("talk", "Perception and Cognition for Ag Robots", "11:05", "11:30", speaker="Lazaros Nalpantidis", context="Keynote", url=MON_WS_36_URL),
        curated_row("talk", "Open-World Robotics", "11:30", "11:55", speaker="Kris Hauser", context="Keynote", url=MON_WS_36_URL),
        curated_row("talk", "Bio-Inspired Soft Robotics", "11:55", "12:20", speaker="Sachin Sachin / Barbara Mazzolai", context="Keynote", url=MON_WS_36_URL),
        curated_row("talk", "Photogrammetry and robotics in agriculture", "13:40", "14:05", speaker="Cyrill Stachniss", context="Keynote", url=MON_WS_36_URL),
        curated_row("talk", "Robot control and locomotion in challenging environments", "14:05", "14:30", speaker="Claudio Semini", context="Keynote", url=MON_WS_36_URL),
        *curated_papers("paper", "14:30", "15:00", MON_WS_36_ORAL_2, context="Oral talks", url=MON_WS_36_URL),
        *curated_papers("lightning", "15:00", "15:30", MON_WS_36_POSTER_2, context="Poster pitches", url=MON_WS_36_URL),
        *curated_papers("poster", "15:30", "16:00", MON_WS_36_POSTER_2, context="Coffee break + poster session2", url=MON_WS_36_URL),
        curated_row("talk", "Workshop", "16:00", "17:00", url=MON_WS_36_URL),
        curated_row("talk", "Agricultural field robotics", "17:00", "17:25", speaker="Stavros Vougioukas", context="Keynote", url=MON_WS_36_URL),
    ],
    # Manually read from the Frontiers of Optimization schedule page.
    "mon-ws-37": [
        curated_row("talk", "Numerical Optimal Control for Nonsmooth Systems", "09:00", "09:30", speaker="Christian Dietz and Moritz Diehl", context="Numerical Methods and Differentiability", url=MON_WS_37_URL),
        curated_row("talk", "A gap penalty semismooth method for QPCC with applications to contact-implicit trajectory optimization.", "09:30", "10:00", speaker="Toshiyuki Ohtsuka", context="Numerical Methods and Differentiability", url=MON_WS_37_URL),
        curated_row("talk", "Invited talk", "10:00", "10:30", speaker="Justin Carpentier", context="Numerical Methods and Differentiability", url=MON_WS_37_URL),
        curated_row("lightning", "Lightning Talks", "10:30", "10:45", url=MON_WS_37_URL),
        curated_row("poster", "Coffee Break & Posters", "10:45", "11:30", url=MON_WS_37_URL),
        curated_row("talk", "Beyond Discrete-Time MPC: Nonlinear Lifting and Intersample Optimization in Robotics", "11:30", "12:00", speaker="Kaoru Yamamoto (remote)", context="Learning and Optimization I", url=MON_WS_37_URL),
        curated_row("talk", "Non-smooth control for MuJoCo physics", "12:00", "12:30", speaker="Emo Todorov", context="Learning and Optimization I", url=MON_WS_37_URL),
        curated_row("talk", "Invited talk", "13:30", "14:00", speaker="Farbod Farshidian", context="Learning and Optimization II", url=MON_WS_37_URL),
        curated_row("talk", "Invited talk", "14:00", "14:30", speaker="Melanie Zeilinger", context="Learning and Optimization II", url=MON_WS_37_URL),
        curated_row("lightning", "Lightning Talks", "14:30", "14:45", url=MON_WS_37_URL),
        curated_row("poster", "Mentoring Breakout, Coffee Break & Posters", "14:45", "16:00", url=MON_WS_37_URL),
        curated_row("talk", "Graphs of Convex Sets: A New Framework for Efficient Discrete-Continuous Optimization", "16:00", "16:30", speaker="Tobia Marcucci", context="Global Optimality and Zero-Order Methods", url=MON_WS_37_URL),
        curated_row("talk", "Scaling Semidefinite Relaxations for Robot Perception and Control", "16:30", "17:00", speaker="Heng Yang", context="Global Optimality and Zero-Order Methods", url=MON_WS_37_URL),
        curated_row("talk", "Invited talk", "17:00", "17:30", speaker="Evangelos Theodorou", context="Global Optimality and Zero-Order Methods", url=MON_WS_37_URL),
        curated_row("panel", "Panel Discussion", "17:30", "18:00", url=MON_WS_37_URL),
    ],
    # Manually read from https://workshop-pbp2026.github.io/.
    "mon-ws-38": [
        curated_row("talk", "Invited Talk", "14:10", "14:40", speaker="Prof. Hubert P. H. Shum", url=MON_WS_38_URL),
        curated_row("talk", "Invited Talk", "14:40", "15:10", speaker="Prof. Philippe Martinet", url=MON_WS_38_URL),
        curated_row("talk", "Invited Talk", "15:10", "15:40", speaker="Dr. Amir Rasouli", url=MON_WS_38_URL),
        curated_row("talk", "Invited Talk", "15:40", "16:10", speaker="Prof. Alexandre Alahi", url=MON_WS_38_URL),
        curated_row("poster", "Coffee Break + Poster Session", "16:10", "16:30", url=MON_WS_38_URL),
        curated_row("paper", "Paper Presentation (including Brave New Ideas)", "16:30", "17:10", url=MON_WS_38_URL),
        curated_row("talk", "Sponsor Remarks", "17:10", "17:20", url=MON_WS_38_URL),
    ],
    # Manually read from https://rl4il-icra.github.io/.
    "mon-ws-39": [
        curated_row("talk", "Invited Talk", "09:00", "09:30", speaker="Robert Platt", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "09:30", "10:00", speaker="Pulkit Agrawal", url=MON_WS_39_URL),
        *curated_papers("lightning", "10:00", "10:30", MON_WS_39_ORALS[:6], context="Lightning Talks I (L1-L6)", url=MON_WS_39_URL),
        *curated_papers("poster", "10:30", "11:00", MON_WS_39_ORALS[:6] + MON_WS_39_POSTERS[:6], context="Coffee break + Posters I (L1-L6 and P1-P6)", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "11:00", "11:30", speaker="Georgia Chalvatzaki", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "11:30", "12:00", speaker="Jason Ma", url=MON_WS_39_URL),
        curated_row("panel", "Reinforce or Imitate: Practical Challenges in RL with Real World Data", "12:00", "12:30", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "14:00", "14:30", speaker="David Held", url=MON_WS_39_URL),
        *curated_papers("lightning", "14:30", "15:00", MON_WS_39_ORALS[6:], context="Lightning Talks II (L7-L11)", url=MON_WS_39_URL),
        *curated_papers("poster", "15:00", "15:30", MON_WS_39_ORALS[6:] + MON_WS_39_POSTERS[6:], context="Coffee break + Posters II (L7-L11 and P7-P13)", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "15:30", "16:00", speaker="Sergey Levine", url=MON_WS_39_URL),
        curated_row("talk", "Invited Talk", "16:00", "16:30", speaker="Chelsea Finn and Perry Dong", url=MON_WS_39_URL),
    ],
    # Manually read from https://cr2-icra.github.io.
    "mon-ws-40": [
        curated_row("panel", "Panel 1: Contact Model Representations", "09:15", "10:15", speaker="Nima Fazeli, Marc Toussaint, Yuval Tassa, Yilun Du", url=MON_WS_40_URL),
        *curated_papers("poster", "10:15", "11:00", MON_WS_40_POSTERS, context="Coffee break + poster session (all accepted posters)", url=MON_WS_40_URL),
        *curated_papers("spotlight", "11:00", "12:00", MON_WS_40_POSTERS[:3], context="Spotlight talks: Simulation, Modeling, and Learning", url=MON_WS_40_URL),
        curated_row("panel", "Panel 2: Algorithms for Contact-Rich Control", "13:30", "14:30", speaker="Justin Carpentier, Bibit Bianchini, Hae-Won Park, Emo Todorov", url=MON_WS_40_URL),
        *curated_papers("spotlight", "14:30", "15:30", MON_WS_40_POSTERS[3:7], context="Spotlight talks: Control", url=MON_WS_40_URL),
        *curated_papers("poster", "15:30", "16:15", MON_WS_40_POSTERS, context="Coffee break + poster session (all accepted posters)", url=MON_WS_40_URL),
        curated_row("panel", "Panel 3: State and Future for Contact-Rich Control", "16:15", "17:15", speaker="Matt Mason, Aaron Ames, Aaron Johnson, Dmitry Berenson", url=MON_WS_40_URL),
    ],
    # Friday workshops/tutorials, manually read from linked program pages.
    "fri-ws-41": [
        curated_row("talk", "Overview of 3D force sensing and gripper-focused applications", "14:00", "15:00", context="Presentation", url=FRI_WS_41_URL),
        curated_row("poster", "Coffee break", "15:00", "15:15", url=FRI_WS_41_URL),
        curated_row("talk", "Hands-on session", "15:15", "17:15", context="Teams build a two-finger gripper and integrate the 3D tactile sensor", url=FRI_WS_41_URL),
    ],
    "fri-ws-42": [
        curated_row("talk", "Connecting GNSS and Robotics: Toward Tightly Coupled Sensor Integration", "09:00", "09:30", speaker="Dr. Taro Suzuki", context="Research Session I", url=FRI_WS_42_URL),
        curated_row("talk", "Ranging-Aided Localization: on the Ground, among the Drones, and into a Neural Network", "09:30", "10:00", speaker="Prof. Dr. Thien-Minh Nguyen", context="Research Session I", url=FRI_WS_42_URL),
        curated_row("talk", "Equivariant Estimators: Using Symmetries to Cope with Challenging Ranging Observations", "10:00", "10:30", speaker="Prof. Dr. Stephan Weiss", context="Research Session I", url=FRI_WS_42_URL),
        *curated_papers("spotlight", "10:45", "12:00", FRI_WS_42_PAPERS, context="20 Spotlight Talks (3 min/pers)", url=FRI_WS_42_URL),
        *curated_papers("poster", "12:00", "13:30", FRI_WS_42_PAPERS, context="Poster Session and Lunch Break", url=FRI_WS_42_URL),
        curated_row("talk", "Sponsor Pitch Presentation", "13:30", "13:45", speaker="Sponsors", context="Industry Session", url=FRI_WS_42_URL),
        curated_row("talk", "Start-Up Presentation: Neuraloc", "13:45", "14:00", speaker="Dr.-Ing. Fabian Ruwisch", context="Industry Session", url=FRI_WS_42_URL),
        curated_row("talk", "Bridging navigation safety: from aviation to unmanned ground vehicles", "14:00", "14:30", speaker="Prof. Dr. Matthew Spenko", context="Research Session II", url=FRI_WS_42_URL),
        curated_row("talk", "Industrial Indoor Localization - Challenges and Trends", "14:30", "15:00", speaker="Dr. Tim Pfeifer", context="Research Session II", url=FRI_WS_42_URL),
        curated_row("talk", "Safety-quantifiable Joint Positioning and Control for Factor Graph", "15:15", "15:45", speaker="Prof. Dr. Weisong Wen", context="Research Session II", url=FRI_WS_42_URL),
        curated_row("talk", "Initialization, Uncertainty, and Accuracy in Range-Based Trajectory Estimation", "15:45", "16:15", speaker="Dr. Abhishek Goudar", context="Research Session II", url=FRI_WS_42_URL),
        curated_row("talk", "No GPS, No Problem: Exploiting Signals of Opportunity for Resilient and Accurate Autonomous Vehicle Navigation in GPS-Denied Environments", "16:15", "16:45", speaker="Prof. Dr. Zak Kassas", context="Research Session II", url=FRI_WS_42_URL),
        curated_row("panel", "Ranging-Inspired Problems in Robotics: system, integrity, and transferability to other sensor modalities", "16:45", "17:30", speaker="Dr. Daniel Medina", url=FRI_WS_42_URL),
    ],
    "fri-ws-43": [
        curated_row("talk", "Why Reproducibility Matters. 20 more years?", "09:05", "09:30", speaker="Prof. Fabio Bonsignorio", url=FRI_WS_43_URL),
        curated_row("talk", "Parece que fue ayer... State of Benchmarking in Robotics", "09:30", "10:00", speaker="Prof. Angel P. del Pobil", url=FRI_WS_43_URL),
        curated_row("talk", "The R-articles; Reproducibility in IEEE RAM and beyond", "10:00", "10:30", speaker="Dr. Enrica Zereik", url=FRI_WS_43_URL),
        curated_row("talk", "Reproducibility in Robotics Surgery: our small review in 2023", "11:00", "11:30", speaker="Dr. Angela Faragasso", url=FRI_WS_43_URL),
        curated_row("talk", "The Role of Competitions", "11:30", "12:00", speaker="Prof. Pedro Lima", url=FRI_WS_43_URL),
        curated_row("talk", "Improving Reproducibility in Machine Learning Research", "12:00", "12:30", speaker="Prof. Joelle Pineau", url=FRI_WS_43_URL),
        curated_row("talk", "Reproducibility in mobile robotics: our small review in 2009. Did we make any progress since then?", "14:00", "14:30", speaker="Prof. Francesco Amigoni", url=FRI_WS_43_URL),
        curated_row("talk", "The first R-Article in 2019 on R&A Mag. Lessons Learned", "14:30", "15:00", speaker="Prof. Stefano Carpin", url=FRI_WS_43_URL),
        curated_row("talk", "The NSF Compare project. Lesson Learned and Road Ahead", "15:00", "15:30", speaker="Prof. Adam Norton", url=FRI_WS_43_URL),
        curated_row("talk", "Software Platforms for Reproducibility in Robotics", "16:00", "16:30", speaker="Prof. Enric Cervera", url=FRI_WS_43_URL),
        curated_row("talk", "Breakout Sessions", "16:30", "17:20", speaker="All", url=FRI_WS_43_URL),
        curated_row("panel", "Final Discussion", "17:20", "18:00", speaker="All", url=FRI_WS_43_URL),
    ],
    "fri-ws-44": [
        curated_row("talk", "Cyber-Archaeology through 3D Vision Technologies", "09:10", "09:40", speaker="Takeshi Oishi", context="Invited Talk 1", url=FRI_WS_44_URL),
        curated_row("talk", "From fixed setups to adaptive robotics: Hyperspectral Imaging for Cultural Heritage", "09:40", "10:10", speaker="Agnese Babini", context="Invited Talk 2", url=FRI_WS_44_URL),
        curated_row("paper", "Autonomous multi-session RGB-thermal mapping of historical multi-storey buildings for conservation monitoring", "10:10", "10:30", speaker="Antonio Adan", context="Contributed Talk", url=FRI_WS_44_URL),
        curated_row("talk", "Heritage ROSS: Toward Automated Acquisition and Integrated Analysis of Heterogeneous Multisensory Data for the Preservation of Cultural Heritage", "11:00", "11:20", speaker="Alexander Bornik", context="Invited Talk 3", url=FRI_WS_44_URL),
        curated_row("talk", "Challenges in Robotic Manipulation for Fresco Assembly", "11:20", "11:50", speaker="Maren Bennewitz", context="Invited Talk 4", url=FRI_WS_44_URL),
        curated_row("talk", "I, AUTOMATA. What if archaeology met robotics?", "11:50", "12:20", speaker="Gabriele Gattiglia", context="Invited Talk 5", url=FRI_WS_44_URL),
    ],
    "fri-ws-45": [
        curated_row("talk", "Opening Remarks and Keynote", "09:00", "09:10", speaker="Prof. Yong-Lae Park", url=FRI_WS_45_URL),
        curated_row("talk", "Industry talk", "09:10", "09:20", speaker="Qiu'ang Li, HopTo Tech", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "09:20", "09:50", speaker="Prof. Mark Yim", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "09:50", "10:20", speaker="Prof. Kento Kawaharazuka", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "10:20", "10:35", speaker="Prof. Nathan Usevitch", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "11:00", "11:30", speaker="Prof. Perla Maiolino", url=FRI_WS_45_URL),
        curated_row("talk", "Student speaker", "11:30", "11:45", speaker="Ezra Ben Abu", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "11:45", "12:00", speaker="Prof. Will Johnson", url=FRI_WS_45_URL),
        curated_row("lightning", "Lightning talks", "12:00", "12:40", context="2 minutes - Student participants", url=FRI_WS_45_URL),
        curated_row("poster", "Lunch Break and poster presentations", "12:40", "13:30", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "13:30", "14:00", speaker="Prof. Kaushik Jayaram", url=FRI_WS_45_URL),
        curated_row("talk", "Student speaker", "14:00", "14:15", speaker="Shashwat Singh", url=FRI_WS_45_URL),
        curated_row("panel", "Debate", "14:15", "15:00", speaker="Prof. Robert Baines", context="Morphology vs Control", url=FRI_WS_45_URL),
        curated_row("talk", "Invited talk", "15:00", "15:30", speaker="Prof. Kyujin Cho", url=FRI_WS_45_URL),
        curated_row("panel", "Panel Discussion", "16:00", "16:30", speaker="Prof. Lillian Chin; Perla Maiolino, Kyujin Cho, Kaushik Jayaram, Nathan Usevitch, Yong-Lae Park", url=FRI_WS_45_URL),
    ],
    "fri-ws-46": [
        curated_row("talk", "Bioinspired Control and Wearable Intelligence for Adaptive Human-Machine Systems", "09:00", "09:30", speaker="Silvia Tolu", url=FRI_WS_46_URL),
        curated_row("talk", "What Human-Robot Interaction Assumes About the Brain -- Lessons from body restoration and augmentation", "09:30", "10:00", speaker="Tamar Makin", url=FRI_WS_46_URL),
        curated_row("talk", "Invited talk", "10:00", "10:30", speaker="Shunichi Kasahara", url=FRI_WS_46_URL),
        curated_row("poster", "Coffee Break (Poster and NeuroDesign Showcase Demo)", "10:30", "10:45", url=FRI_WS_46_URL),
        curated_row("talk", "Incorporating Human Models into Robot Control Loop for Adaptive Human-Robot Co-Manipulation", "10:45", "11:15", speaker="Luka Peternel", url=FRI_WS_46_URL),
        curated_row("talk", "Invited talk", "11:15", "11:45", speaker="Cesco Willemse", url=FRI_WS_46_URL),
        curated_row("talk", "Industry Highlight Talks & Demo", "11:45", "12:15", speaker="All experts", url=FRI_WS_46_URL),
        curated_row("poster", "Lunch Break (Poster and NeuroDesign Showcase Demo)", "12:15", "13:00", url=FRI_WS_46_URL),
        curated_row("talk", "One Decade of Cybathlon: Bridging Robotics, Rehab, and the Society", "13:00", "13:30", speaker="Robert Riener", url=FRI_WS_46_URL),
        curated_row("talk", "Meaning at a Distance", "13:30", "14:00", speaker="Stefano Caggiano", url=FRI_WS_46_URL),
        curated_row("talk", "Open Your Mind: Brain-Computer Interfaces", "14:00", "14:30", speaker="Nataliya Kosmyna", url=FRI_WS_46_URL),
        curated_row("poster", "Coffee Break (Poster and NeuroDesign Showcase Demo)", "14:30", "14:45", url=FRI_WS_46_URL),
        curated_row("talk", "NeuroDesign in HRI Innovation Expo & Competition", "14:45", "15:45", speaker="Competition teams", url=FRI_WS_46_URL),
        curated_row("talk", "Trust as Interaction: A Neuroergonomics View of Human Robot Teaming", "15:45", "16:15", speaker="Ranjana Mehta", url=FRI_WS_46_URL),
        curated_row("talk", "Helpful or Harmful? Exploring the Impact of Fostering Emotional Connections between Children and Educational Robots", "16:15", "16:45", speaker="Sarah Sebo", url=FRI_WS_46_URL),
        curated_row("talk", "Invited talk", "16:45", "17:15", speaker="Grace Leslie", url=FRI_WS_46_URL),
        curated_row("panel", "Panel Discussion", "17:15", "18:00", speaker="All Experts", url=FRI_WS_46_URL),
    ],
    # Source lists confirmed speakers, but says the detailed program will be announced soon.
    "fri-ws-47": [
        curated_row("talk", "Invited talk", "09:00", "09:30", speaker="Katherine Driggs-Campbell, University of Illinois", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "09:30", "10:00", speaker="Dana Kulic, Monash University", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "10:00", "10:30", speaker="Jean Oh, Carnegie Mellon University", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "11:00", "11:30", speaker="Lukas Schmid, Technische Universitat Nurnberg", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "11:30", "12:00", speaker="Kashyap Chitta, NVIDIA", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "12:00", "12:30", speaker="Johannes Betz, TUM", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "14:00", "14:30", speaker="Tim Pfeifer, Siemens AG", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "14:30", "15:00", speaker="David Woollard, Standard AI", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
        curated_row("talk", "Invited talk", "15:00", "15:30", speaker="Alessandro Corbetta, TU/e", context="Speaker list; source has no timed program", url=FRI_WS_47_URL),
    ],
    "fri-ws-48": [
        curated_row("talk", "Situated Heuristics-Based Evolution of Mechanically Intelligent Systems (SHAPE)", "14:10", "14:30", speaker="Thrishantha Nanayakkara", context="Session I: Methods & Architectures", url=FRI_WS_48_URL),
        curated_row("talk", "Vision-Based Parametrization of the Digital Twin in Dynamic Environments through Analysis of Action Constraints", "14:30", "14:50", speaker="Darius Burschka", context="Session I: Methods & Architectures", url=FRI_WS_48_URL),
        curated_row("lightning", "ArtiTwinSplat: Interactable Digital Twin Reconstruction via Gaussian Splatting from RGB-D Videos", "14:50", "14:55", speaker="Pranjal Mishra", url=FRI_WS_48_URL),
        curated_row("lightning", "Programming Manufacturing Robots with Imperfect AI: LLMs as Tuning Experts for FDM Print Configuration Selection", "14:55", "15:00", speaker="Ekta U. Samani", url=FRI_WS_48_URL),
        curated_row("lightning", "Multi-Agent Sequential Decision-Making for Autonomous Carpooling", "15:00", "15:05", speaker="Antonio Salcuni", url=FRI_WS_48_URL),
        curated_row("lightning", "When Digital Twins Meet Large Language Models: Realistic, Interactive, and Editable Simulation for Autonomous Driving", "15:05", "15:10", speaker="Tanmay Samak, Chinmay Samak", url=FRI_WS_48_URL),
        curated_row("poster", "Poster & Demo Session", "15:10", "15:25", speaker="All accepted contributors", url=FRI_WS_48_URL),
        curated_row("talk", "A Digital Twin Approach for a New Concept of Last-Mile Delivery", "15:40", "16:00", speaker="Agostino Marcello Mangini", context="Session II: Validation, Deployment & Applied Perspectives", url=FRI_WS_48_URL),
        curated_row("talk", "Dynamic Dense Packing of Novel Objects", "16:00", "16:20", speaker="Jing Xiao", url=FRI_WS_48_URL),
        curated_row("talk", "XR as an Interaction Digital Twin: Teaching, Expressing, and Validating Robots with Humans in the Loop", "16:20", "16:35", speaker="Chao Wang", context="Industry Invited Talk", url=FRI_WS_48_URL),
        curated_row("talk", "Agentic AI on the Manufacturing Floor - Robotic Intelligence and 3D Simulation for Autonomous Operations", "16:35", "16:40", speaker="Kevin Patel", context="Startup Spotlight", url=FRI_WS_48_URL),
        curated_row("talk", "pluma: Reliable AI Infrastructure for Industrial Automation", "16:40", "16:45", speaker="Michele Marvulli", context="Startup Spotlight", url=FRI_WS_48_URL),
        curated_row("talk", "Cognivix - Startup contribution (title TBA)", "16:45", "16:50", speaker="Daniele Bernardini", context="Startup Spotlight", url=FRI_WS_48_URL),
        curated_row("talk", "Needleye Robotics - Startup contribution (title TBA)", "16:50", "16:55", speaker="Paolo Fiorini", context="Startup Spotlight", url=FRI_WS_48_URL),
        curated_row("lightning", "A Digital Twin Framework for Vision-Guided Hose Manipulation in Pharmaceutical Automation", "16:55", "17:00", speaker="Thomas Becker", url=FRI_WS_48_URL),
        curated_row("lightning", "A Deployment Case Study in Robotic Apparel Automation: Digital Twin Integration, Interoperability, and Workforce Enablement", "17:00", "17:05", speaker="Gokul Narayanan", url=FRI_WS_48_URL),
        curated_row("lightning", "A Finite Element-Driven Intelligent Digital Twin of Milling Dynamics for Real-Time Monitoring and Chatter Suppression", "17:05", "17:10", speaker="Yi Huang", url=FRI_WS_48_URL),
        curated_row("lightning", "Uniform Modeling and Integration of Humans and Robots in Industrial Automation Systems", "17:10", "17:15", speaker="Dominik Hujo-Lauer", url=FRI_WS_48_URL),
        curated_row("lightning", "Orchestrating a Multi-Robot Micro-Factory: Cyber-Physical System for Timber Manufacturing", "17:15", "17:20", speaker="Zhenxiang Huang", url=FRI_WS_48_URL),
    ],
    "fri-ws-49": [
        curated_row("talk", "Embodied AI on Tiny Robots", "08:30", "09:45", speaker="Daniele Palossi", context="AI Paradigms for Safety in Aerial Robotics", url=FRI_WS_49_URL),
        curated_row("talk", "Reliable Visual Navigation, Anytime, Anywhere", "08:30", "09:45", speaker="Elia Kaufmann", context="AI Paradigms for Safety in Aerial Robotics", url=FRI_WS_49_URL),
        curated_row("talk", "Collision Avoidance in Aerial Vehicles", "08:30", "09:45", speaker="Nora Ayanian", context="AI Paradigms for Safety in Aerial Robotics", url=FRI_WS_49_URL),
        curated_row("paper", "Contributed papers / Oral presentations", "09:45", "10:30", url=FRI_WS_49_URL),
        *curated_papers("poster", "10:30", "11:00", FRI_WS_49_MORNING_POSTERS, context="Coffee Break and poster session", url=FRI_WS_49_URL),
        curated_row("talk", "Aggressive acrobatics in insect-scale aerial robots via deep-learned model predictive control", "11:00", "12:15", speaker="Kevin Chen", context="Soft & Morphing Aerial Robotics for Safe Interaction", url=FRI_WS_49_URL),
        curated_row("talk", "Soft & Morphing Aerial Robotics for Safe Interaction", "11:00", "12:15", speaker="Robert Katzschmann", context="Soft & Morphing Aerial Robotics for Safe Interaction", url=FRI_WS_49_URL),
        curated_row("talk", "Modular aerial robotics for physical interaction", "11:00", "12:15", speaker="Moju Zhao", context="Soft & Morphing Aerial Robotics for Safe Interaction", url=FRI_WS_49_URL),
        curated_row("talk", "Unlocking the potential of unconventional aerial robots", "13:00", "14:15", speaker="Sophie Armanini", context="Bio-Inspired Intelligence and Generative Design", url=FRI_WS_49_URL),
        curated_row("talk", "Generative Bio-Inspired Design for Adaptive Morphing Robots", "13:00", "14:15", speaker="Barbara Mazzolai", context="Bio-Inspired Intelligence and Generative Design", url=FRI_WS_49_URL),
        curated_row("talk", "Bio-Inspired Strategies for Agile and Resilient Miniature Aerial Robotics", "13:00", "14:15", speaker="Pakpong Chirarattananon", context="Bio-Inspired Intelligence and Generative Design", url=FRI_WS_49_URL),
        curated_row("talk", "From Safety Research to Deployment", "14:15", "15:30", context="Session", url=FRI_WS_49_URL),
        *curated_papers("poster", "15:30", "16:00", FRI_WS_49_AFTERNOON_POSTERS, context="Coffee Break and poster session", url=FRI_WS_49_URL),
        curated_row("panel", "Ethics & Regulation Panel", "16:00", "16:45", url=FRI_WS_49_URL),
    ],
    "fri-ws-50": [
        curated_row("talk", "Talk 1", "09:00", "09:30", speaker="Jitendra Malik", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 2", "09:30", "10:00", speaker="Edward Johns", url=FRI_WS_50_URL),
        curated_row("poster", "Break + Poster Session", "10:00", "11:00", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 3", "11:00", "11:30", speaker="Danfei Xu", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 4", "11:30", "12:00", speaker="Karen Liu", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 5", "12:00", "12:30", speaker="Katerina Fragkiadaki", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 6", "13:30", "14:00", speaker="Yue Wang", url=FRI_WS_50_URL),
        curated_row("talk", "Talk 7", "14:00", "14:30", speaker="Roberto Martín-Martín", url=FRI_WS_50_URL),
        curated_row("spotlight", "Student Spotlights", "14:30", "15:00", url=FRI_WS_50_URL),
        curated_row("poster", "Break + Poster Session", "15:00", "16:00", url=FRI_WS_50_URL),
        curated_row("talk", "Sponsor Talk: Lightwheel AI", "16:00", "16:20", url=FRI_WS_50_URL),
        curated_row("panel", "Panel Discussion", "16:20", "17:20", url=FRI_WS_50_URL),
    ],
    "fri-ws-51": [
        curated_row("talk", "Invited Speaker 1", "08:45", "09:15", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 2", "09:15", "09:45", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 3", "09:45", "10:15", url=FRI_WS_51_URL),
        curated_row("poster", "Coffee Break & Demo", "10:15", "11:00", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 4", "11:00", "11:30", url=FRI_WS_51_URL),
        curated_row("panel", "Debate", "11:30", "12:30", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 5", "13:45", "14:15", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 6", "14:15", "14:45", url=FRI_WS_51_URL),
        curated_row("spotlight", "Next Gen Spotlight", "14:45", "15:15", url=FRI_WS_51_URL),
        curated_row("poster", "Poster Session", "15:15", "16:00", url=FRI_WS_51_URL),
        curated_row("talk", "Interactive Session", "16:00", "16:30", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 7", "16:30", "17:00", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 8", "17:00", "17:30", url=FRI_WS_51_URL),
        curated_row("talk", "Invited Speaker 9", "17:30", "18:00", url=FRI_WS_51_URL),
    ],
    # Source lists invited speakers and accepted papers, but no timed program page is published.
    "fri-ws-52": [
        curated_row("talk", "Invited talk", "09:00", "09:30", speaker="Jean Ferre, Prophesee", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
        curated_row("talk", "Invited talk", "09:30", "10:00", speaker="Davide Scaramuzza, University of Zurich", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
        curated_row("talk", "Invited talk", "10:00", "10:30", speaker="Chiara Bartolozzi, Italian Institute of Technology", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
        *curated_papers("poster", "10:30", "11:00", FRI_WS_52_PAPERS, context="Accepted papers; source has no timed program", url=FRI_WS_52_URL),
        curated_row("talk", "Invited talk", "11:00", "11:30", speaker="Guido de Croon, Delft University of Technology", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
        curated_row("talk", "Invited talk", "11:30", "12:00", speaker="Yi Zhou, Hunan University", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
        curated_row("talk", "Invited talk", "12:00", "12:30", speaker="Paul Kirkland, International Centre for Neuromorphic Systems", context="Speaker list; source has no timed program", url=FRI_WS_52_URL),
    ],
    "fri-ws-53": [
        curated_row("talk", "Trustworthy Robotic Assistance in Minimally Invasive Surgery", "08:50", "09:20", speaker="Federica Ferraguti", url=FRI_WS_53_URL),
        curated_row("talk", "Endoluminal Robotics & Embodied AI in vivo", "09:20", "09:50", speaker="Hongliang Ren", url=FRI_WS_53_URL),
        curated_row("talk", "Are Humanoid Robots in Medicine Unrealistic or Are They Inevitable? A Technological Perspective", "09:50", "10:20", speaker="Michael Yip", url=FRI_WS_53_URL),
        *curated_papers("spotlight", "10:20", "10:35", FRI_WS_53_POSTER_1, context="Poster Presentation 1 (No.1-14)", url=FRI_WS_53_URL),
        *curated_papers("poster", "10:35", "11:05", FRI_WS_53_POSTER_1, context="Poster Session 1 and Coffee Break", url=FRI_WS_53_URL),
        curated_row("talk", "Collaborative Surgical Robotics in the Era of Embodied AI: The Maestro System", "11:05", "11:20", speaker="David Noonan", url=FRI_WS_53_URL),
        curated_row("talk", "Autonomy in Surgical Robotics: Future Challenges and Opportunities", "11:20", "11:50", speaker="Fanny Ficuciello", url=FRI_WS_53_URL),
        curated_row("talk", "AI and Robotics in Surgery: from Academia to MedTech", "11:50", "12:00", speaker="Paolo Fiorini", context="Startup Presentation 1", url=FRI_WS_53_URL),
        curated_row("talk", "Towards An Intelligent Robotic Endoscope Holder", "12:00", "12:30", speaker="Christos Bergeles", url=FRI_WS_53_URL),
        curated_row("talk", "Enhancing Surgical Task Autonomy Through Simulations and Robot Learning", "13:40", "14:10", speaker="Mahdi Tavakoli", url=FRI_WS_53_URL),
        curated_row("talk", "Startup Presentation 2", "14:10", "14:20", url=FRI_WS_53_URL),
        curated_row("talk", "AI-empowered autonomy from upper G.I. to ophthalmology", "14:20", "14:50", speaker="Riccardo Muradore", url=FRI_WS_53_URL),
        curated_row("talk", "Automation by Imitation: Capturing Embryologist Expertise for the Next Generation of IVF Robotics", "14:50", "15:05", speaker="Gerardo Mendizabal", url=FRI_WS_53_URL),
        curated_row("talk", "Startup Presentation 3", "15:05", "15:15", url=FRI_WS_53_URL),
        *curated_papers("spotlight", "15:15", "15:30", FRI_WS_53_POSTER_2, context="Poster Presentation 2 (No.15-28)", url=FRI_WS_53_URL),
        *curated_papers("poster", "15:30", "16:00", FRI_WS_53_POSTER_2, context="Poster Session 2 and Coffee Break", url=FRI_WS_53_URL),
        curated_row("talk", "AI healthcare automation: what can we truly rely on?", "16:00", "16:30", speaker="Elena De Momi", url=FRI_WS_53_URL),
        curated_row("talk", "Towards the Future of Surgery: Exploring Digital Surgery and Surgical AI", "16:30", "16:45", speaker="Lin Zhang", url=FRI_WS_53_URL),
        curated_row("panel", "Panel Discussion, Q&A, and Poster Award", "16:50", "17:40", url=FRI_WS_53_URL),
    ],
    "fri-ws-54": [
        curated_row("talk", "Human Factors for Manipulation Control under Extreme Constraints", "09:05", "10:30", context="Session I", url=FRI_WS_54_URL),
        *curated_papers("lightning", "10:20", "10:30", FRI_WS_54_POSTERS, context="Poster teasers", url=FRI_WS_54_URL),
        *curated_papers("poster", "10:30", "11:00", FRI_WS_54_POSTERS, context="Poster Session and Coffee Break", url=FRI_WS_54_URL),
        curated_row("talk", "Embodied Intelligence for Robust Grasping in Harsh Environments", "11:00", "12:00", context="Session II", url=FRI_WS_54_URL),
        curated_row("panel", "Round Table Discussion", "12:00", "12:30", url=FRI_WS_54_URL),
        curated_row("talk", "Networking Social: Drinks, Bites, & Brainstorming", "12:30", "13:30", url=FRI_WS_54_URL),
    ],
    # Source lists speakers and several talk titles, but no timed program page is published.
    "fri-ws-55": [
        curated_row("talk", "Humanoids in Healthcare and Welfare: A Careful Transition", "09:00", "09:30", speaker="Gordon Cheng, Technical University of Munich", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "09:30", "10:00", speaker="Shane Xie, University of Leeds", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "10:00", "10:30", speaker="Darwin Caldwell, Italian Institute of Technology", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Physical human-robot interaction for assistive and rehabilitation purposes: Can these be translated to elderly-support technologies?", "11:00", "11:30", speaker="Mustafa Suphi Erden, Heriot-Watt University", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "11:30", "12:00", speaker="Qi An, The University of Tokyo", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "12:00", "12:30", speaker="Gastone Ciuti, Scuola Superiore Sant'Anna", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "14:00", "14:30", speaker="Mingchuan Zhou, Zhejiang University", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "The Use of Robots and AI in Residential Care Facilities and their Impact on Care and Caring: A Frontline Perspective", "14:30", "15:00", speaker="Kazuko Obayashi and Shigeru Masuyama", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "From Simulation to Real-World Assistance: Robust Learning for Legged Robots in Aging Environments", "15:00", "15:30", speaker="Yue Gao, Shanghai Jiaotong University", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Dynamic Tactile Sensor (DTS) with Edge Intelligence for Safe Human-Robot Interaction", "16:00", "16:30", speaker="Yanmin Zhou, Tongji University", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
        curated_row("talk", "Invited talk", "16:30", "17:00", speaker="Naonori Kodate, University College Dublin", context="Speaker list; source has no timed program", url=FRI_WS_55_URL),
    ],
    "fri-ws-56": [
        curated_row("talk", "Development of a highly manoeuvrable Autonomous Underwater Vehicle for benthic survey", "09:00", "09:30", speaker="Stefan Williams", url=FRI_WS_56_URL),
        curated_row("spotlight", "Spotlight talks", "09:30", "09:50", speaker="Five emerging researchers", url=FRI_WS_56_URL),
        curated_row("poster", "Coffee break and poster session", "09:50", "10:50", url=FRI_WS_56_URL),
        curated_row("talk", "Autonomous Robotic Inspection on Ship Hulls and Storage Tanks", "10:50", "11:20", speaker="Cédric Pradalier", url=FRI_WS_56_URL),
        curated_row("talk", "Field-deployable maritime autonomy through energy-aware planning, docking, and coordination", "11:20", "11:50", speaker="Nina Mahmoudian", url=FRI_WS_56_URL),
        curated_row("talk", "Scaling field robotics in the age of LLMs", "13:10", "14:10", speaker="Nicholas Roy", context="Keynote", url=FRI_WS_56_URL),
        curated_row("panel", "Panel", "14:10", "14:40", url=FRI_WS_56_URL),
        curated_row("spotlight", "Spotlight talks", "14:40", "15:00", speaker="Five emerging researchers", url=FRI_WS_56_URL),
        curated_row("poster", "Coffee break and poster session", "15:00", "16:00", url=FRI_WS_56_URL),
        curated_row("talk", "Result announcement for the dataset competition", "16:00", "16:10", speaker="Organizers", url=FRI_WS_56_URL),
        curated_row("talk", "Lidar-centric Navigation for Field Drones", "16:10", "16:40", speaker="Fu Zhang", url=FRI_WS_56_URL),
        curated_row("talk", "Towards Agricultural Robots with Humans in and on the Loop", "16:40", "17:10", speaker="Katie Driggs-Campbell", url=FRI_WS_56_URL),
    ],
    "fri-ws-57": [
        curated_row("talk", "Invited Talk", "09:00", "09:30", speaker="Shuran Song", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "09:30", "10:00", speaker="Masha Itkina", url=FRI_WS_57_URL),
        curated_row("panel", "Building Reliable VLA Pipelines", "10:00", "10:30", url=FRI_WS_57_URL),
        curated_row("poster", "Poster Session 1", "11:00", "11:40", context="Interactive posters and demos", url=FRI_WS_57_URL),
        curated_row("paper", "Contributed Talks (Oral)", "11:40", "12:00", speaker="Selected papers", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "12:00", "12:30", speaker="Yuke Zhu", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "14:00", "14:30", speaker="Alberto Rodriguez", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "14:30", "15:00", speaker="Tetsuya Ogata", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "15:00", "15:30", speaker="Katherine Liu", url=FRI_WS_57_URL),
        curated_row("poster", "Poster Session 2", "16:00", "16:40", context="Interactive posters and demos", url=FRI_WS_57_URL),
        curated_row("talk", "Invited Talk", "16:40", "17:10", speaker="Karl Pertsch", url=FRI_WS_57_URL),
    ],
    "fri-ws-58": [
        curated_row("panel", "Debate: Question 1 - Education", "09:10", "10:00", url=FRI_WS_58_URL),
        curated_row("talk", "Discussion: Question 1 - Education", "10:00", "10:20", context="Interactive session", url=FRI_WS_58_URL),
        curated_row("spotlight", "Spotlight Talks", "10:20", "10:30", url=FRI_WS_58_URL),
        curated_row("poster", "Coffee Break & Poster Session I", "10:30", "11:00", url=FRI_WS_58_URL),
        curated_row("panel", "Debate: Question 2 - Research", "11:00", "11:50", url=FRI_WS_58_URL),
        curated_row("talk", "Discussion: Question 2 - Research", "11:50", "12:10", url=FRI_WS_58_URL),
        curated_row("panel", "Debate: Question 3 - Communication", "14:00", "14:50", url=FRI_WS_58_URL),
        curated_row("talk", "Discussion: Question 3 - Communication", "14:50", "15:20", url=FRI_WS_58_URL),
        curated_row("spotlight", "Spotlight Talks", "15:20", "15:30", url=FRI_WS_58_URL),
        curated_row("poster", "Coffee Break & Poster Session II", "15:30", "16:00", url=FRI_WS_58_URL),
        curated_row("panel", "Full Panel Discussion", "16:00", "17:00", url=FRI_WS_58_URL),
    ],
    "fri-ws-59": [
        curated_row("talk", "Origami-Inspired Heavy-Duty Deployable Systems: Compact Storage for Portability and High Payload for Utility.", "09:10", "09:30", speaker="Kyujin Cho", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "TBD", "09:30", "09:50", speaker="Yan Chen", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "TBD", "09:50", "10:10", speaker="Renee Zhao", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "Origami and Kirigami Mechanisms in Medical Robotics", "10:10", "10:30", speaker="Hongliang Ren", url="https://sites.google.com/view/origamirob"),
        curated_row("spotlight", "Short talks given by two students", "10:30", "10:40", url="https://sites.google.com/view/origamirob"),
        curated_row("poster", "Coffee break, posters, live demos", "10:40", "11:00", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "The focus of ADRR and AM", "11:00", "11:20", speaker="Sneha K Rhode", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "Kiri-Meta-Bot: transforming kirigami metamaterials into robots", "11:20", "11:40", speaker="Jie Yin", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "Origami-inspired continuum soft robots", "11:40", "12:00", speaker="Cagdas Onal", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "Cubism Kirigami and deployable mechanism", "12:00", "12:20", speaker="Bo Li", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "Assigning functionality through folding", "12:20", "12:40", speaker="Shuhei Miyashita", url="https://sites.google.com/view/origamirob"),
        curated_row("talk", "TBD", "12:40", "13:00", speaker="Damiano Pasini", url="https://sites.google.com/view/origamirob"),
    ],
    "fri-ws-60": [
        curated_row("talk", "Learning and Bio-Inspired Approaches to Contact-Rich Manipulation", "09:15", "09:40", speaker="Marco Santello", url=FRI_WS_60_URL),
        curated_row("talk", "Invited talk", "09:40", "10:05", speaker="Lael Odhner", url=FRI_WS_60_URL),
        curated_row("talk", "Invited talk", "10:05", "10:30", speaker="Josie Hughes", url=FRI_WS_60_URL),
        curated_row("poster", "Coffee Break + Poster Session", "10:30", "11:00", url=FRI_WS_60_URL),
        curated_row("talk", "Design and Control of Dexterous Robotic End-Effectors", "11:00", "11:25", speaker="Antonio Bicchi", url=FRI_WS_60_URL),
        curated_row("talk", "Invited talk", "11:25", "11:50", speaker="Quentin Sanders", url=FRI_WS_60_URL),
        curated_row("panel", "Panel Session #1", "11:50", "12:15", url=FRI_WS_60_URL),
        curated_row("lightning", "Lightning Talks", "12:15", "12:30", url=FRI_WS_60_URL),
        curated_row("poster", "Poster Session + Lunch Break", "12:30", "13:30", url=FRI_WS_60_URL),
        curated_row("talk", "Human Motor Control of Physical Interaction", "14:00", "14:25", speaker="Neville Hogan", url=FRI_WS_60_URL),
        curated_row("talk", "Invited talk", "14:25", "14:50", speaker="Atsushi Takagi", url=FRI_WS_60_URL),
        curated_row("talk", "Soft Robotic Solutions for Dexterous Manipulation", "14:50", "15:15", speaker="Kait Becker", url=FRI_WS_60_URL),
        curated_row("talk", "Invited talk", "15:15", "15:40", speaker="Huang Huishi", url=FRI_WS_60_URL),
        curated_row("poster", "Coffee Break + Poster Session", "15:40", "16:00", url=FRI_WS_60_URL),
        curated_row("talk", "Insights from Industry", "16:00", "16:25", speaker="Richard Walker", url=FRI_WS_60_URL),
        curated_row("panel", "Round Table with all panelists", "16:25", "17:20", url=FRI_WS_60_URL),
    ],
    "fri-ws-61": [
        curated_row("talk", "Indoor and outdoor inspection of industrial infrastructure by fully autonomous flying robots (UAVs)", "09:10", "09:30", speaker="Martin Saska", url=FRI_WS_61_URL),
        curated_row("talk", "Perception for aerial inspection", "09:30", "09:50", speaker="Margarita Chli", url=FRI_WS_61_URL),
        curated_row("talk", "Diving into the forest with robotic systems", "09:50", "10:10", speaker="Chao Xu", url=FRI_WS_61_URL),
        curated_row("talk", "Mapping the Condition of Critical Pipeline Infrastructure with NDT Robotic Crawlers", "10:10", "10:30", speaker="Jaime Valls Miro", url=FRI_WS_61_URL),
        curated_row("lightning", "Contributed papers lightning talks - morning session", "10:30", "10:45", url=FRI_WS_61_URL),
        curated_row("poster", "Coffee Break and Poster/Demo Session", "10:45", "11:00", url=FRI_WS_61_URL),
        curated_row("talk", "From Construction Sites to Underwater Structures: Multi-Sensor Approaches for Structural Inspections", "11:00", "11:20", speaker="Ayoung Kim", url=FRI_WS_61_URL),
        curated_row("talk", "Towards ship scanning in a day: robotics for above water, undersea and interior survey of marine assets", "11:20", "11:40", speaker="Stefan Williams", url=FRI_WS_61_URL),
        curated_row("talk", "Hilti x Trimble SLAM Challenge 2026 Award Ceremony", "11:40", "12:10", speaker="Emilia Szymanska", url=FRI_WS_61_URL),
        curated_row("talk", "TBD", "13:00", "13:20", speaker="Nexxis", url=FRI_WS_61_URL),
        curated_row("talk", "From Moonshots to Reality: Legged Robots for Autonomous Asset Inspection", "13:20", "13:40", speaker="Christian Gehring", url=FRI_WS_61_URL),
        curated_row("talk", "From research to product: Asset management using handheld scanners", "13:40", "14:00", speaker="David Wisth", url=FRI_WS_61_URL),
        curated_row("talk", "Aerial imaging and Inspection", "14:00", "14:20", speaker="Suchet Bargoti", url=FRI_WS_61_URL),
        curated_row("poster", "Coffee Break and Poster/Demo Session", "14:20", "14:40", url=FRI_WS_61_URL),
        curated_row("panel", "Panel discussion", "14:40", "15:20", url=FRI_WS_61_URL),
        curated_row("talk", "Computational Symmetry and Learning for Robotics", "15:20", "15:40", speaker="Maani Ghaffari", url=FRI_WS_61_URL),
        curated_row("lightning", "Contributed papers lightning talks - afternoon session", "15:40", "16:20", url=FRI_WS_61_URL),
    ],
    "fri-ws-62": [
        curated_row("talk", "Perceptual Challenges and Testing for the DLR Autonomous Exploration Experiment onboard the MMX Idefix Rover", "09:00", "09:30", speaker="Lukas Burkhard", url=FRI_WS_62_URL),
        curated_row("talk", "Invited Talk", "09:30", "10:00", speaker="Dr. Jean-Pierre de la Croix", url=FRI_WS_62_URL),
        *curated_papers("poster", "10:00", "11:00", FRI_WS_62_POSTERS, context="Coffee Break and Poster Session", url=FRI_WS_62_URL),
        curated_row("talk", "Invited Talk", "11:00", "11:30", speaker="Dr. Masahiro Ono", url=FRI_WS_62_URL),
        curated_row("talk", "The Lunar Leaper Mission", "11:30", "12:00", speaker="Prof. Marco Hutter", url=FRI_WS_62_URL),
        *curated_papers("spotlight", "12:00", "12:30", FRI_WS_62_POSTERS, context="Contributed Papers Spotlight Talks", url=FRI_WS_62_URL),
        curated_row("talk", "Semantic and Object-Driven Localization for Multi-Rover Exploration in Planetary Environments", "13:30", "14:00", speaker="Annika Thomas", url=FRI_WS_62_URL),
        curated_row("talk", "Active SLAM", "14:00", "14:30", speaker="Prof. Davide Scaramuzza", url=FRI_WS_62_URL),
        curated_row("talk", "Lunar Cargo Logistics", "14:30", "15:00", speaker="Prof. Tim Barfoot", url=FRI_WS_62_URL),
        *curated_papers("poster", "15:00", "16:00", FRI_WS_62_POSTERS, context="Coffee Break and Poster Session", url=FRI_WS_62_URL),
        curated_row("panel", "Panel Discussion", "16:00", "17:00", url=FRI_WS_62_URL),
    ],
    "fri-ws-63": [
        curated_row("talk", "Intelligent Robotics in Sustainable Forest Management", "09:10", "09:35", speaker="Janine Schweier", context="Terrestrial robotics session", url=FRI_WS_63_URL),
        curated_row("talk", "Scaling up wildlife ecology with drones and computer vision", "09:35", "10:00", speaker="Blaire Costelloe", context="Terrestrial robotics session", url=FRI_WS_63_URL),
        curated_row("talk", "Drones for Nature Conservation - Challenges and Results from the WildDrone Project", "10:00", "10:25", speaker="Ulrik Lundquist", context="Terrestrial robotics session", url=FRI_WS_63_URL),
        curated_row("talk", "Challenges and Opportunities in Degraded Rangelands", "10:55", "11:20", speaker="Christoffer Heckman", context="The Role of Robots in Land Restoration", url=FRI_WS_63_URL),
        curated_row("talk", "Autonomous Soil Characterization with Ground-Based Robotic Systems", "11:20", "11:45", speaker="Aaron Johnson", context="The Role of Robots in Land Restoration", url=FRI_WS_63_URL),
        curated_row("talk", "Unlocking Soil Secrets with AI for Sustainable Agriculture.", "11:45", "12:15", speaker="Katharina Keiblinger", context="The Role of Robots in Land Restoration", url=FRI_WS_63_URL),
        curated_row("talk", "Towards Low-Cost Underwater Navigation and Ubiquitous Ocean Observation", "13:30", "13:55", speaker="Alan Papalia", context="Current and Future of Robotics as a Climate Technology", url=FRI_WS_63_URL),
        curated_row("talk", "From Lab to Landscape: Ensuring Robotics is Applicable and Accessible to Achieve Ecological Impact", "13:55", "14:20", speaker="Jenna Lawson", context="Current and Future of Robotics as a Climate Technology", url=FRI_WS_63_URL),
        curated_row("lightning", "Poster flash presentations", "14:20", "14:50", url=FRI_WS_63_URL),
        curated_row("poster", "Interactive poster session that extends to coffee break", "14:50", "15:25", url=FRI_WS_63_URL),
        curated_row("talk", "Novel Biorobotic Paradigms to Protect Ecosystems in a Changing Environment", "15:55", "16:20", speaker="Donato Romano", context="Novel Designs for Climate Robotics", url=FRI_WS_63_URL),
        curated_row("talk", "Soft Robotics for Deep-Sea Suction Sampling", "16:20", "16:45", speaker="Jan Peters", context="Novel Designs for Climate Robotics", url=FRI_WS_63_URL),
        curated_row("panel", "Round Table", "16:45", "17:30", speaker="Moderated by Patrik Meier", url=FRI_WS_63_URL),
    ],
    "fri-ws-64": [
        curated_row("talk", "Real-Time Planning and Estimation for High-Performance Drone Racing", "09:20", "09:55", speaker="Davide Scaramuzza", url=FRI_WS_64_URL),
        curated_row("talk", "Next Generation Motion Planning via SIMD Acceleration", "09:55", "10:30", speaker="Lydia Kavraki", url=FRI_WS_64_URL),
        curated_row("talk", "Large-Scale Planning for Real-Time Performance", "10:55", "11:30", speaker="An Thai Le", url=FRI_WS_64_URL),
        *curated_papers("lightning", "11:30", "12:00", FRI_WS_64_LIGHTNING, context="Lightning Talks", url=FRI_WS_64_URL),
        *curated_papers("poster", "12:00", "12:45", FRI_WS_64_LIGHTNING + FRI_WS_64_EXTRA_POSTERS, context="Poster Session", url=FRI_WS_64_URL),
        curated_row("panel", "Industry Panel and Lightning Talks", "14:00", "15:35", speaker="Mainak Biswas, Ashish Rao Mangalore, Andreas Orthey, Brian Jackson", url=FRI_WS_64_URL),
        curated_row("panel", "Systems and Architecture Research Panel and Lightning Talks", "15:55", "17:10", speaker="R. Iris Bahar, Sabrina M. Neuman, Songchen MA", url=FRI_WS_64_URL),
    ],
    "fri-ws-65": [
        curated_row("talk", "Human Sense of Touch and Embodiment", "09:15", "09:30", speaker="Prof. Marcia O'Malley", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "09:30", "09:45", speaker="Prof. Yasemin Vardar", url=FRI_WS_65_URL),
        curated_row("spotlight", "Poster Teaser A", "09:45", "10:00", url=FRI_WS_65_URL),
        curated_row("panel", "Panel Discussion Session 1 - Q/A and CoffeeBreak", "10:00", "10:30", url=FRI_WS_65_URL),
        curated_row("talk", "Tactile Sensing Technologies for Embodied Robots", "10:30", "10:45", speaker="Prof. Oliver Brock", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "10:45", "11:00", speaker="Prof. Matei Ciocarlie", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "11:00", "11:15", speaker="Prof. Domenico Prattichizzo", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "11:15", "11:30", speaker="Prof. Matteo Bianchi", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "11:30", "11:45", speaker="Prof. Lorenzo Jamone", url=FRI_WS_65_URL),
        curated_row("talk", "Invited talk", "11:45", "12:00", speaker="Prof. Michael Yu Wang", url=FRI_WS_65_URL),
        curated_row("spotlight", "Poster Teaser B", "12:00", "12:15", url=FRI_WS_65_URL),
        curated_row("panel", "Panel Discussion Session 2 and Q/A", "12:15", "12:45", url=FRI_WS_65_URL),
        curated_row("poster", "Lunch and Demo Session", "12:45", "13:40", url=FRI_WS_65_URL),
    ],
    "fri-ws-66": [
        curated_row("talk", "Invited talk", "09:15", "09:40", speaker="Maurice Fallon, University of Oxford, UK", context="Session 1", url=FRI_WS_66_URL),
        curated_row("talk", "Exploring interactions with object-level maps", "09:40", "10:05", speaker="Jen Jen Chung, The University of Queensland, Australia", context="Session 1", url=FRI_WS_66_URL),
        curated_row("talk", "Open-World Autonomy: Representations, Mapping, Interaction", "10:15", "10:40", speaker="Abhinav Valada, University of Freiburg, Germany", context="Session 2", url=FRI_WS_66_URL),
        curated_row("talk", "From Map to Memories: Present and Future of Spatial AI for Robotics", "10:40", "11:05", speaker="Luca Carlone, MIT, USA", context="Session 2", url=FRI_WS_66_URL),
        curated_row("talk", "Open Scene Graphs for Open World Navigation", "11:05", "11:30", speaker="David Hsu, National University of Singapore, Singapore", context="Session 2", url=FRI_WS_66_URL),
        curated_row("poster", "Interactive Poster", "11:30", "12:00", url=FRI_WS_66_URL),
        curated_row("talk", "Tightly Integrating Semantic-Relational Priors into SLAM for Indoor Environments", "13:10", "13:35", speaker="Jose Luis Sanchez Lopez, University of Luxembourg, Luxembourg", context="Session 3", url=FRI_WS_66_URL),
        curated_row("talk", "Invited talk", "13:35", "14:00", speaker="Huan Yin (online), Hunan University, China", context="Session 3", url=FRI_WS_66_URL),
        curated_row("paper", "Paper Presentation", "14:20", "14:45", speaker="Accepted contributors", context="Session 4", url=FRI_WS_66_URL),
        curated_row("talk", "Geometric and Open-Vocabulary Priors for Visual SLAM", "14:45", "15:10", speaker="Javier Civera, University of Zaragoza, Spain", context="Session 4", url=FRI_WS_66_URL),
        curated_row("paper", "Paper Presentation", "15:10", "15:40", speaker="Accepted contributors", context="Session 4", url=FRI_WS_66_URL),
        curated_row("talk", "Invited talk", "15:40", "16:05", speaker="Ji Zhang (Haokun Zhu substitute), CMU, USA", context="Session 4", url=FRI_WS_66_URL),
        curated_row("paper", "Paper Presentation", "16:05", "16:25", speaker="Accepted contributors", context="Session 4", url=FRI_WS_66_URL),
        curated_row("poster", "Interactive Poster", "16:25", "16:50", url=FRI_WS_66_URL),
        curated_row("talk", "Invited talk", "16:50", "17:15", speaker="Xiaolong Wang (online), UCSD, USA", context="Session 4", url=FRI_WS_66_URL),
    ],
    "fri-ws-67": [
        curated_row("talk", "Invited talk 1", "09:10", "09:30", url=FRI_WS_67_URL),
        curated_row("talk", "Invited talk 2", "09:40", "10:00", url=FRI_WS_67_URL),
        curated_row("paper", "Oral presentations 1", "10:10", "10:40", context="15 min x2", url=FRI_WS_67_URL),
        curated_row("poster", "Poster session & coffee socials 1", "10:45", "11:45", url=FRI_WS_67_URL),
        curated_row("talk", "Invited talk 3", "13:00", "13:20", url=FRI_WS_67_URL),
        curated_row("talk", "Invited talk 4", "13:30", "13:50", url=FRI_WS_67_URL),
        curated_row("paper", "Oral presentations 2", "13:50", "14:20", context="15 min x2", url=FRI_WS_67_URL),
        curated_row("poster", "Poster session & coffee socials 2", "14:25", "15:25", url=FRI_WS_67_URL),
        curated_row("talk", "Invited talk 5", "15:30", "15:50", url=FRI_WS_67_URL),
        curated_row("talk", "Invited talk 6", "16:00", "16:20", url=FRI_WS_67_URL),
        curated_row("panel", "Panel discussion", "16:30", "17:00", url=FRI_WS_67_URL),
    ],
    "fri-ws-68": [
        curated_row("talk", "Reasoning VLA Models for Vehicle Autonomy", "09:15", "09:45", speaker="Milan Ganai", context="Theme A", url=FRI_WS_68_URL),
        curated_row("talk", "Digital Twins for Embodied AI: Advancing the Frontiers of Realism & Interaction", "09:45", "10:15", speaker="Manolis Savva", context="Theme A", url=FRI_WS_68_URL),
        curated_row("lightning", "Lightning Talks", "10:15", "10:55", context="Theme A", url=FRI_WS_68_URL),
        curated_row("poster", "Coffee Break and Morning Poster Session", "10:55", "11:15", url=FRI_WS_68_URL),
        curated_row("talk", "Spatio-Temporal Reasoning over Objects and Humans", "11:15", "11:45", speaker="Lukas Schmid", context="Theme A", url=FRI_WS_68_URL),
        curated_row("talk", "Semantic Queries of Robot Data", "11:45", "12:15", speaker="Ken Goldberg", context="Theme A", url=FRI_WS_68_URL),
        curated_row("panel", "Theme A Panel Discussion", "12:15", "12:45", url=FRI_WS_68_URL),
        curated_row("talk", "Building an Adaptable Generalist Robot: A Human-Centered Perspective", "13:55", "14:25", speaker="Mengdi Xu", context="Theme B", url=FRI_WS_68_URL),
        curated_row("talk", "Semantics for Robot Task Execution Monitoring", "14:25", "14:55", speaker="Dongheui Lee", context="Theme B", url=FRI_WS_68_URL),
        curated_row("lightning", "Lightning Talks", "14:55", "15:35", context="Theme B", url=FRI_WS_68_URL),
        curated_row("poster", "Coffee Break and Afternoon Poster Session", "15:35", "15:55", url=FRI_WS_68_URL),
        curated_row("talk", "Invited Talk", "15:55", "16:25", speaker="Masha Itkina", context="Theme B", url=FRI_WS_68_URL),
        curated_row("talk", "LLM-Enabled Robots: Jailbreaking Attacks and Defenses", "16:25", "16:55", speaker="George Pappas", context="Theme B", url=FRI_WS_68_URL),
        curated_row("panel", "Theme B Panel Discussion", "16:55", "17:25", url=FRI_WS_68_URL),
    ],
    "fri-ws-69": [
        curated_row("talk", "Soft Robots in Clinical Practice: Definitions, Real Needs, and Future Opportunities", "08:40", "09:00", speaker="Alberto Arezzo", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Form Follows Function: Matching Compliance to Clinical Need in Medical Robotics", "09:00", "09:20", speaker="Sam Schorr, Intuitive Surgical", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "TBD", "09:20", "09:40", speaker="Hedyeh Rafii-Tari, Monarch J&J", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Medical Applications of Soft Robotics: Mirage or Keystone?", "10:30", "10:50", speaker="Arianna Menciassi", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Opportunities in soft actuation/sensing of soft robots", "10:50", "11:10", speaker="Kaspar Althoefer", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Multimaterial Robotics: Where Soft Meets Rigid for Better Performance", "11:10", "11:30", speaker="Bram Vanderborght", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Startup Pitches", "11:30", "12:00", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "How soft is soft? Different perspectives on what Soft means", "13:00", "13:20", speaker="Thrishantha Nanayakkara", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "When and Why Soft is Better: Analysis, Design and Applications", "13:20", "13:40", speaker="Josie Hughes", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Soft yet precise robots by means of good old model based control", "13:40", "14:00", speaker="Cosimo Della Santina", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("talk", "Soft Magnetic Surgical Robots for Endoluminal Applications", "14:00", "14:30", speaker="Pietro Valdastri", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("panel", "Interactive Debate Session", "14:40", "16:00", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
        curated_row("panel", "Concluding Panel Discussion", "16:00", "16:45", url="https://sites.google.com/view/rethinkingsoftness/schedule"),
    ],
    "fri-ws-70": [
        curated_row("talk", "The SWAG Project", "09:15", "09:30", speaker="Panagiotis Polygerinos", url=FRI_WS_70_URL),
        curated_row("talk", "The VIVO Hub", "09:30", "09:45", speaker="Jonathan Rossiter", url=FRI_WS_70_URL),
        curated_row("lightning", "Flash Poster Pitches", "09:45", "10:30", url=FRI_WS_70_URL),
        curated_row("poster", "Coffee Break, Demos, and Posters", "10:30", "11:00", url=FRI_WS_70_URL),
        curated_row("talk", "Advancing Human Movement and Performance Through Wearable Technologies", "11:00", "11:15", speaker="Maria Alejandra Diaz", url=FRI_WS_70_URL),
        curated_row("talk", "Wearable for whom? Decentring 'The Human' in human-centred design", "11:15", "11:30", speaker="Chris Kent", url=FRI_WS_70_URL),
        curated_row("talk", "Reshaping body image through co-adaptation of humans and wearable robots", "11:30", "11:45", speaker="Helen Huang", url=FRI_WS_70_URL),
        curated_row("panel", "What's next for exo design?", "11:45", "12:30", speaker="Saivimal Sridar, Ilaria Pacifico, Evangelos Papadopoulos, Charlotte Pouwels, Andrea Bertolini, Gerdienke Prange", url=FRI_WS_70_URL),
        curated_row("talk", "Deformables in MuJoCo", "13:30", "13:45", speaker="Google DeepMind", url=FRI_WS_70_URL),
        curated_row("talk", "MyoAssist: Bridging Neuromechanics, Robotics, and Machine Learning Through Open-Source Simulation", "13:45", "14:00", speaker="Seungmoon Song", url=FRI_WS_70_URL),
        curated_row("talk", "Towards Volitional Control of Assistive Robots After Neuromuscular Injury", "14:00", "14:15", speaker="Massimo Sartori", url=FRI_WS_70_URL),
        curated_row("talk", "Control strategies for soft wearable robotics using machine learning and artificial vision", "14:15", "14:30", speaker="Lorenzo Masia", url=FRI_WS_70_URL),
        curated_row("talk", "Magnetic Interfaces for Tailoring Human Movement and Perception", "14:30", "14:45", speaker="Federico Masiero", url=FRI_WS_70_URL),
        curated_row("panel", "Speed Mentoring", "14:45", "15:30", context="Interactive session", url=FRI_WS_70_URL),
        curated_row("poster", "Coffee Break, Demos, and Posters", "15:30", "16:00", url=FRI_WS_70_URL),
        curated_row("talk", "Soft and Textile-Integrated Sensors for Human-Robot Interaction in Wearable Robotics", "16:00", "16:15", speaker="Martina Masseli", url=FRI_WS_70_URL),
        curated_row("talk", "Lightweight Exosuit with Underactuated Mechanism for Walking Assistance", "16:15", "16:30", speaker="Kyujin Cho", url=FRI_WS_70_URL),
        curated_row("talk", "Soft Robotic Exosuit for Knee Assistance using Human-in-the-loop Reinforcement Learning", "16:30", "16:45", speaker="Wenlong Zhang", url=FRI_WS_70_URL),
        curated_row("poster", "Posters and Demos", "16:45", "17:15", url=FRI_WS_70_URL),
    ],
    "fri-ws-71": [
        curated_row("talk", "Tentative title: TBD", "09:15", "09:45", speaker="Marija Popovich", url=FRI_WS_71_URL),
        curated_row("talk", "Scalable Multi-Agent Navigation: Hierarchical Planning Under Real-World Uncertainty", "09:45", "10:15", speaker="Nicholas Roy", url=FRI_WS_71_URL),
        curated_row("poster", "Poster Session", "10:45", "11:45", url=FRI_WS_71_URL),
        curated_row("lightning", "Highlight Posters Presentation", "11:45", "12:30", url=FRI_WS_71_URL),
        curated_row("talk", "Tentative title: TBD", "13:30", "14:00", speaker="Abhinav Valada", url=FRI_WS_71_URL),
        curated_row("talk", "Understanding the 3D World for a General Agent", "14:00", "14:30", speaker="Siyuan Huang", url=FRI_WS_71_URL),
        curated_row("poster", "Poster Session", "15:00", "16:00", url=FRI_WS_71_URL),
        curated_row("lightning", "Highlight Posters Presentation", "16:00", "16:30", url=FRI_WS_71_URL),
        curated_row("talk", "Competition Winner Presentation", "16:30", "16:45", url=FRI_WS_71_URL),
    ],
    "fri-ws-72": [
        curated_row("talk", "Challenges on Robotic Exploration of Lava Caves on the Moon and Mars", "08:45", "09:15", speaker="Carlos Pérez del Pulgar", url=FRI_WS_72_URL),
        *curated_papers("lightning", "09:15", "10:00", FRI_WS_72_SPOTLIGHT_1, context="Spotlight Talks I", url=FRI_WS_72_URL),
        *curated_papers("poster", "10:00", "11:00", FRI_WS_72_SPOTLIGHT_1, context="Poster Session I", url=FRI_WS_72_URL),
        curated_row("talk", "Towards Vision-based Manipulation and Grasping for ISAM: Challenges and Opportunities", "11:00", "11:30", speaker="Kuldeep Barad", url=FRI_WS_72_URL),
        curated_row("talk", "Weak-Force Actuation for Space Mobility: Attitude, Drag, and Lasers", "11:30", "12:00", speaker="Giusy Falcone", url=FRI_WS_72_URL),
        curated_row("talk", "Robot Learning and Shared Autonomy for Lunar Assembly Tasks", "12:00", "12:30", speaker="Dongheui Lee", url=FRI_WS_72_URL),
        curated_row("talk", "From Explorers to Builders: Design and Demonstration of Robotic Mobility and Tools for Lunar ISRU and Construction", "13:45", "14:15", speaker="Genya Ishigami", url=FRI_WS_72_URL),
        curated_row("talk", "TABxStartups: Francisco Cuellar, CEO of TUMI Robotics", "14:15", "14:25", url=FRI_WS_72_URL),
        *curated_papers("lightning", "14:25", "15:00", FRI_WS_72_SPOTLIGHT_2, context="Spotlight Talks II", url=FRI_WS_72_URL),
        *curated_papers("poster", "15:00", "16:00", FRI_WS_72_SPOTLIGHT_2, context="Poster Session II", url=FRI_WS_72_URL),
        curated_row("talk", "Increasing Transparency in Teleoperation Under Large Time-Delays for Orbital and Planetary Space Applications", "16:00", "16:30", speaker="Christian Ott", url=FRI_WS_72_URL),
        curated_row("panel", "Panel Discussion / Round Table", "16:30", "17:15", url=FRI_WS_72_URL),
    ],
    "fri-ws-73": [
        curated_row("talk", "Invited Talks 1-4", "14:10", "15:30", context="20 min each including Q&A", url=FRI_WS_73_URL),
        curated_row("poster", "Posters and Coffee", "15:30", "16:00", url=FRI_WS_73_URL),
        curated_row("poster", "Tool-as-Interface: Learning Robot Policies from Observing Human Tool Use", "15:30", "16:00", url=FRI_WS_73_URL),
        curated_row("poster", "DexWild: Dexterous Human Interactions for In-the-Wild Robot Policies", "15:30", "16:00", url=FRI_WS_73_URL),
        curated_row("talk", "Invited Talks 5-7", "16:00", "17:00", url=FRI_WS_73_URL),
        curated_row("panel", "Principles and Pathways to Human-Level Robustness", "17:00", "17:30", url=FRI_WS_73_URL),
    ],
    "fri-ws-74": [
        curated_row("talk", "Invited Talks and Spotlight Talks", "09:10", "10:40", context="2 talks plus spotlight talks", url=FRI_WS_74_URL),
        curated_row("talk", "Invited Talk and Spotlight Talks", "11:00", "12:00", context="1 invited talk plus spotlight talks", url=FRI_WS_74_URL),
        curated_row("panel", "Junior Panel", "13:00", "14:00", url=FRI_WS_74_URL),
        curated_row("talk", "Invited Talks", "14:00", "15:00", context="2 talks", url=FRI_WS_74_URL),
        curated_row("poster", "Coffee Break and Poster Session", "15:00", "16:00", url=FRI_WS_74_URL),
        curated_row("talk", "Invited Talks", "16:00", "17:20", context="3 talks", url=FRI_WS_74_URL),
    ],
}


def curated_presentations_for_workshop(workshop: dict) -> list[dict] | None:
    rows = CURATED_WORKSHOP_PRESENTATIONS.get(workshop.get("id", ""))
    if rows is None:
        return None
    return [row.copy() for row in rows]


def apply_poster_time_context(presentations: list[dict]) -> list[dict]:
    poster_slots: list[dict] = []
    for item in presentations:
        title = item.get("title", "")
        hay = title.lower()
        if (
            item.get("start")
            and item.get("end")
            and "poster session" in hay
            and "lightning" not in hay
        ):
            poster_slots.append(
                {
                    "start": item["start"],
                    "end": item["end"],
                    "time": item.get("time") or f"{item['start']}-{item['end']}",
                    "label": strip_pdf_filename(TIME_RE.sub("", title)) or "Poster session",
                }
            )
    if not poster_slots:
        return presentations

    expanded: list[dict] = []
    for item in presentations:
        if item.get("kind") == "poster" and not item.get("start") and not item.get("time"):
            for slot in poster_slots:
                copy = item.copy()
                copy["start"] = slot["start"]
                copy["end"] = slot["end"]
                copy["time"] = slot["time"]
                copy["context"] = " · ".join([x for x in [slot["label"], item.get("context", "")] if x])
                expanded.append(copy)
        else:
            expanded.append(item)
    return expanded


def enrich_missing_speakers_by_title(presentations: list[dict]) -> list[dict]:
    """Fill author/speaker gaps from duplicate paper titles on another slot/page."""
    speaker_by_title: dict[str, str] = {}
    for item in presentations:
        title_key = compact_for_compare(item.get("title", ""))
        speaker = normalize_text(item.get("speaker", ""))
        if not title_key or not speaker or re.fullmatch(r"(lightning|poster|oral|talk|paper)", speaker, flags=re.I):
            continue
        if title_key not in speaker_by_title or len(speaker) > len(speaker_by_title[title_key]):
            speaker_by_title[title_key] = speaker

    out: list[dict] = []
    for item in presentations:
        copy = item.copy()
        title_key = compact_for_compare(copy.get("title", ""))
        speaker = normalize_text(copy.get("speaker", ""))
        if title_key and (not speaker or re.fullmatch(r"(lightning|poster|oral|talk|paper)", speaker, flags=re.I)):
            fill = speaker_by_title.get(title_key, "")
            if fill:
                copy["speaker"] = fill
        out.append(copy)
    return out


def crawl_workshop(workshop: dict) -> dict:
    url = workshop.get("url") or ""
    if not url:
        workshop["crawlStatus"] = "missing_link"
        return workshop

    pages_to_try = [url]
    visited: set[str] = set()
    all_lines: list[str] = []
    pages: list[dict] = []
    presentations: list[dict] = []
    failures: list[str] = []

    while pages_to_try and len(visited) < 9:
        page_url = pages_to_try.pop(0)
        page_url = urldefrag(page_url)[0]
        if page_url in visited:
            continue
        visited.add(page_url)
        status, final_url, text = fetch(page_url)
        if not text or text.startswith("FETCH_ERROR") or status is None or status >= 500:
            failures.append(f"{page_url} ({status})")
            continue
        if "Just a moment" in text and "Cloudflare" in text:
            failures.append(f"{page_url} (cloudflare)")
            continue
        soup = BeautifulSoup(text, "html.parser")
        special_presentations = extract_wosra_presentations(soup, final_url)
        table_presentations = extract_presentations_from_tables(soup, final_url)
        heading_presentations = extract_presentations_from_headings(soup, final_url)
        presentations.extend(special_presentations)
        presentations.extend(table_presentations)
        presentations.extend(heading_presentations)
        lines = text_lines_from_soup(soup)
        page_text = normalize_text(" ".join(lines))
        all_lines.extend(lines[:500])
        skip_generic = is_wosra_j_wosmars_page(final_url)
        if not skip_generic and not table_presentations and not heading_presentations:
            presentations.extend(extract_presentations(lines, final_url))
        pages.append(
            {
                "url": final_url,
                "status": status,
                "title": normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else "",
                "snippet": page_text[:1200],
            }
        )
        for nxt in discover_subpages(final_url, soup):
            if nxt not in visited and nxt not in pages_to_try:
                pages_to_try.append(nxt)
        time.sleep(0.08)

    deduped_presentations: list[dict] = []
    seen_presentations = set()
    for item in presentations:
        key = (
            item.get("time", ""),
            re.sub(r"\W+", "", item.get("title", "").lower())[:180],
            re.sub(r"\W+", "", item.get("speaker", "").lower())[:80],
        )
        if key in seen_presentations:
            continue
        seen_presentations.add(key)
        deduped_presentations.append(item)
    presentations = enrich_missing_speakers_by_title(normalize_time_sequence(apply_poster_time_context(deduped_presentations)))
    normalized_presentations: list[dict] = []
    seen_presentations = set()
    for item in presentations:
        key = (
            item.get("time", ""),
            re.sub(r"\W+", "", item.get("title", "").lower())[:180],
            re.sub(r"\W+", "", item.get("speaker", "").lower())[:80],
        )
        if key in seen_presentations:
            continue
        seen_presentations.add(key)
        normalized_presentations.append(item)
    presentations = normalized_presentations
    curated = curated_presentations_for_workshop(workshop)
    curated_applied = curated is not None
    if curated_applied:
        presentations = curated

    joined = normalize_text(" ".join(all_lines))
    has_manual_presentations = curated_applied and bool(presentations)
    workshop["pages"] = pages
    workshop["presentations"] = presentations
    workshop["crawlStatus"] = "ok" if pages or has_manual_presentations else "failed"
    workshop["crawlFailures"] = [] if has_manual_presentations else failures[:10]
    workshop["pageText"] = joined[:24000]
    pres_text = " ".join([p["title"] + " " + p.get("context", "") + " " + p.get("speaker", "") for p in presentations])
    workshop["searchText"] = normalize_text(
        " ".join(
            [
                workshop.get("title", ""),
                workshop.get("category", ""),
                workshop.get("room", ""),
                workshop.get("block", ""),
                workshop.get("url", ""),
                joined,
                pres_text,
            ]
        )
    ).lower()
    return workshop


def try_rasevents() -> dict:
    status, final_url, text = fetch(RASEVENTS_URL)
    blocked = "Just a moment" in text or "Cloudflare" in text or not text
    return {
        "url": RASEVENTS_URL,
        "status": status,
        "finalUrl": final_url,
        "blocked": blocked,
        "note": "Cloudflare challenge encountered; workshop metadata was taken from the official ICRA workshops page and linked workshop sites instead."
        if blocked
        else "Fetched, but this builder currently uses the official ICRA workshops page as the workshop index.",
    }


def make_embedded_presentations(workshops: list[dict]) -> list[dict]:
    searchable_kinds = {"talk", "poster", "paper", "lightning", "spotlight", "panel"}
    rows = []
    for ws in workshops:
        for i, p in enumerate(ws.get("presentations", []), start=1):
            kind = p.get("kind", "mention")
            if kind not in searchable_kinds:
                continue
            title = p.get("title", "")
            title_norm = normalize_text(title).lower()
            workshop_title_norm = normalize_text(ws.get("title", "")).lower()
            title_key = re.sub(r"\W+", "", title_norm)
            workshop_title_key = re.sub(r"\W+", "", workshop_title_norm)
            has_internal_time = bool(p.get("start") and p.get("end"))
            if is_schedule_group_heading(title) and not has_internal_time:
                continue
            if is_public_presentation_noise(title):
                continue
            if is_housekeeping_presentation(title):
                continue
            if title_norm and workshop_title_norm and (title_norm == workshop_title_norm or workshop_title_norm in title_norm):
                continue
            if title_key and workshop_title_key and (title_key == workshop_title_key or workshop_title_key in title_key):
                continue
            if not has_internal_time and (duration_minutes(ws.get("start", ""), ws.get("end", "")) or 0) >= 120:
                continue
            start = p.get("start") or ws["start"]
            end = p.get("end") or ws["end"]
            public_duration = duration_minutes(start, end)
            if public_duration is not None and public_duration >= 120 and kind in {"talk", "paper", "lightning"}:
                continue
            paper_id = p.get("paperId", "")
            if PDF_FILE_RE.search(paper_id):
                paper_id = ""
            clean_context = clean_item_context(p)
            rows.append(
                {
                    "type": "workshop_presentation",
                    "source": "Workshop linked page crawl",
                    "id": f"{ws['id']}-p{i:02d}",
                    "workshopId": ws["id"],
                    "workshopTitle": ws["title"],
                    "category": ws["category"],
                    "kind": kind,
                    "day": ws["day"],
                    "start": start,
                    "end": end,
                    "time": f"{start}-{end}",
                    "internalTime": p.get("time", ""),
                    "room": ws["room"],
                    "title": p.get("title", ""),
                    "speaker": p.get("speaker", ""),
                    "paperId": paper_id,
                    "abstract": p.get("abstract", ""),
                    "context": clean_context,
                    "url": p.get("url") or ws.get("url", ""),
                    "searchText": normalize_text(
                        " ".join(
                            [
                                p.get("title", ""),
                                p.get("abstract", ""),
                                p.get("speaker", ""),
                                clean_context,
                                p.get("paperId", ""),
                                ws["title"],
                            ]
                        )
                    ).lower(),
                    "displayText": normalize_text(
                        " ".join([p.get("title", ""), p.get("speaker", ""), clean_context, ws["title"]])
                    ).lower(),
                }
            )
    return rows


def is_housekeeping_presentation(title: str) -> bool:
    clean = normalize_text(title).strip(" .:-–—").lower()
    if not clean:
        return True
    has_poster_or_demo = bool(re.search(r"\b(poster|demo|showcase)\b", clean))
    has_panel = bool(re.search(r"\b(panel|round\s*table|roundtable)\b", clean))
    if re.fullmatch(
        r"(welcome|welcome remarks|welcome and (intro|introduction)|welcome & (intro|introduction)|"
        r"welcome \+ intro|introduction and welcome|opening remarks?|opening|workshop introduction|"
        r"introductory remarks?|closing|closing remarks?|concluding remarks?|conclusions|farewell|"
        r"wrap[-\s]*up.*|workshop ends?|end of workshop|concluding remarks and end of workshop)",
        clean,
    ):
        return True
    if re.search(r"\b(welcome|opening remarks?|closing remarks?|concluding remarks?|farewell|workshop ends?)\b", clean):
        return True
    if re.search(r"\b(best .* award|awards?|award announcement).*\b(closing|concluding remarks?|wrap[-\s]*up)\b", clean):
        return True
    if re.search(r"\b(closing|concluding remarks?|wrap[-\s]*up).*\b(best .* award|awards?)\b", clean):
        return True
    if clean == "introduction to aspire program":
        return True
    if not has_poster_or_demo and not has_panel:
        if re.fullmatch(r"(break|coffee\s*break|lunch|lunch\s*break|q\s*&\s*a|q/a|qa)", clean):
            return True
        if re.fullmatch(r"(discussion and networking|networking social.*)", clean):
            return True
        if re.fullmatch(r"sponsors?.*(remarks?|introductions?|pitch presentation)", clean):
            return True
        if re.search(r"\b(award ceremony|sponsor remarks?)\b", clean):
            return True
    return False


def normalize_context_label(text: str) -> str:
    text = strip_leading_time(strip_pdf_filename(text or ""))
    text = re.sub(r"[-–—_]{3,}", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    return normalize_text(text).strip(" :-–—.")


def is_generic_context_label(text: str) -> bool:
    clean = normalize_context_label(text)
    if not clean:
        return True
    low = clean.lower()
    if re.fullmatch(
        r"(lightning|poster|poster only|oral|talk|paper|accepted papers?|"
        r"accepted poster/demo contributions|program committee|tentative agenda|"
        r"talk\s+\d+|ieee standards status)",
        low,
    ):
        return True
    if re.fullmatch(r"(poster|spotlight|lightning|contributed)\s+talks?(?:\s+[ivx]+)?(?:\s*\([^)]*\))?", low):
        return True
    if re.fullmatch(r"(poster|demo|interactive)?\s*posters?(?:\s+and\s+demos?)?(?:\s+interactive)?\s+session.*", low):
        return True
    if "poster lightning talks" in low:
        return True
    if "lightning talks" in low and "panel" in low:
        return True
    if "poster session" in low and (
        "coffee" in low
        or "break" in low
        or "networking" in low
        or "accepted poster" in low
        or len(low) <= 90
    ):
        return True
    if "coffee" in low or "lunch" in low or re.search(r"\bbreak\b", low):
        if len(low) <= 130 or "poster" in low or "networking" in low:
            return True
    if low.startswith(("invited talk", "selected papers", "briefly highlighting contributions")):
        return True
    return False


def clean_item_context(item: dict) -> str:
    context = normalize_context_label(item.get("context", ""))
    if not context:
        return ""
    title = normalize_text(item.get("title", ""))
    speaker = normalize_text(item.get("speaker", ""))
    paper_id = normalize_text(item.get("paperId", ""))
    parts = [normalize_text(part) for part in re.split(r"\s*·\s*", context) if normalize_text(part)]
    kept = []
    skip_keys = {compact_for_compare(x) for x in [title, speaker, paper_id] if compact_for_compare(x)}
    for part in parts:
        clean_part = normalize_context_label(part)
        key = compact_for_compare(clean_part)
        if not key or key in skip_keys or is_generic_context_label(clean_part):
            continue
        kept.append(clean_part)
    context = " · ".join(kept)
    if not context:
        return ""
    context_key = compact_for_compare(context)
    if not context_key:
        return ""
    if is_generic_context_label(context):
        return ""
    if is_public_presentation_noise(context):
        return ""
    if re.search(r"tentative schedule|please see|workshop will be held|held in person|schedule below", context, re.I):
        return ""
    title_key = compact_for_compare(title)
    speaker_key = compact_for_compare(speaker)
    seen_keys = {x for x in [title_key, speaker_key] if x}
    if context_key in seen_keys or compact_for_compare(title + " " + speaker) == context_key:
        return ""
    if title_key and (context_key.startswith(title_key) or title_key in context_key) and len(title_key) >= 18:
        if len(context_key) <= len(title_key) + 90 or re.search(r"\b(abstract|invited talk|talk|presentation)\b", context, re.I):
            return ""
    if speaker_key and speaker_key in context_key and len(speaker_key) >= 8:
        if len(context_key) <= len(speaker_key) + 120 or re.search(r"\b(invited talk|speaker|abstract)\b", context, re.I):
            return ""
    if re.search(r"tentative schedule|please see|workshop will be held|held in person|schedule below", context, re.I):
        return ""
    return context


def clean_slot_label(text: str) -> str:
    text = normalize_context_label(text)
    parts = [normalize_text(part) for part in re.split(r"\s*·\s*", text) if normalize_text(part)]
    topic_parts = [part for part in parts if re.search(r"poster|lightning|session|talk|presentation|panel|discussion", part, re.I)]
    if topic_parts:
        text = topic_parts[0]
    text = re.sub(r"\s*·\s*Accepted poster/demo contributions\b", "", text, flags=re.I)
    text = normalize_text(text).strip(" :-–—.")
    if not text or is_public_presentation_noise(text):
        return ""
    if len(text) > 140:
        return ""
    if re.search(r"tentative schedule|please see|held in person", text, re.I):
        return ""
    return text


def is_slot_label_item(item: dict, all_items: list[dict]) -> bool:
    if len(all_items) <= 1:
        return False
    if item.get("paperId") or item.get("abstract"):
        return False
    title = normalize_text(item.get("title", ""))
    lower = title.lower()
    speaker = normalize_text(item.get("speaker", ""))
    context = normalize_text(item.get("context", ""))
    if not title:
        return False
    if "lightning talks" in lower or "poster session" in lower or "spotlight talks" in lower:
        return True
    if is_schedule_group_heading(title):
        return True
    if re.fullmatch(r"\d+\s+(papers?|posters?|topics?)", speaker, flags=re.I):
        return True
    if re.fullmatch(r"\d+\s+(papers?|posters?|topics?)", context, flags=re.I):
        return True
    return False


def slot_title_for_group(items: list[dict], label_items: list[dict] | None = None) -> str:
    label_items = label_items or []
    label_titles = [normalize_text(item.get("title", "")) for item in label_items if normalize_text(item.get("title", ""))]
    if label_titles:
        return min(label_titles, key=len)
    if len(items) == 1:
        return items[0].get("title", "Workshop timetable item")
    for item in items:
        title = normalize_text(item.get("title", ""))
        lower = title.lower()
        if (lower.startswith("session") or re.search(r"\bsession\s+\d|\bsession:", lower)) and len(title) <= 180:
            return title
    titles = [normalize_text(item.get("title", "")) for item in items if normalize_text(item.get("title", ""))]
    if titles:
        shortest = min(titles, key=len)
        short_key = re.sub(r"\W+", "", shortest.lower())
        if short_key and all(short_key in re.sub(r"\W+", "", title.lower()) for title in titles):
            return shortest
    kinds = {item.get("kind", "") for item in items}
    if len(items) > 1:
        if kinds == {"spotlight"}:
            return "Spotlight talks"
        if kinds == {"lightning"}:
            return "Lightning talks"
        if kinds == {"poster"}:
            return "Poster session"
        if kinds == {"paper"}:
            return "Contributed presentations"
    labels = [clean_slot_label(item.get("context", "")) for item in items]
    labels = [label for label in labels if label and label.lower() not in {"accepted poster/demo contributions"}]
    if labels:
        return Counter(labels).most_common(1)[0][0]
    if len(items) > 1:
        return "Workshop timetable item"
    return items[0].get("title", "Workshop timetable item")


def make_embedded_workshop_slots(workshops: list[dict], presentation_rows: list[dict]) -> list[dict]:
    ws_by_id = {ws["id"]: ws for ws in workshops}
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in presentation_rows:
        key = (row.get("workshopId", ""), row.get("start", ""), row.get("end", ""))
        grouped.setdefault(key, []).append(row)

    slots: list[dict] = []
    for idx, ((workshop_id, start, end), items) in enumerate(grouped.items(), start=1):
        ws = ws_by_id.get(workshop_id, {})
        items = sorted(items, key=lambda x: (x.get("kind", ""), x.get("title", "")))
        label_items = [item for item in items if is_slot_label_item(item, items)]
        display_items = [item for item in items if item not in label_items] or items
        title = slot_title_for_group(display_items, label_items)
        nested = [
            {
                "kind": item.get("kind", ""),
                "title": item.get("title", ""),
                "speaker": item.get("speaker", ""),
                "context": item.get("context", ""),
                "abstract": item.get("abstract", ""),
                "url": item.get("url", ""),
                "paperId": item.get("paperId", ""),
            }
            for item in display_items
        ]
        kind_counts = Counter(item.get("kind", "") for item in display_items if item.get("kind"))
        kind_label = ", ".join(f"{kind} x{count}" if count > 1 else kind for kind, count in sorted(kind_counts.items()))
        search_text = normalize_text(
            " ".join(
                [
                    title,
                    ws.get("title", ""),
                    ws.get("category", ""),
                    ws.get("room", ""),
                    kind_label,
                    " ".join(
                        " ".join(
                            [
                                item.get("title", ""),
                                item.get("speaker", ""),
                                item.get("context", ""),
                                item.get("abstract", ""),
                                item.get("paperId", ""),
                            ]
                        )
                        for item in display_items + label_items
                    ),
                ]
            )
        ).lower()
        slots.append(
            {
                "type": "workshop_slot",
                "source": "Workshop linked page crawl",
                "id": f"{workshop_id}-slot-{idx:03d}",
                "workshopId": workshop_id,
                "workshopTitle": ws.get("title", ""),
                "category": ws.get("category", ""),
                "kind": "workshop_slot",
                "slotKinds": kind_label,
                "slotItemCount": len(nested),
                "day": ws.get("day", ""),
                "start": start,
                "end": end,
                "time": f"{start}-{end}",
                "internalTime": f"{start}-{end}",
                "room": ws.get("room", ""),
                "title": title,
                "items": nested,
                "url": (display_items[0] if display_items else items[0]).get("url") or ws.get("url", ""),
                "searchText": search_text,
                "displayText": normalize_text(" ".join([title, kind_label, ws.get("title", ""), ws.get("room", "")])).lower(),
            }
        )
    slots.sort(key=lambda x: ((x.get("day") or ""), minutes_of_day(x.get("start", "")) or 9999, x.get("workshopTitle", ""), x.get("title", "")))
    return slots


def make_embedded_workshops(workshops: list[dict]) -> list[dict]:
    rows = []
    for ws in workshops:
        rows.append(
            {
                "type": ws.get("type", "workshop"),
                "id": ws.get("id", ""),
                "title": ws.get("title", ""),
                "category": ws.get("category", ""),
                "day": ws.get("day", ""),
                "start": ws.get("start", ""),
                "end": ws.get("end", ""),
                "time": ws.get("time", ""),
                "room": ws.get("room", ""),
                "block": ws.get("block", ""),
                "url": ws.get("url", ""),
                "crawlStatus": ws.get("crawlStatus", ""),
                "crawlFailures": ws.get("crawlFailures", []),
                "presentationCount": len(ws.get("presentations", [])),
            }
        )
    return rows


def conference_session(
    *,
    id: str,
    kind: str,
    title: str,
    day: str,
    start: str,
    end: str,
    room: str,
    url: str,
    items: list[dict] | None = None,
    context: str = "",
) -> dict:
    nested = []
    for item in items or []:
        nested.append(
            {
                "kind": item.get("kind", kind),
                "title": item.get("title", ""),
                "speaker": item.get("speaker", ""),
                "context": item.get("context", ""),
                "abstract": item.get("abstract", ""),
                "url": item.get("url") or url,
                "paperId": item.get("paperId", ""),
            }
        )
    search_text = normalize_text(
        " ".join(
            [
                title,
                kind,
                context,
                day,
                room,
                " ".join(
                    " ".join(
                        [
                            item.get("title", ""),
                            item.get("speaker", ""),
                            item.get("context", ""),
                            item.get("abstract", ""),
                        ]
                    )
                    for item in nested
                ),
            ]
        )
    ).lower()
    return {
        "type": "conference_session",
        "source": "Official ICRA 2026 program pages",
        "id": id,
        "kind": kind,
        "day": day,
        "start": start,
        "end": end,
        "time": f"{start}-{end}",
        "room": room,
        "title": title,
        "context": context,
        "slotItemCount": len(nested) or 1,
        "items": nested,
        "url": url,
        "searchText": search_text,
        "displayText": normalize_text(" ".join([title, kind, context, room])).lower(),
    }


def talk(title: str, speaker: str = "", context: str = "", kind: str = "talk") -> dict:
    return {"kind": kind, "title": title, "speaker": speaker, "context": context}


def make_conference_sessions() -> list[dict]:
    """Curated non-paper main-conference sessions from official ICRA pages.

    The official WordPress pages currently block local requests in some environments,
    so these rows are kept as explicit metadata while technical papers and workshop
    pages remain crawler-driven.
    """

    sessions = [
        conference_session(
            id="keynote-1",
            kind="keynote",
            title="Keynote 1: Autonomous Vehicles & Navigation",
            day="Tuesday",
            start="11:00",
            end="12:30",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk(
                    "Learning to Handle Autonomous Vehicles at the Limits - Lessons Learned from Real-World Autonomous Motorsport",
                    "Johannes Betz, Technical University of Munich",
                    "Autonomous racing, learning-based motion planning, control, uncertainty",
                ),
                talk(
                    "From Neuroscience to Autonomous Vehicle Navigation",
                    "Michael Milford, QUT Centre for Robotics",
                    "Navigation, localization, autonomous vehicles, field deployment",
                ),
                talk(
                    "Toward Behaviorally-Intelligent Robots: Safe Navigation in Unstructured and Human-Centered Environments",
                    "Aniket Bera, Purdue University",
                    "Safe navigation, semantic scene understanding, world modeling, human behavior prediction",
                ),
                talk(
                    "Learning to Navigate: From Scene Understanding to Decision Making",
                    "Hesheng Wang, Shanghai Jiao Tong University",
                    "Scene understanding, dynamic environments, decision making",
                ),
            ],
        ),
        conference_session(
            id="keynote-2",
            kind="keynote",
            title="Keynote 2: Medical & Healthcare Robotics",
            day="Tuesday",
            start="16:45",
            end="18:15",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk("Using Magnetic Fields to Control Tiny Robots in the Gut and Brain", "Eric Diller, University of Toronto"),
                talk(
                    "From Bioinspired Design to Safe Control: Emerging Challenges in Medical Robotics",
                    "Fanny Ficuciello, University of Naples Federico II",
                ),
                talk("Magnetically Actuated Microrobots for Precision Medicine", "Tiantian Xu, SIAT"),
                talk(
                    "Towards Wearable Robotics with better Portability, Safety, and Comfort",
                    "Haoyong Yu, National University of Singapore",
                ),
            ],
        ),
        conference_session(
            id="keynote-3",
            kind="keynote",
            title="Keynote 3: Robot Perception & Spatial AI",
            day="Wednesday",
            start="11:00",
            end="12:30",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk(
                    "The Underdog Sensors: Are Robots Using Thermal and Radar Right?",
                    "Ayoung Kim, Seoul National University",
                    "Perceptual robotics, SLAM, state estimation, spatial representation learning, LiDAR, radar, thermal infrared, vision",
                ),
                talk(
                    "Maps, Memory, and Tasks: Toward Spatial AI for the Next Generation of Robots",
                    "Luca Carlone, Massachusetts Institute of Technology",
                    "Spatial AI, maps, memory, tasks, 3D reconstruction, geometric foundation models, SLAM",
                ),
                talk(
                    "Advancing Service Robots Through Active Perception: Mapping and Object Search Under Occlusion",
                    "Maren Bennewitz, University of Bonn",
                    "Active perception, 3D mapping, object search, occlusion",
                ),
                talk(
                    "Why Field Robotics Research Still Matters",
                    "Timothy Barfoot, University of Toronto",
                    "Field robotics, state estimation, SLAM, mining, planetary rovers, autonomy",
                ),
            ],
        ),
        conference_session(
            id="keynote-4",
            kind="keynote",
            title="Keynote 4: Manipulation, Humanoids, Embodied Design",
            day="Wednesday",
            start="16:45",
            end="18:15",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk("Do We Still Need Dexterous Hands?", "Jeannette Bohg, Stanford University"),
                talk(
                    "At the Intersection of Biology and Machines: From Musculoskeletal to Wire-driven Robots",
                    "Kento Kawaharazuka, The University of Tokyo",
                ),
                talk(
                    "Modular Bodies and Recovery Capabilities: Building Robots for Unstructured Environments",
                    "Nikos Tsagarakis, Istituto Italiano di Tecnologia",
                ),
                talk("Building Generalist Humanoid Robots", "Yuke Zhu, UT Austin"),
            ],
        ),
        conference_session(
            id="keynote-5",
            kind="keynote",
            title="Keynote 5: Robot Learning, Planning & Foundation Models",
            day="Thursday",
            start="11:00",
            end="12:30",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk(
                    "Scalable Robot Decision Making in the Open World: Planning and Plan Prediction with LLMs",
                    "David Hsu, National University of Singapore",
                ),
                talk(
                    "Towards Complex Language in Partially Observed Environments",
                    "Stefanie Tellex, Brown University",
                    "Language, partially observed environments, POMDP, object search",
                ),
                talk(
                    "Traveling the Robot Learning Manifold: A Tale of Geometries and Inductive Biases",
                    "Noemie Jaquier, KTH Royal Institute of Technology",
                    "Geometric robot learning, differential geometry, inductive biases",
                ),
                talk(
                    "Intrinsic Robustness: A Journey from Control-Aware Planning to Robust Robot Learning",
                    "Paolo Robuffo Giordano, IRISA Rennes",
                    "Robust planning, uncertainty propagation, robot learning, MPC",
                ),
            ],
        ),
        conference_session(
            id="keynote-6",
            kind="keynote",
            title="Keynote 6: Human-Robot Interaction",
            day="Thursday",
            start="16:45",
            end="18:15",
            room="Hall A1 (Plenary)",
            url=KEYNOTE_SESSIONS_URL,
            context="Main keynote session",
            items=[
                talk(
                    "Challenges in Adaptive Robot Teaming: Understanding Human Teammate Performance",
                    "Julie A. Adams, Oregon State University",
                ),
                talk(
                    "Guiding with Touch: Wearable Haptics for Shaping Human-Robot Interaction",
                    "Marcia O'Malley, Rice University",
                ),
                talk(
                    "Engineering Human Agency and Self-Efficacy: The Next Frontier of Human-Robot Symbiosis",
                    "Tetsunari Inamura, Tamagawa University",
                ),
                talk(
                    "Overcoming Manipulation Challenges in Environmental Robotics through AI-based Solutions and Human-Robot Partnership",
                    "Berk Calli, Worcester Polytechnic Institute",
                ),
            ],
        ),
        conference_session(
            id="plenary-1",
            kind="plenary",
            title='Can GOFE and Code-as-Policy Close the 100,000-Year "Data Gap" in Robot Manipulation?',
            day="Tuesday",
            start="14:00",
            end="14:50",
            room="Hall A1 (Plenary)",
            url=PLENARY_SESSIONS_URL,
            context="Plenary session",
            items=[talk('Can GOFE and Code-as-Policy Close the 100,000-Year "Data Gap" in Robot Manipulation?', "Ken Goldberg, UC Berkeley and Ambi Robotics")],
        ),
        conference_session(
            id="plenary-2",
            kind="plenary",
            title="Natural Revolution: Biological Principles for Frugal and Sustainable Robotics",
            day="Wednesday",
            start="14:00",
            end="14:50",
            room="Hall A1 (Plenary)",
            url=PLENARY_SESSIONS_URL,
            context="Plenary session",
            items=[talk("Natural Revolution: Biological Principles for Frugal and Sustainable Robotics", "Barbara Mazzolai, Istituto Italiano di Tecnologia")],
        ),
        conference_session(
            id="plenary-3",
            kind="plenary",
            title="Aerial Robots - From Omnidirectional Flight to Physical Interaction at Height",
            day="Thursday",
            start="14:00",
            end="14:50",
            room="Hall A1 (Plenary)",
            url=PLENARY_SESSIONS_URL,
            context="Plenary session",
            items=[talk("Aerial Robots - From Omnidirectional Flight to Physical Interaction at Height", "Roland Siegwart, ETH Zurich")],
        ),
        conference_session(
            id="keynote-tutorial-1",
            kind="keynote_tutorial",
            title="Learning Agile Vision-based Quadrotor Flight: from Simulation to Real-world Adaption",
            day="Tuesday",
            start="09:00",
            end="10:30",
            room="Strauss 1-2",
            url=KEYNOTE_TUTORIALS_URL,
            context="Keynote tutorial",
            items=[
                talk(
                    "Learning Agile Vision-based Quadrotor Flight: from Simulation to Real-world Adaption",
                    "Davide Scaramuzza, Rudolf Reiter, Ismail Geles",
                    "Simulation-to-real adaptation, vision-based flight, state estimation, differentiable simulation, reinforcement learning",
                    kind="tutorial",
                )
            ],
        ),
        conference_session(
            id="keynote-tutorial-2",
            kind="keynote_tutorial",
            title="The Open Motion Planning Library (OMPL 2.0)",
            day="Tuesday",
            start="15:00",
            end="16:30",
            room="Strauss 1-2",
            url=KEYNOTE_TUTORIALS_URL,
            context="Keynote tutorial",
            items=[
                talk(
                    "The Open Motion Planning Library (OMPL 2.0)",
                    "Lydia Kavraki, Thai Duong, Theodoros Tyrovouzis, Clayton Ramsey, Nikki Hart, Arden Knoll",
                    "Sampling-based motion planning, OMPL, task and motion planning",
                    kind="tutorial",
                )
            ],
        ),
        conference_session(
            id="keynote-tutorial-3",
            kind="keynote_tutorial",
            title="Building, Running and Deploying Modern Software Tools for Robotics",
            day="Wednesday",
            start="15:00",
            end="16:30",
            room="Strauss 1-2",
            url=KEYNOTE_TUTORIALS_URL,
            context="Keynote tutorial",
            items=[
                talk(
                    "Building, Running and Deploying Modern Software Tools for Robotics",
                    "Peter Corke, Tobias Fischer",
                    "Robotics software, reproducibility, Python toolboxes, ROS, SLAM",
                    kind="tutorial",
                )
            ],
        ),
        conference_session(
            id="keynote-tutorial-4",
            kind="keynote_tutorial",
            title="Behavior Foundation Models from the Ground Up: A Hands-On Tutorial",
            day="Thursday",
            start="15:00",
            end="16:30",
            room="Strauss 1-2",
            url=KEYNOTE_TUTORIALS_URL,
            context="Keynote tutorial",
            items=[
                talk(
                    "Behavior Foundation Models from the Ground Up: A Hands-On Tutorial",
                    "Rudolf Lioutikov",
                    "Behavior foundation models, vision-language-action models, robot learning",
                    kind="tutorial",
                )
            ],
        ),
        conference_session(
            id="panel-1",
            kind="panel",
            title="Panel 1: From Humanoid Robotics Research to Startup Creation: The Role of Public Funding",
            day="Tuesday",
            start="09:00",
            end="10:30",
            room="Hall A1",
            url=PANEL_SESSIONS_URL,
            context="Panel session",
        ),
        conference_session(
            id="panel-2",
            kind="panel",
            title="Panel 2: Advancing Sustainability in Robotics: From Green Design to Real-World Impact",
            day="Tuesday",
            start="15:00",
            end="16:30",
            room="Hall A1",
            url="https://2026.ieee-icra.org/event/panel-2-advancing-sustainability-in-robotics-from-green-design-to-real-world-impact/",
            context="Panel session; moderators/speakers include Bram Vanderborght, Aude Billard, Barbara Mazzolai, Ludovic Righetti, Cecilia Laschi, Mirko Kovac",
        ),
        conference_session(
            id="panel-3",
            kind="panel",
            title="Panel 3: Building Sustainable and Trustworthy AI for Automation",
            day="Wednesday",
            start="09:00",
            end="10:30",
            room="Hall A1",
            url=PANEL_SESSIONS_URL,
            context="Panel session",
        ),
        conference_session(
            id="panel-4",
            kind="panel",
            title="Panel 4: Publish or Perish: Surviving the Paper Deluge - Is AI the solution? If not, what is the Solution?",
            day="Wednesday",
            start="15:00",
            end="16:30",
            room="Hall A1",
            url="https://2026.ieee-icra.org/event/panel-4/",
            context="Panel session; moderator Aude Billard; speakers include Renaud Detry, Greg Dudek, Nadia Figueroa, Dongheui Lee, Shigeki Sugano, Kunpeng Yao",
        ),
        conference_session(
            id="panel-5",
            kind="panel",
            title='Panel 5: "Robots for All" in a Fragmented World: Competing Global Visions and Shared Futures from Europe, Asia, and the United States',
            day="Thursday",
            start="09:00",
            end="10:30",
            room="Hall A1",
            url=PANEL_SESSIONS_URL,
            context="Panel session",
        ),
        conference_session(
            id="panel-6",
            kind="panel",
            title="Panel 6: Return on Humanoid Investment",
            day="Thursday",
            start="15:00",
            end="16:30",
            room="Hall A1",
            url="https://2026.ieee-icra.org/event/panel-6/",
            context="Panel session",
        ),
        conference_session(
            id="industry-keynote-1",
            kind="industry_keynote",
            title="Industry Keynote Session 1",
            day="Wednesday",
            start="09:00",
            end="10:30",
            room="Strauss 1-2",
            url="https://2026.ieee-icra.org/event/industry-keynote-session-1/",
            context="Industry keynote session",
            items=[
                talk("Automating Documentation Artifacts in Safety Critical Processes", "EIT Manufacturing, Dominik Kerschat"),
                talk("ATRO - The future of robotics is modular", "Beckhoff Automation GmbH, Thomas Morscher-Unger"),
                talk("Translating Innovation: Closing the Gap in Physical AI Deployment", "Franka Robotics, Sven Parusel"),
                talk("Amazon's Robotic Manipulation", "Amazon, Aaron Parness"),
                talk("The road towards a new era of actuators for humanoids", "Infineon Technologies AG, Maurizio Incurvati"),
                talk("Breaking Boundaries in 3D Perception, Empowering Spatial Intelligence", "Robosense, Xiansheng Yang"),
                talk("Build a Bridge Between AI Intelligence and the Physical World", "PaXini Tech, Li Jiale"),
                talk("Towards the AlphaGo and ChatGPT Moments of Embodied AI", "Galbot, He Wang"),
                talk("What Data Makes Robots Work?", "Encord, Alejandra Gutierrez"),
                talk("Bridging the Last Millimeter in Contact-Rich Manipulation", "Flexiv Robotics, Shuyun Chung"),
            ],
        ),
        conference_session(
            id="industry-keynote-2",
            kind="industry_keynote",
            title="Industry Keynote Session 2",
            day="Thursday",
            start="09:00",
            end="10:30",
            room="Strauss 1-2",
            url="https://2026.ieee-icra.org/event/industry-keynote-session-2/",
            context="Industry keynote session",
            items=[
                talk("From Demos to Deployment: Building Humanoids That Work", "Technology Innovation Institute, Danilo Caporale"),
                talk("Foundations for General Physical Intelligence", "TARS, Wenchao Ding"),
                talk("Gento: Touching the world Gently", "Gento Robotics, Hanwen Kang"),
                talk("Newton Physics Simulation Engine: A Lightwheel Perspective on Robotics Applications", "Lightwheel, Martin Elbs"),
                talk("Robotic Foundation Models That Learn While Deploying", "AGIBOT, Jianlan Luo"),
                talk("GPUs for Robotics: Benchmarking LeRobot Policy Training Across GPU Architectures", "NEBIUS, Timothy Le, Mikhail Rozhkov"),
                talk("Bare Metal to Models: Accelerating embodied AI", "Weights & Biases by CoreWeave, Edmund Kuras"),
                talk("Publishing in Wiley's Advanced Portfolio journals: Advanced Robotics Research & Advanced Intelligent Systems", "Wiley, Sneha Rhode Gupta"),
            ],
        ),
        conference_session(
            id="ras-conference-organizers-workshop",
            kind="ras_event",
            title="2026 IEEE RAS Conference Organizers Workshop",
            day="Tuesday",
            start="10:00",
            end="11:00",
            room="",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
        conference_session(
            id="ras-town-hall",
            kind="ras_event",
            title="RAS Town Hall",
            day="Tuesday",
            start="18:15",
            end="19:15",
            room="",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
        conference_session(
            id="ras-lunch-with-leaders",
            kind="ras_event",
            title="Lunch with Leaders",
            day="Wednesday",
            start="12:30",
            end="14:00",
            room="",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
        conference_session(
            id="ras-science-communication-crash-course",
            kind="ras_event",
            title="Science Communication Crash Course",
            day="Wednesday",
            start="13:00",
            end="14:00",
            room="",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
        conference_session(
            id="ras-awards-lunch",
            kind="ras_event",
            title="ICRA 2026 Awards Lunch",
            day="Thursday",
            start="12:30",
            end="14:00",
            room="Hall A / Exhibition Hall",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
        conference_session(
            id="ras-community-building-day",
            kind="ras_event",
            title="Community Building Day",
            day="Thursday",
            start="08:00",
            end="18:00",
            room="",
            url=RAS_EVENTS_URL,
            context="RAS event",
        ),
    ]

    for day in ["Tuesday", "Wednesday", "Thursday"]:
        day_id = day.lower()
        sessions.extend(
            [
                conference_session(
                    id=f"ras-tech-talk-stage-{day_id}",
                    kind="ras_event",
                    title="Tech Talk Stage",
                    day=day,
                    start="09:00",
                    end="17:00",
                    room="",
                    url=RAS_EVENTS_URL,
                    context="RAS event stage",
                ),
                conference_session(
                    id=f"ras-innovation-stage-{day_id}",
                    kind="ras_event",
                    title="Innovation Stage",
                    day=day,
                    start="09:00",
                    end="17:00",
                    room="",
                    url=RAS_EVENTS_URL,
                    context="RAS event stage",
                ),
            ]
        )

    day_order = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, "Friday": 5}
    sessions.sort(key=lambda x: (day_order.get(x.get("day", ""), 9), minutes_of_day(x.get("start", "")) or 9999, x.get("title", "")))
    return sessions


def build_html(dataset: dict) -> str:
    data_json = json.dumps(dataset, ensure_ascii=False, separators=(",", ":"))
    generated = html.escape(dataset["meta"]["generatedAt"])
    floorplan_page_url = html.escape(FLOORPLAN_PAGE_URL, quote=True)
    floorplan_image_path = html.escape(FLOORPLAN_IMAGE_PATH, quote=True)
    floorplan_pdf_path = html.escape(FLOORPLAN_PDF_PATH, quote=True)
    visitor_counter_url = html.escape(VISITOR_COUNTER_BADGE_URL, quote=True)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ICRA 2026 Schedule Explorer</title>
<style>
:root {{
  --bg:#f7f8fb; --panel:#fff; --fg:#18202a; --muted:#667085; --line:#d8dee8;
  --accent:#126c73; --accent2:#7a4eac; --soft:#eaf6f6; --warn:#a45f00;
  --paper:#e8f2ff; --workshop:#f0ecff; --presentation:#fff4e2; --slot:#ecf7ef; --session:#e8f7fb;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif; line-height:1.55; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:0 24px; }}
header {{ background:#fff; border-bottom:1px solid var(--line); padding:42px 0 28px; }}
h1 {{ margin:0 0 10px; font-size:38px; line-height:1.15; letter-spacing:0; overflow-wrap:anywhere; }}
.lead {{ margin:0; max-width:920px; color:var(--muted); font-size:17px; overflow-wrap:anywhere; }}
.meta {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:18px; }}
.chip {{ display:inline-flex; align-items:center; gap:6px; max-width:100%; padding:5px 10px; border:1px solid var(--line); border-radius:999px; background:#fff; color:#475467; font-size:12px; overflow-wrap:anywhere; }}
.visitorChip {{ padding:4px 8px; }}
.visitorChip img {{ display:block; height:20px; max-width:100%; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px; box-shadow:0 1px 2px rgba(16,24,40,.03); }}
.controls {{ display:grid; grid-template-columns:1.1fr 1.1fr .75fr .65fr .65fr .7fr; gap:12px; align-items:end; margin:22px 0; }}
.controls > div {{ min-width:0; }}
.secondaryControls {{ grid-template-columns:1.1fr .6fr; margin:0; }}
label {{ display:block; font-size:12px; color:var(--muted); font-weight:700; margin-bottom:6px; text-transform:uppercase; letter-spacing:.04em; }}
input,select,button {{ width:100%; min-width:0; border:1px solid var(--line); border-radius:8px; padding:10px 11px; background:#fff; color:var(--fg); font-size:14px; }}
select {{ text-overflow:ellipsis; }}
button {{ background:var(--accent); border-color:var(--accent); color:#fff; font-weight:700; cursor:pointer; }}
button.secondary {{ background:#fff; color:var(--accent); }}
.layout {{ display:grid; grid-template-columns:280px 1fr; gap:18px; align-items:start; }}
.side {{ position:sticky; top:10px; }}
.stat {{ display:grid; grid-template-columns:1fr auto; gap:8px; padding:9px 0; border-bottom:1px dashed var(--line); font-size:14px; }}
.stat:last-child {{ border-bottom:0; }}
.stat b {{ font-variant-numeric:tabular-nums; }}
.resultsHead {{ display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:12px; }}
.resultsHead h2 {{ margin:0; font-size:22px; }}
.floorplanPanel {{ margin-top:18px; }}
.floorplanHead {{ display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }}
.floorplanHead h2 {{ margin:0; font-size:22px; }}
.floorplanActions {{ display:flex; gap:10px; flex-wrap:wrap; }}
.floorplanImageWrap {{ overflow:auto; border:1px solid var(--line); border-radius:10px; background:#fff; }}
.floorplanImage {{ display:block; width:100%; min-width:760px; height:auto; }}
.group {{ margin-bottom:18px; }}
.groupTitle {{ display:flex; align-items:center; gap:10px; margin:0 0 8px; color:#344054; font-size:15px; font-weight:800; }}
.card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 15px; margin-bottom:10px; }}
.card.paper {{ border-left:5px solid #2d75bd; }}
.card.workshop {{ border-left:5px solid #7a4eac; }}
.card.workshop_presentation {{ border-left:5px solid #d3831f; }}
.card.workshop_slot {{ border-left:5px solid #278553; }}
.card.conference_session {{ border-left:5px solid #0f7f94; }}
.topline {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:6px; }}
.badge {{ display:inline-flex; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:800; color:#344054; background:#eef2f7; }}
.badge.paper {{ background:var(--paper); color:#195383; }}
.badge.workshop {{ background:var(--workshop); color:#57318b; }}
.badge.workshop_presentation {{ background:var(--presentation); color:#7a4a00; }}
.badge.workshop_slot {{ background:var(--slot); color:#12613a; }}
.badge.conference_session {{ background:var(--session); color:#075667; }}
.title {{ margin:0 0 7px; font-size:16px; line-height:1.35; font-weight:800; overflow-wrap:anywhere; }}
.subtle {{ color:var(--muted); font-size:13px; overflow-wrap:anywhere; }}
.snippet {{ margin-top:8px; color:#344054; font-size:13px; }}
.kw {{ background:#fff0b3; border-radius:4px; padding:0 2px; }}
.empty {{ padding:28px; text-align:center; color:var(--muted); }}
.warn {{ background:#fff8eb; border:1px solid #f3d098; color:#5d3a00; border-radius:10px; padding:12px 14px; font-size:13px; margin-top:12px; overflow-wrap:anywhere; }}
details {{ border-top:1px solid var(--line); padding-top:12px; margin-top:12px; }}
summary {{ cursor:pointer; color:var(--accent); font-weight:700; font-size:13px; }}
.detailBody {{ margin-top:10px; color:#344054; font-size:13px; }}
.slotItems {{ display:grid; gap:9px; margin-top:10px; }}
.slotItem {{ border:1px solid #e4e9f2; border-radius:8px; padding:9px 10px; background:#fbfcfe; }}
.slotItemTitle {{ font-weight:800; color:#263342; overflow-wrap:anywhere; }}
.slotMeta {{ margin-top:3px; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }}
.slotItem details {{ margin-top:8px; padding-top:8px; }}
.smallList {{ max-height:260px; overflow:auto; padding-right:6px; }}
.smallItem {{ padding:7px 0; border-bottom:1px dashed var(--line); font-size:12px; color:#475467; }}
.gapTitle {{ margin-top:14px; color:#344054; font-size:13px; font-weight:800; }}
@media (max-width:940px) {{
  .controls {{ grid-template-columns:1fr 1fr; }}
  .secondaryControls {{ grid-template-columns:1fr 1fr; }}
  .layout {{ grid-template-columns:1fr; }}
  .side {{ position:static; }}
}}
@media (max-width:620px) {{
  .controls {{ grid-template-columns:1fr; }}
  .secondaryControls {{ grid-template-columns:1fr; }}
  h1 {{ font-size:29px; }}
  .wrap {{ padding:0 16px; }}
}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>ICRA 2026 Schedule Explorer</h1>
    <p class="lead">Enter a day, time range, and comma-separated keywords to search ICRA technical papers, conference sessions, and workshop timetable slots. Matching uses titles, abstracts, keywords, author names, speakers, and nested session paper/topic text.</p>
    <div class="meta">
      <span class="chip">Technical papers: <b id="paperCount"></b></span>
      <span class="chip">Conference sessions: <b id="sessionCount"></b></span>
      <span class="chip">Workshops scanned: <b id="workshopCount"></b></span>
      <span class="chip">Workshop timetable slots: <b id="slotCount"></b></span>
      <span class="chip">Generated: {generated}</span>
      <span class="chip visitorChip"><img src="{visitor_counter_url}" alt="Visitor count" loading="lazy"></span>
    </div>
  </div>
</header>

<main class="wrap">
  <section class="panel">
    <div class="controls">
      <div>
        <label for="q">Include keywords</label>
        <input id="q" placeholder="Comma-separated keywords" value="">
      </div>
      <div>
        <label for="excludeQ">Exclude keywords</label>
        <input id="excludeQ" placeholder="Hide matching keywords" value="">
      </div>
      <div>
        <label for="day">Day</label>
        <select id="day">
          <option value="">Any day</option>
          <option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option>
        </select>
      </div>
      <div>
        <label for="start">Start</label>
        <input id="start" type="text" inputmode="numeric" pattern="[0-9]{{2}}:[0-9]{{2}}" placeholder="00:00" value="00:00">
      </div>
      <div>
        <label for="end">End</label>
        <input id="end" type="text" inputmode="numeric" pattern="[0-9]{{2}}:[0-9]{{2}}" placeholder="24:00" value="24:00">
      </div>
      <div>
        <label>&nbsp;</label>
        <button id="searchBtn">Search</button>
      </div>
    </div>
    <div class="controls secondaryControls">
      <div>
        <label for="mode">Keyword match</label>
        <select id="mode">
          <option value="all">Require every keyword (AND)</option>
          <option value="any">Match any keyword (OR)</option>
          <option value="phrase">Exact phrase</option>
        </select>
      </div>
      <div>
        <label>&nbsp;</label>
        <button class="secondary" id="resetBtn">Reset</button>
      </div>
    </div>
  </section>

  <section class="panel floorplanPanel" id="floorplan">
    <div class="floorplanHead">
      <h2>Conference Floorplan</h2>
      <div class="floorplanActions">
        <a class="chip" href="{floorplan_pdf_path}" target="_blank" rel="noreferrer">Open PDF</a>
        <a class="chip" href="{floorplan_page_url}" target="_blank" rel="noreferrer">Official page</a>
      </div>
    </div>
    <div class="floorplanImageWrap">
      <a href="{floorplan_pdf_path}" target="_blank" rel="noreferrer">
        <img class="floorplanImage" src="{floorplan_image_path}" alt="ICRA 2026 conference venue floorplan" loading="lazy">
      </a>
    </div>
  </section>

  <section class="layout" style="margin-top:18px;">
    <aside class="panel side">
      <div class="stat"><span>Matched total</span><b id="matchedTotal">0</b></div>
    </aside>
    <section class="panel">
      <div class="resultsHead">
        <h2>Results</h2>
        <span class="subtle" id="resultNote"></span>
      </div>
      <div id="results"></div>
    </section>
  </section>
</main>

<script id="schedule-data" type="application/json">{data_json}</script>
<script>
const DATA = JSON.parse(document.getElementById('schedule-data').textContent);
const allItems = [...DATA.papers, ...(DATA.sessions || []), ...DATA.workshopSlots];
const dayOrder = {{Monday:1, Tuesday:2, Wednesday:3, Thursday:4, Friday:5, Sunday:0}};

function minutes(t) {{
  if (!t) return null;
  const m = String(t).match(/(\\d{{1,2}}):(\\d{{2}})/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}}
function overlaps(item, start, end) {{
  const s = minutes(item.start), e = minutes(item.end);
  const qs = minutes(start), qe = minutes(end);
  if (s == null || e == null || qs == null || qe == null) return true;
  return s < qe && e > qs;
}}
function terms(q, mode) {{
  q = q.trim().toLowerCase();
  if (!q) return [];
  if (mode === 'phrase') return [q];
  const parts = q.includes(',') ? q.split(/[,;]+/) : q.split(/\\s+(?:or|and)\\s+|\\s+/i);
  return parts.map(x => x.trim()).filter(x => x && !['and', 'or'].includes(x));
}}
function textMatchesTerms(text, tt, mode, q) {{
  const haystack = String(text || '').toLowerCase();
  if (!tt.length) return true;
  if (mode === 'any' || /\\s+or\\s+/i.test(q)) return tt.some(t => haystack.includes(t));
  return tt.every(t => haystack.includes(t));
}}
function nestedText(x) {{
  return [x.title, x.speaker, x.context, x.abstract, x.paperId].filter(Boolean).join(' ');
}}
function parentText(item) {{
  if (item.type === 'workshop_slot') {{
    return [item.title, item.workshopTitle, item.category, item.slotKinds, item.room, item.day, item.time].filter(Boolean).join(' ');
  }}
  if (item.type === 'conference_session') {{
    return [item.title, item.kind, item.context, item.room, item.day, item.time].filter(Boolean).join(' ');
  }}
  return item.searchText || '';
}}
function matchesKeyword(item, q, mode) {{
  const tt = terms(q, mode);
  if (!tt.length) return true;
  const children = item.items || [];
  if (!children.length) return textMatchesTerms(item.searchText || '', tt, mode, q);
  return textMatchesTerms(parentText(item), tt, mode, q)
    || children.some(x => textMatchesTerms(nestedText(x), tt, mode, q));
}}
function textHasExcludedTerm(text, excludeQ) {{
  const excluded = terms(excludeQ, 'all');
  return excluded.length > 0 && textMatchesTerms(text, excluded, 'any', excludeQ);
}}
function itemParentExcluded(item, excludeQ) {{
  const children = item.items || [];
  return textHasExcludedTerm(children.length ? parentText(item) : (item.searchText || ''), excludeQ);
}}
function visibleNestedItems(item, q, mode, excludeQ) {{
  const children = item.items || [];
  const tt = terms(q, mode);
  const allowedChildren = children.filter(x => !textHasExcludedTerm(nestedText(x), excludeQ));
  if (!tt.length || !children.length || textMatchesTerms(parentText(item), tt, mode, q)) return allowedChildren;
  return allowedChildren.filter(x => textMatchesTerms(nestedText(x), tt, mode, q));
}}
function passesExclude(item, q, mode, excludeQ) {{
  if (itemParentExcluded(item, excludeQ)) return false;
  const children = item.items || [];
  return !children.length || visibleNestedItems(item, q, mode, excludeQ).length > 0;
}}
function nestedCountLabel(item, shownItems, noun) {{
  const total = item.slotItemCount || (item.items || []).length || shownItems.length;
  if (shownItems.length && shownItems.length !== total) return `${{shownItems.length}} of ${{total}} ${{noun}}`;
  return `${{total}} ${{noun}}`;
}}
function detailText(item, q) {{
  const raw = item.abstract || item.searchText || '';
  return highlightText(raw, q);
}}
function highlightText(s, q) {{
  const raw = String(s || '');
  const tt = [...new Set(terms(q, 'all'))].sort((a, b) => b.length - a.length);
  if (!tt.length) return escapeHtml(raw);
  const re = new RegExp(tt.map(escapeReg).join('|'), 'ig');
  let out = '';
  let last = 0;
  for (const match of raw.matchAll(re)) {{
    out += escapeHtml(raw.slice(last, match.index));
    out += `<span class="kw">${{escapeHtml(match[0])}}</span>`;
    last = match.index + match[0].length;
  }}
  return out + escapeHtml(raw.slice(last));
}}
function escapeHtml(s) {{
  return String(s || '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function escapeReg(s) {{ return s.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'); }}
function compactText(s) {{ return String(s || '').toLowerCase().replace(/\\W+/g, ''); }}
function typeLabel(t) {{
  return t === 'paper' ? 'Technical paper' : (t === 'workshop_slot' ? 'Workshop timetable slot' : 'Conference session');
}}
function itemSort(a,b) {{
  return (dayOrder[a.day] ?? 9) - (dayOrder[b.day] ?? 9)
    || (minutes(a.start) ?? 9999) - (minutes(b.start) ?? 9999)
    || (a.type || '').localeCompare(b.type || '')
    || (a.title || '').localeCompare(b.title || '');
}}
function render() {{
  const q = document.getElementById('q').value;
  const excludeQ = document.getElementById('excludeQ').value;
  const day = document.getElementById('day').value;
  const start = document.getElementById('start').value || '00:00';
  const end = document.getElementById('end').value || '24:00';
  const mode = document.getElementById('mode').value;
  let rows = allItems.filter(item => (!day || item.day === day) && overlaps(item, start, end) && matchesKeyword(item, q, mode) && passesExclude(item, q, mode, excludeQ));
  rows.sort(itemSort);
  const fullCount = rows.length;
  document.getElementById('matchedTotal').textContent = fullCount;
  document.getElementById('resultNote').textContent = '';
  const groups = new Map();
  for (const item of rows) {{
    const key = `${{item.day || ''}} ${{item.start || ''}}-${{item.end || ''}}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }}
  const root = document.getElementById('results');
  if (!rows.length) {{
    root.innerHTML = '<div class="empty">No matches. Try broader keywords or a wider time range.</div>';
    return;
  }}
  root.innerHTML = [...groups.entries()].map(([slot, items]) => `
    <div class="group">
      <div class="groupTitle">${{escapeHtml(slot)}} <span class="badge">${{items.length}} items</span></div>
      ${{items.map(item => card(item, q, mode, excludeQ)).join('')}}
    </div>`).join('');
}}
function card(item, q, mode, excludeQ) {{
  if (item.type === 'paper') return paperCard(item, q);
  if (item.type === 'workshop_slot') return workshopSlotCard(item, q, mode, excludeQ);
  return sessionCard(item, q, mode, excludeQ);
}}
function paperCard(item, q) {{
  const loc = [item.day, item.time, item.room].filter(Boolean).join(' · ');
  const sub = [item.code, item.sessionTitle, item.authors].filter(Boolean).join(' · ');
  const kw = item.keywords && item.keywords.length ? `<div class="subtle">${{item.keywords.map(escapeHtml).join(', ')}}</div>` : '';
  const abstract = item.abstract
    ? `<details><summary>Abstract</summary><div class="detailBody">${{detailText(item, q)}}</div></details>`
    : '';
  return `<article class="card paper">
    <div class="topline">
      <span class="badge paper">${{typeLabel(item.type)}}</span>
      <span class="subtle">${{escapeHtml(loc)}}</span>
    </div>
    <h3 class="title"><a href="${{escapeHtml(item.url || '#')}}" target="_blank" rel="noreferrer">${{highlightText(item.title, q)}}</a></h3>
    <div class="subtle">${{escapeHtml(sub)}}</div>
    ${{kw}}
    ${{abstract}}
  </article>`;
}}
function workshopSlotCard(item, q, mode, excludeQ) {{
  const loc = [item.day, item.time, item.room].filter(Boolean).join(' · ');
  const shownItems = visibleNestedItems(item, q, mode, excludeQ);
  const sub = [item.workshopTitle, nestedCountLabel(item, shownItems, 'papers/topics')].filter(Boolean).join(' · ');
  return `<article class="card workshop_slot">
    <div class="topline">
      <span class="badge workshop_slot">${{typeLabel(item.type)}}</span>
      <span class="subtle">${{escapeHtml(loc)}}</span>
    </div>
    <h3 class="title"><a href="${{escapeHtml(item.url || '#')}}" target="_blank" rel="noreferrer">${{highlightText(item.title, q)}}</a></h3>
    <div class="subtle">${{escapeHtml(sub)}}</div>
    <div class="slotItems">${{shownItems.map(x => slotItem(x, q, item)).join('')}}</div>
  </article>`;
}}
function sessionCard(item, q, mode, excludeQ) {{
  const loc = [item.day, item.time, item.room].filter(Boolean).join(' · ');
  const shownItems = visibleNestedItems(item, q, mode, excludeQ);
  const sub = [item.context, nestedCountLabel(item, shownItems, 'items')].filter(Boolean).join(' · ');
  const details = shownItems.length
    ? `<div class="slotItems">${{shownItems.map(x => slotItem(x, q, item)).join('')}}</div>`
    : '';
  return `<article class="card conference_session">
    <div class="topline">
      <span class="badge conference_session">${{typeLabel(item.type)}}</span>
      <span class="subtle">${{escapeHtml(loc)}}</span>
    </div>
    <h3 class="title"><a href="${{escapeHtml(item.url || '#')}}" target="_blank" rel="noreferrer">${{highlightText(item.title, q)}}</a></h3>
    <div class="subtle">${{highlightText(sub, q)}}</div>
    ${{details}}
  </article>`;
}}
function slotItem(x, q, parent) {{
  const genericSpeaker = /^(lightning|poster|oral|talk|paper)$/i.test(x.speaker || '');
  let context = x.context || '';
  if (x.paperId) context = context.replace(new RegExp(`^${{escapeReg(x.paperId)}}\\\\s*·\\\\s*`, 'i'), '');
  if (/^(lightning|poster|oral|talk|paper)$/i.test(context) || /tentative schedule|please see|workshop will be held|held in person|schedule below/i.test(context)) context = '';
  const titleKey = compactText(x.title);
  const parentTitleKey = compactText(parent && parent.title);
  const parts = [x.paperId, genericSpeaker ? '' : x.speaker, context].filter(Boolean);
  const metaParts = [];
  for (const part of parts) {{
    const key = compactText(part);
    if (!key || key === titleKey || key === parentTitleKey || metaParts.some(p => compactText(p) === key)) continue;
    metaParts.push(part);
  }}
  const meta = metaParts.join(' · ');
  const repeatedTitle = parent && (parent.slotItemCount || 0) === 1 && titleKey && titleKey === parentTitleKey;
  const abstract = x.abstract
    ? `<details><summary>Abstract</summary><div class="detailBody">${{highlightText(x.abstract, q)}}</div></details>`
    : '';
  const titleHtml = repeatedTitle ? '' : `<div class="slotItemTitle"><a href="${{escapeHtml(x.url || '#')}}" target="_blank" rel="noreferrer">${{highlightText(x.title, q)}}</a></div>`;
  const metaHtml = meta ? `<div class="slotMeta">${{highlightText(meta, q)}}</div>` : '';
  return `<div class="slotItem">
    ${{titleHtml}}
    ${{metaHtml}}
    ${{abstract}}
  </div>`;
}}
function clearResults() {{
  document.getElementById('matchedTotal').textContent = '0';
  document.getElementById('resultNote').textContent = '';
  document.getElementById('results').innerHTML = '<div class="empty">Set filters and click Search.</div>';
}}
document.getElementById('paperCount').textContent = DATA.papers.length;
document.getElementById('sessionCount').textContent = (DATA.sessions || []).length;
document.getElementById('workshopCount').textContent = DATA.workshops.length;
document.getElementById('slotCount').textContent = DATA.workshopSlots.length;
document.getElementById('searchBtn').addEventListener('click', render);
document.getElementById('resetBtn').addEventListener('click', () => {{
  document.getElementById('q').value = '';
  document.getElementById('excludeQ').value = '';
  document.getElementById('day').value = '';
  document.getElementById('start').value = '00:00';
  document.getElementById('end').value = '24:00';
  document.getElementById('mode').value = 'all';
  clearResults();
}});
clearResults();
</script>
</body>
</html>
"""


def main() -> None:
    log("Fetching technical papers from gisbi-kim/icra2026-explorer...")
    repo = ensure_explorer_repo()
    github_papers = load_github_papers(repo)
    log(f"Loaded {len(github_papers)} GitHub explorer papers as fallback.")

    log("Fetching and parsing current PaperCept technical program...")
    papers = parse_papercept_current(github_papers)
    log(f"Parsed {len(papers)} current PaperCept technical papers.")

    log("Fetching official ICRA workshop/tutorial table...")
    workshops = load_workshop_table()
    log(f"Loaded {len(workshops)} workshops/tutorials.")

    log("Testing RAS events access...")
    rasevents = try_rasevents()
    log(f"RAS events blocked={rasevents['blocked']} status={rasevents['status']}")

    log("Crawling linked workshop pages...")
    crawled = []
    for idx, ws in enumerate(workshops, start=1):
        log(f"[{idx:02d}/{len(workshops)}] {ws['day']} {ws['title'][:80]}")
        crawled.append(crawl_workshop(ws))

    workshop_presentations = make_embedded_presentations(crawled)
    workshop_slots = make_embedded_workshop_slots(crawled, workshop_presentations)
    public_workshops = make_embedded_workshops(crawled)
    conference_sessions = make_conference_sessions()
    failures = [w for w in crawled if w.get("crawlStatus") != "ok"]
    no_presentations = [w for w in crawled if w.get("crawlStatus") == "ok" and not w.get("presentations")]
    counts = Counter(
        [p["type"] for p in papers]
        + [s["type"] for s in conference_sessions]
        + [w["type"] for w in public_workshops]
        + [p["type"] for p in workshop_presentations]
        + [s["type"] for s in workshop_slots]
    )

    dataset = {
        "meta": {
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "sources": {
                "githubExplorer": EXPLORER_REPO,
                "papercept": PAPERCEPT_BASE,
                "workshops": WORKSHOPS_URL,
                "programAtGlance": PROGRAM_AT_A_GLANCE_URL,
                "conferenceSessions": [
                    KEYNOTE_SESSIONS_URL,
                    PLENARY_SESSIONS_URL,
                    KEYNOTE_TUTORIALS_URL,
                    PANEL_SESSIONS_URL,
                    INDUSTRY_KEYNOTES_URL,
                    RAS_EVENTS_URL,
                ],
                "rasevents": rasevents,
            },
            "counts": dict(counts),
            "failedWorkshopCrawls": len(failures),
            "workshopsWithoutExtractedPresentations": len(no_presentations),
            "coverageNote": (
                f"RAS events sessions page returned {'Cloudflare challenge' if rasevents['blocked'] else 'fetchable HTML'}; "
                f"technical paper timing comes from current PaperCept day pages; GitHub explorer was used as a fallback source. "
                f"Workshop metadata comes from the official ICRA workshop table. "
                f"Linked workshop pages crawled: {len(crawled) - len(failures)}/{len(crawled)} ok; "
                f"{len(workshop_presentations)} candidate internal talks/posters/papers extracted and grouped into "
                f"{len(workshop_slots)} workshop timetable slots. "
                f"Official conference-session rows added: {len(conference_sessions)}. "
                "Search results intentionally include technical papers, conference sessions, and workshop timetable slots, not workshop container rows."
            ),
        },
        "papers": papers,
        "sessions": conference_sessions,
        "workshops": public_workshops,
        "workshopPresentations": workshop_presentations,
        "workshopSlots": workshop_slots,
    }

    (ROOT / "icra2026_schedule_data.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "index.html").write_text(build_html(dataset), encoding="utf-8")

    log("Wrote index.html and icra2026_schedule_data.json")
    log(f"Workshop crawl failures: {len(failures)}")
    for w in failures[:20]:
        log(f"  - {w['title']} :: {w.get('crawlStatus')} :: {w.get('url')}")
    log(f"Workshops with no extracted internal presentation candidates: {len(no_presentations)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
