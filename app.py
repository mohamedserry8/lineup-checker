# -*- coding: utf-8 -*-
"""
Lineup Checker -- مقارنة تشكيلة السيستم الداخلي بتشكيلة Flashscore
=================================================================
"""

import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import streamlit as st

try:
    from rapidfuzz import fuzz
except ImportError:
    from fuzzywuzzy import fuzz

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

import requests

try:
    import cloudscraper
    HAVE_SCRAPER = True
except ImportError:
    HAVE_SCRAPER = False


NAME_MATCH_THRESHOLD = 85

COUNTRY_ALIASES = {
    "germany": {"germany", "ger", "deutschland", "de"},
    "luxembourg": {"luxembourg", "lux", "lu"},
    "togo": {"togo", "tog", "tg"},
    "peru": {"peru", "per", "pe"},
    "croatia": {"croatia", "cro", "hrvatska", "hr"},
    "austria": {"austria", "aut", "osterreich", "at"},
    "switzerland": {"switzerland", "sui", "schweiz", "ch"},
    "netherlands": {"netherlands", "ned", "holland", "nl"},
    "england": {"england", "eng"},
    "scotland": {"scotland", "sco"},
    "wales": {"wales", "wal", "cymru"},
    "northern ireland": {"northern ireland", "nir", "n ireland"},
    "ireland": {"ireland", "irl", "republic of ireland", "eire", "roi"},
    "jamaica": {"jamaica", "jam"},
    "st kitts and nevis": {
        "st kitts and nevis", "saint kitts and nevis", "st. kitts and nevis", "skn",
    },
    "usa": {"usa", "united states", "united states of america", "us"},
    "south korea": {"south korea", "korea republic", "republic of korea", "kor"},
    "ivory coast": {"ivory coast", "cote divoire", "civ"},
    "dr congo": {"dr congo", "congo dr", "democratic republic of congo", "cod"},
}

COUNTRY_GROUPS = [
    {"united kingdom", "great britain", "gb", "uk",
     "england", "scotland", "wales", "northern ireland"},
]


# ---------------------------------------------------------------------------
# تطبيع
# ---------------------------------------------------------------------------

def strip_diacritics(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def normalize_name(name: str) -> str:
    if not name:
        return ""
    out = strip_diacritics(str(name)).lower()
    for a, b in (("ø", "o"), ("ß", "ss"), ("đ", "d"), ("ł", "l"), ("æ", "ae")):
        out = out.replace(a, b)
    out = re.sub(r"[^a-z0-9 .]", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def name_tokens(name: str):
    full, initials = [], []
    for tok in normalize_name(name).split():
        clean = tok.rstrip(".")
        if not clean:
            continue
        (initials if len(clean) == 1 else full).append(clean)
    return full, initials


def name_similarity(src_name: str, fs_name: str) -> int:
    if not src_name or not fs_name:
        return 0

    src_full, src_init = name_tokens(src_name)
    fs_full, fs_init = name_tokens(fs_name)
    base = fuzz.token_set_ratio(" ".join(src_full), " ".join(fs_full))

    remaining = list(src_full)
    ok = bool(fs_full)
    for tok in fs_full:
        hit = next((r for r in remaining if fuzz.ratio(tok, r) >= 88), None)
        if hit is None:
            ok = False
            break
        remaining.remove(hit)

    if ok:
        for ini in fs_init + src_init:
            hit = next((r for r in remaining if r.startswith(ini)), None)
            if hit:
                remaining.remove(hit)
        return max(int(base), 96)
    return int(base)


def normalize_dob(value) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw or raw in {"-", "—", "غير متوفر"}:
        return ""

    if re.fullmatch(r"\d{9,13}", raw):
        ts = int(raw)
        if ts > 10_000_000_000:
            ts //= 1000
        try:
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", raw)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


ISO_CODES = {
    "ar": "argentina", "arg": "argentina", "au": "australia", "aus": "australia",
    "at": "austria", "aut": "austria", "be": "belgium", "bel": "belgium",
    "br": "brazil", "bra": "brazil", "bg": "bulgaria", "bgr": "bulgaria",
    "ca": "canada", "can": "canada", "cl": "chile", "chl": "chile",
    "cn": "china", "chn": "china", "co": "colombia", "col": "colombia",
    "cr": "costa rica", "cri": "costa rica", "hr": "croatia", "cro": "croatia",
    "cz": "czechia", "cze": "czechia", "dk": "denmark", "den": "denmark",
    "ec": "ecuador", "ecu": "ecuador", "eg": "egypt", "egy": "egypt",
    "eng": "england", "sco": "scotland", "wal": "wales", "nir": "northern ireland",
    "fr": "france", "fra": "france", "de": "germany", "ger": "germany",
    "deu": "germany", "gh": "ghana", "gha": "ghana", "gr": "greece",
    "grc": "greece", "hu": "hungary", "hun": "hungary", "is": "iceland",
    "isl": "iceland", "ie": "ireland", "irl": "ireland", "it": "italy",
    "ita": "italy", "ci": "ivory coast", "civ": "ivory coast",
    "jm": "jamaica", "jam": "jamaica", "jp": "japan", "jpn": "japan",
    "kr": "south korea", "kor": "south korea", "lu": "luxembourg",
    "lux": "luxembourg", "mx": "mexico", "mex": "mexico", "ma": "morocco",
    "mar": "morocco", "nl": "netherlands", "ned": "netherlands",
    "nld": "netherlands", "ng": "nigeria", "nga": "nigeria", "no": "norway",
    "nor": "norway", "py": "paraguay", "pry": "paraguay", "pe": "peru",
    "per": "peru", "pl": "poland", "pol": "poland", "pt": "portugal",
    "por": "portugal", "prt": "portugal", "ro": "romania", "rou": "romania",
    "ru": "russia", "rus": "russia", "sn": "senegal", "sen": "senegal",
    "rs": "serbia", "srb": "serbia", "sk": "slovakia", "svk": "slovakia",
    "si": "slovenia", "svn": "slovenia", "za": "south africa",
    "rsa": "south africa", "es": "spain", "esp": "spain", "se": "sweden",
    "swe": "sweden", "ch": "switzerland", "sui": "switzerland",
    "tg": "togo", "tog": "togo", "tn": "tunisia", "tun": "tunisia",
    "tr": "turkey", "tur": "turkey", "ua": "ukraine", "ukr": "ukraine",
    "uy": "uruguay", "uru": "uruguay", "us": "usa", "usa": "usa",
    "ve": "venezuela", "ven": "venezuela", "cm": "cameroon", "cmr": "cameroon",
    "dz": "algeria", "alg": "algeria", "cd": "dr congo", "cod": "dr congo",
    "gn": "guinea", "gui": "guinea", "ml": "mali", "mli": "mali",
    "bf": "burkina faso", "bfa": "burkina faso", "al": "albania",
    "alb": "albania", "ba": "bosnia and herzegovina", "bih": "bosnia and herzegovina",
    "mk": "north macedonia", "mkd": "north macedonia", "me": "montenegro",
    "mne": "montenegro", "kv": "kosovo", "kos": "kosovo", "fi": "finland",
    "fin": "finland", "il": "israel", "isr": "israel", "sa": "saudi arabia",
    "ksa": "saudi arabia", "ae": "united arab emirates", "uae": "united arab emirates",
    "bo": "bolivia", "bol": "bolivia", "pa": "panama", "pan": "panama",
    "hn": "honduras", "hon": "honduras", "gt": "guatemala", "gua": "guatemala",
    "skn": "st kitts and nevis", "nz": "new zealand", "nzl": "new zealand",
}


def country_keys(value: str) -> set:
    if not value:
        return set()
    out = set()
    for part in re.split(r"[|/,;]", str(value)):
        norm = normalize_name(part).replace(".", "").strip()
        if not norm:
            continue
        if len(norm) in (2, 3) and norm in ISO_CODES:
            out.add(ISO_CODES[norm])
            continue
        hit = next(
            (canon for canon, al in COUNTRY_ALIASES.items() if norm in al), norm
        )
        out.add(hit)
    return out


def countries_agree(src_val: str, fs_val: str) -> str:
    a, b = country_keys(src_val), country_keys(fs_val)
    if not a or not b:
        return "➖"
    if a & b:
        return "✅"
    for group in COUNTRY_GROUPS:
        if (a & group) and (b & group):
            return "✅"
    return "❌"


# ---------------------------------------------------------------------------
# تفكيك السيستم الداخلي
# ---------------------------------------------------------------------------

INTERNAL_ROW = re.compile(
    r"^(\d{1,3})\s+(\d{3,})\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s*(.*)$"
)

def parse_internal(text: str):
    players, section = [], "Started"

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()

        if low.startswith("bench"):
            section = "Bench"
            continue
        if low.startswith(("started", "starting")):
            section = "Started"
            continue
        if "remove from lineup" in low or low.startswith("id "):
            continue

        m = INTERNAL_ROW.match(line)
        if m:
            number, internal_id, name, dob, nat = m.groups()
        else:
            parts = [p.strip() for p in re.split(r"\t+|\s{2,}", line) if p.strip()]
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            number, internal_id, name, dob = parts[:4]
            nat = parts[4] if len(parts) > 4 else ""

        players.append({
            "number": int(number),
            "internal_id": internal_id,
            "name": name.strip(),
            "dob": normalize_dob(dob),
            "nationality": nat.strip(),
            "type": "Started" if section == "Started" else "Bench",
        })
    return players


# ---------------------------------------------------------------------------
# تفكيك نص Flashscore / zerozero
# ---------------------------------------------------------------------------

MARKER_RE = re.compile(r"\(\s*(?:G|C|GK|VC)\s*\)", re.I)

SECTION_WORDS = (
    "substitutes", "subs", "bench", "starting", "lineup", "formation",
    "coach", "manager", "missing players", "injuries", "suspended",
)


def parse_flashscore(text: str, want_side: str):
    players = []

    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "\t" in line:
            cols = [c.strip() for c in line.split("\t")]
            cols += [""] * (7 - len(cols))
            number, name, dob, country, side, fs_id = cols[:6]
        else:
            low = line.lower()
            if len(line) < 32 and any(w in low for w in SECTION_WORDS):
                continue

            cleaned = MARKER_RE.sub(" ", line).strip()
            if not cleaned or not re.search(r"[A-Za-zÀ-ÿ]", cleaned):
                continue

            m = re.match(r"^(\d{1,3})\s*(.+)$", cleaned)
            if m:
                number, rest = m.group(1), m.group(2).strip()
            else:
                number, rest = "", cleaned

            dob_m = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", rest)
            dob = dob_m.group(0) if dob_m else ""
            if dob:
                rest = rest.replace(dob, " ")

            name, country, side, fs_id = rest.strip(), "", "", ""

        name = re.sub(r"\s+", " ", name).strip(" -–—\t")
        if not name or len(name) < 2:
            continue

        side = (side or "").strip().upper()
        if want_side != "ANY" and side in ("HOME", "AWAY") and side != want_side:
            continue

        players.append({
            "shirt": int(number) if str(number).strip().isdigit() else None,
            "name": name,
            "dob": normalize_dob(dob),
            "nationality": country.strip(),
            "fs_id": fs_id.strip() or "—",
            "side": side or "—",
        })
    return players


# ---------------------------------------------------------------------------
# جلب ترانسفرماركت مباشرة من الأداة
# ---------------------------------------------------------------------------

TM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _looks_like_lineup(html: str) -> bool:
    return bool(html) and "/spieler/" in html


@st.cache_data(ttl=900, show_spinner=False)
def tm_get(url: str):
    attempts = []

    if HAVE_SCRAPER:
        try:
            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
            r = s.get(url, headers=TM_HEADERS, timeout=25)
            attempts.append(("cloudscraper", r.text, r.status_code))
        except Exception as exc:
            attempts.append(("cloudscraper", "", f"Error: {exc}"))

    try:
        r = requests.get(url, headers=TM_HEADERS, timeout=25,
                         allow_redirects=True)
        attempts.append(("requests", r.text, r.status_code))
    except Exception as exc:
        attempts.append(("requests", "", f"Error: {exc}"))

    for name, html, status in attempts:
        if _looks_like_lineup(html):
            return html, 200, None

    if not attempts:
        return "", None, "No fetch library available"

    best = max(attempts, key=lambda a: len(a[1] or ""))
    name, html, status = best
    detail = "; ".join(
        f"{n}: code={s} len={len(h or '')}" for n, h, s in attempts
    )
    return html, status, (
        f"All attempts returned a page without players ({detail}). "
        "Likely a Cloudflare challenge."
    )


def tm_match_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith("http"):
        if re.match(r"(www\.)?transfermarkt\.", raw, re.I):
            raw = "https://" + raw
        else:
            raw = "https://www.transfermarkt.com/" + raw.lstrip("/")

    p = urlparse(raw)
    m = re.search(r"/spielbericht/(?:index/spielbericht/)?(\d+)", p.path)
    if not m:
        m = re.search(r"/(\d{4,})(?:/|$)", p.path)
    if not m:
        return raw

    mid = m.group(1)
    slug = p.path.lstrip("/").split("/")[0] or "spielbericht"
    return f"{p.scheme}://{p.netloc}/{slug}/aufstellung/spielbericht/{mid}"


def tm_parse_lineup(html: str, base_url: str):
    if not HAVE_BS4:
        return [], "beautifulsoup4 library not installed"

    soup = BeautifulSoup(html, "html.parser")

    slug = urlparse(base_url).path.lstrip("/").split("/")[0]
    home_slug, _, away_slug = slug.partition("_")

    by_id = {}
    for a in soup.select('a[href*="/spieler/"]'):
        href = a.get("href") or ""
        m = re.search(r"/spieler/(\d+)", href)
        if not m:
            continue
        pid = m.group(1)

        name = (a.get("title") or a.get_text() or "").strip()
        if not name:
            img = a.find("img")
            if img:
                name = (img.get("title") or img.get("alt") or "").strip()
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) < 2:
            continue

        club = ""
        node = a
        for _ in range(8):
            node = node.parent
            if node is None:
                break
            link = node.find("a", href=re.compile(r"/startseite/verein/"))
            if link:
                club = (link.get("href") or "").lstrip("/").split("/")[0]
                break

        countries, row = [], a
        for _ in range(6):
            row = row.parent
            if row is None:
                break
            flags = row.find_all("img", class_=re.compile(r"flagge"))
            for f in flags:
                t = (f.get("title") or "").strip()
                if t and t not in countries:
                    countries.append(t)
            if countries:
                break

        age, shirt = "", None
        row = a
        for _ in range(6):
            row = row.parent
            if row is None:
                break
            txt = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
            if not age:
                am = re.search(r"\((\d{1,2})\s*(?:years old|Jahre)", txt, re.I)
                if am:
                    age = am.group(1)
            if shirt is None:
                nm = re.search(r"(?:^|\s)(\d{1,2})(?:\s|$)", txt.replace(name, " "))
                if nm:
                    shirt = int(nm.group(1))
            if age and shirt is not None:
                break
            if len(txt) > 600:
                break

        prev = by_id.get(pid)
        if prev and len(prev["name"]) >= len(name):
            continue

        side = "HOME" if club and club == home_slug else (
            "AWAY" if club and club == away_slug else "UNKNOWN"
        )

        by_id[pid] = {
            "tm_id": pid,
            "name": name,
            "shirt": shirt,
            "side": side,
            "nationality": "|".join(countries),
            "age": age,
            "dob": "",
            "profile": urljoin(
                base_url, f"/{href.lstrip('/').split('/')[0]}/profil/spieler/{pid}"
            ),
        }

    players = list(by_id.values())
    note = ""
    if players and all(p["side"] == "UNKNOWN" for p in players):
        note = "Could not determine teams from club links"
    return players, note


DOB_PATTERNS = [
    re.compile(
        r"(?:Date of birth|Geburtsdatum)[^:]*:?\s*<[^>]*>\s*(?:<[^>]*>\s*)?"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", re.I),
    re.compile(
        r"(?:Date of birth|Geburtsdatum)[\s\S]{0,250}?(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
        re.I),
    re.compile(r"waspassiertheute/aktuell/new/datum/(\d{4}-\d{2}-\d{2})", re.I),
    re.compile(r'itemprop=["\']birthDate["\'][^>]*content=["\']([^"\']{6,30})', re.I),
    re.compile(r'itemprop=["\']birthDate["\'][^>]*>\s*([^<]{6,30})<', re.I),
    re.compile(r'"birthDate"\s*:\s*"([^"]{6,30})"', re.I),
]


@st.cache_data(ttl=86400, show_spinner=False)
def tm_profile(url: str):
    html, status, err = tm_get(url)
    if err:
        return "", "", f"Failed: {err}"
    if status != 200:
        return "", "", f"HTTP {status}"

    dob = ""
    for pat in DOB_PATTERNS:
        m = pat.search(html)
        if m:
            dob = normalize_dob(m.group(1))
            if dob:
                break

    ctry = ""
    cm = re.search(r"Citizenship|Staatsb", html, re.I)
    if cm:
        chunk = html[cm.start(): cm.start() + 500]
        names = [
            t for t in re.findall(r'title=["\']([A-Z][A-Za-z .\'&-]{2,30})["\']', chunk)
            if not re.search(r"transfermarkt|imago|logo", t, re.I)
        ]
        seen, uniq = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        ctry = "|".join(uniq[:3])

    return dob, ctry, "ok" if dob else "No DOB found in profile"


def tm_load(match_url: str, want_dob: bool, progress=None):
    msgs = []
    url = tm_match_url(match_url)
    if not url:
        return [], ["URL is empty"]

    msgs.append(f"Fetching: {url}")
    html, status, err = tm_get(url)

    if err:
        msgs.append(f"❌ {err}")
        if html:
            head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[:1200]))
            msgs.append("Response sample: " + head[:300])
        return [], msgs
    if status and status >= 400:
        return [], msgs + [f"❌ Page returned status {status}"]

    raw, note = tm_parse_lineup(html, url)
    if note:
        msgs.append("⚠️ " + note)
    if not raw:
        return [], msgs + [
            "❌ No players found on page. Make sure the link points to a finished "
            "match with a published lineup."
        ]

    msgs.append(f"✅ {len(raw)} players read from lineup page")

    if want_dob:
        for i, p in enumerate(raw):
            dob, ctry, _note = tm_profile(p["profile"])
            p["dob"] = dob
            if not p["nationality"]:
                p["nationality"] = ctry
            if progress:
                progress((i + 1) / len(raw),
                         f"Fetching DOBs {i + 1}/{len(raw)}")
            time.sleep(0.25)

            if i == 3 and not any(x["dob"] for x in raw[:4]):
                msgs.append(
                    f"⚠️ First 4 profiles returned no DOB — stopped. ({_note})"
                )
                break

        ok = sum(1 for p in raw if p["dob"])
        msgs.append(f"{'✅' if ok else '⚠️'} {ok} of {len(raw)} with DOB")

    players = [{
        "shirt": p["shirt"],
        "name": p["name"],
        "dob": p["dob"],
        "nationality": p["nationality"],
        "fs_id": p["tm_id"],
        "side": p["side"],
    } for p in raw]

    return players, msgs


# ---------------------------------------------------------------------------
# معلومات الماتش من رأس النص الملزوق
# ---------------------------------------------------------------------------

def parse_meta(text: str) -> dict:
    meta = {}
    for line in text.strip().splitlines()[:12]:
        line = line.strip()
        if not line.startswith("#") or "=" not in line:
            continue
        key, _, val = line[1:].partition("=")
        key = key.strip().lower()
        if key:
            meta[key] = val.strip()
    return meta


# ---------------------------------------------------------------------------
# تسجيل المراجعات في جوجل شيت
# ---------------------------------------------------------------------------

BLOCK_ON_LOG_FAILURE = False

SHEET_HEADER = [
    "Timestamp", "Email",
    "Match ID (Gatekeeper)", "Match Name (Gatekeeper)",
    "Match ID (Source)", "Match Name (Source)",
    "Match Link", "Team",
    "Gatekeeper Players", "Online Players", "Exact Match", "Needs Review",
    "Missing at Source", "Missing in Gatekeeper", "Diff Details", "Source",
]

PLAYERS_HEADER = [
    "Timestamp", "Email",
    "Match ID (Gatekeeper)", "Match Name (Gatekeeper)",
    "Match ID (Source)", "Match Name (Source)",
    "Source", "Team",
    # Gatekeeper side
    "Internal ID", "Gatekeeper Shirt", "Gatekeeper Name",
    "Gatekeeper DOB", "Gatekeeper Nationality",
    # Online source side
    "Source Player ID", "Online Source Shirt", "Online Source Name",
    "Online Source DOB", "Online Source Nationality",
    # Match quality
    "Match Method", "Name Similarity %",
    "Shirt Match", "DOB Match", "Nationality Match",
    "Status",
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def now_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@st.cache_resource(show_spinner=False)
def _sheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None, "gspread / google-auth libraries missing in requirements.txt"

    try:
        if "gcp_service_account" not in st.secrets:
            return None, "No gcp_service_account in secrets settings"
        if "sheet_id" not in st.secrets:
            return None, "No sheet_id in secrets settings"

        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
            ],
        )
        client = gspread.authorize(creds)
        book = client.open_by_key(st.secrets["sheet_id"])

        tab_name = st.secrets.get("sheet_tab", "log")
        try:
            ws = book.worksheet(tab_name)
        except Exception:
            ws = book.add_worksheet(title=tab_name, rows=2000,
                                    cols=len(SHEET_HEADER))

        try:
            first = ws.row_values(1)
            if not first:
                ws.update("A1", [SHEET_HEADER])
            elif first != SHEET_HEADER:
                return ws, (
                    f"⚠️ Sheet headers are outdated ({len(first)} columns instead of "
                    f"{len(SHEET_HEADER)}). Logging is active but columns may be misaligned. "
                    "Fix the first row in the sheet or create a new empty tab."
                )
        except Exception:
            pass

        return ws, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def log_review(row: list):
    ws, err = _sheet()
    if ws is None:
        return False, err
    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True, err
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


@st.cache_resource(show_spinner=False)
def _players_sheet():
    """Return the 'players' tab worksheet (creates it if missing)."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None, "gspread / google-auth libraries missing"

    try:
        if "gcp_service_account" not in st.secrets or "sheet_id" not in st.secrets:
            return None, "Missing secrets (gcp_service_account or sheet_id)"

        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
            ],
        )
        client = gspread.authorize(creds)
        book = client.open_by_key(st.secrets["sheet_id"])

        # Tab name: configurable via secret, default "players"
        tab_name = st.secrets.get("players_tab", "players")
        try:
            ws = book.worksheet(tab_name)
        except Exception:
            ws = book.add_worksheet(title=tab_name, rows=5000,
                                    cols=len(PLAYERS_HEADER))

        try:
            first = ws.row_values(1)
            if not first:
                ws.update("A1", [PLAYERS_HEADER])
            elif first != PLAYERS_HEADER:
                return ws, (
                    f"⚠️ Players tab headers are outdated ({len(first)} cols vs "
                    f"{len(PLAYERS_HEADER)}). Consider resetting the tab header row."
                )
        except Exception:
            pass

        return ws, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def log_players(df: pd.DataFrame, meta: dict):
    """Append one row per player from the comparison DataFrame to the players tab."""
    ws, err = _players_sheet()
    if ws is None:
        return False, err

    ts          = meta["ts"]
    email       = meta["email"]
    gk_id       = meta["gk_id"]
    gk_name     = meta["gk_name"]
    src_id      = meta["src_id"]
    src_name    = meta["src_name"]
    source      = meta["source"]
    team        = meta["team"]
    src_label   = meta["src_label"]

    rows = []
    shirt_col   = f"{src_label} Shirt"
    src_id_col  = f"{src_label} ID"

    for _, r in df.iterrows():
        rows.append([
            ts, email,
            gk_id, gk_name,
            src_id, src_name,
            source, team,
            # Gatekeeper
            str(r.get("Internal ID", "—")),
            str(r.get("Gatekeeper Shirt", "—")),
            str(r.get("Gatekeeper Name", "—")),
            str(r.get("Gatekeeper DOB", "—")),
            str(r.get("Gatekeeper Nationality", "—")),
            # Online source — column names vary by source label
            str(r.get(src_id_col, r.get("Online Source ID", "—"))),
            str(r.get(shirt_col, r.get("Online Source Shirt", "—"))),
            str(r.get("Online Source Name", "—")),
            str(r.get("Online Source DOB", "—")),
            str(r.get("Online Source Nationality", "—")),
            # Match quality
            str(r.get("Match Method", "—")),
            str(r.get("Name Similarity %", "—")),
            str(r.get("Shirt Match", "—")),
            str(r.get("DOB Match", "—")),
            str(r.get("Nationality Match", "—")),
            str(r.get("Status", "—")),
        ])

    if not rows:
        return True, None

    try:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return True, err
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def mismatch_details(df: pd.DataFrame) -> str:
    out = []
    for _, r in df.iterrows():
        state = str(r["Status"])
        if state.startswith("✅"):
            continue

        name = r["Gatekeeper Name"]
        if "— Not Found —" in str(name):
            out.append(f"{r['Online Source Name']}: in source only")
            continue
        if "— Not Found —" in str(r["Online Source Name"]):
            out.append(f"{name}: in gatekeeper only")
            continue

        bits = []
        if r["Shirt Match"] == "❌":
            bits.append(f"shirt {r['Gatekeeper Shirt']}≠{r['Online Source Shirt']}")
        if r["DOB Match"] == "❌":
            bits.append(f"dob {r['Gatekeeper DOB']}≠{r['Online Source DOB']}")
        if r["Nationality Match"] == "❌":
            bits.append(f"nat {r['Gatekeeper Nationality']}≠{r['Online Source Nationality']}")
        if int(r["Name Similarity %"]) < NAME_MATCH_THRESHOLD:
            bits.append(f"name {name}≠{r['Online Source Name']}")
        if "Shirt only" in str(r["Match Method"]):
            bits.append("matched by shirt only")

        out.append(f"{name}: " + (", ".join(bits) if bits else state))

    return " | ".join(out) if out else "No differences"


# ---------------------------------------------------------------------------
# المطابقة
# ---------------------------------------------------------------------------

def match_squads(source, flashscore):
    pairs = []
    src_left, fs_left = list(source), list(flashscore)

    # 1) تاريخ الميلاد
    for src in list(src_left):
        if not src["dob"]:
            continue
        cands = [f for f in fs_left if f["dob"] and f["dob"] == src["dob"]]
        if not cands:
            continue
        if len(cands) > 1:
            cands.sort(
                key=lambda f: name_similarity(src["name"], f["name"]),
                reverse=True,
            )
        best = cands[0]
        pairs.append((src, best, "Date of Birth", 100))
        src_left.remove(src)
        fs_left.remove(best)

    # 2) الاسم
    for src in list(src_left):
        scored = sorted(
            ((name_similarity(src["name"], f["name"]), f) for f in fs_left),
            key=lambda x: x[0],
            reverse=True,
        )
        if scored and scored[0][0] >= NAME_MATCH_THRESHOLD:
            score, best = scored[0]
            pairs.append((src, best, "Name", score))
            src_left.remove(src)
            fs_left.remove(best)

    # 3) رقم القميص لوحده -- ضعيف
    for src in list(src_left):
        best = next((f for f in fs_left if f["shirt"] == src["number"]), None)
        if best is not None:
            pairs.append((src, best, "Shirt only ⚠", 40))
            src_left.remove(src)
            fs_left.remove(best)

    return pairs, src_left, fs_left


def build_report(pairs, only_source, only_fs, source_label="Online Source"):
    rows = []

    for src, fs, method, _conf in pairs:
        num_ok = fs["shirt"] is not None and fs["shirt"] == src["number"]
        score = name_similarity(src["name"], fs["name"])
        name_ok = score >= NAME_MATCH_THRESHOLD

        if src["dob"] and fs["dob"]:
            dob_state = "✅" if src["dob"] == fs["dob"] else "❌"
        else:
            dob_state = "➖"

        nat_state = countries_agree(src["nationality"], fs["nationality"])

        matched_by_strong_key = not method.startswith("Shirt only")

        if method.startswith("Shirt only"):
            status = "⚠️ Weak match — review manually"
        elif dob_state == "❌":
            status = "⚠️ Partial match (data mismatch)"
        elif nat_state == "❌":
            status = "⚠️ Partial match (data mismatch)"
        elif not name_ok:
            status = "⚠️ Partial match (data mismatch)"
        elif not num_ok and matched_by_strong_key:
            status = "🔢 Shirt differs in source"
        else:
            status = "✅ Exact Match"

        rows.append({
            "Gatekeeper Shirt": src["number"],
            f"{source_label} Shirt": fs["shirt"] if fs["shirt"] is not None else "—",
            "Shirt Match": "✅" if num_ok else ("🔢" if matched_by_strong_key else "❌"),
            "Gatekeeper Name": src["name"],
            "Online Source Name": fs["name"],
            "Name Similarity %": score,
            "Gatekeeper DOB": src["dob"] or "—",
            "Online Source DOB": fs["dob"] or "—",
            "DOB Match": dob_state,
            "Gatekeeper Nationality": src["nationality"] or "—",
            "Online Source Nationality": fs["nationality"] or "—",
            "Nationality Match": nat_state,
            "Internal ID": src["internal_id"],
            f"{source_label} ID": fs["fs_id"],
            "Match Method": method,
            "Status": status,
        })

    blank = {k: "—" for k in (
        "Gatekeeper Shirt", f"{source_label} Shirt", "Gatekeeper Name", "Online Source Name",
        "Gatekeeper DOB", "Online Source DOB", "Gatekeeper Nationality",
        "Online Source Nationality", "Internal ID", f"{source_label} ID",
    )}

    for src in only_source:
        rows.append({**blank,
            "Gatekeeper Shirt": src["number"],
            "Shirt Match": "❌",
            "Gatekeeper Name": src["name"],
            "Online Source Name": "— Not Found —",
            "Name Similarity %": 0,
            "Gatekeeper DOB": src["dob"] or "—",
            "DOB Match": "❌",
            "Gatekeeper Nationality": src["nationality"] or "—",
            "Nationality Match": "❌",
            "Internal ID": src["internal_id"],
            "Match Method": "—",
            "Status": "❌ In Gatekeeper, not in source",
        })

    for fs in only_fs:
        rows.append({**blank,
            f"{source_label} Shirt": fs["shirt"] if fs["shirt"] is not None else "—",
            "Shirt Match": "❌",
            "Gatekeeper Name": "— Not Found —",
            "Online Source Name": fs["name"],
            "Name Similarity %": 0,
            "Online Source DOB": fs["dob"] or "—",
            "DOB Match": "❌",
            "Online Source Nationality": fs["nationality"] or "—",
            "Nationality Match": "❌",
            f"{source_label} ID": fs["fs_id"],
            "Match Method": "—",
            "Status": "❌ In source, not in Gatekeeper",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# الواجهة
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Lineup Comparison", page_icon="⚽", layout="wide")
st.title("⚽ Lineup Comparison against online sources")
st.caption(
    "Matching runs on Date of Birth first, then normalised name, "
    "then shirt number as a last resort."
)

with st.expander("📋 How to use", expanded=False):
    st.markdown(
        """
### One-time setup per device

Install one of the following (first option is recommended):

- **Tampermonkey extension** + the `transfermarkt-userscript.user.js` userscript.
  A green button will appear on the Transfermarkt lineup page.
- **Bookmarklet** in your browser favourites bar — instructions in
  `transfermarkt-bookmarklet.md`. No extension required, but may be blocked
  on some pages.

### Every time

1. Open the match page on Transfermarkt, **LINE-UPS** tab.
2. Click the green button (or bookmarklet) → wait → **Copy**.
3. Paste here into the right-hand box.
4. Paste your Gatekeeper table into the left-hand box.
5. Choose your team (**HOME** / **AWAY**) — important, as shirt numbers repeat across teams.
6. Click **Start Comparison**.

### Why not one click from here?

Transfermarkt blocks server-side requests (AWS WAF), so the tool cannot fetch
the page itself. Extraction must happen in your browser, hence the copy-paste step.

### How matching works

Date of Birth first (strongest key), then normalised name
(handles abbreviations, strips diacritics), then shirt number as a last resort.
Any shirt-only match is flagged ⚠️ as unreliable.

**Note:** 🔢 Shirt differs in source = the player was identified by name or DOB
but the source has a wrong shirt number. This is not an identity error.
        """
    )

st.divider()

_ws, _log_err = _sheet()
email = st.text_input(
    "📧 Your email (required — every review is logged under your name)",
    value=st.session_state.get("reviewer_email", ""),
    placeholder="name@company.com",
)
if email:
    st.session_state["reviewer_email"] = email.strip()

if _ws is None and _log_err:
    st.warning(
        f"⚠️ Google Sheet logging not active: {_log_err}\n\n"
        "Comparison will work normally but results won't be logged. "
        "See SETUP-GOOGLE-SHEET.md for details."
    )
elif _log_err:
    st.warning(_log_err)

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Add Gatekeeper Lineup")
    internal_text = st.text_area(
        "Paste the table:",
        height=300,
        placeholder="30 1001243 Noah Kruth 2003-06-24 Germany\n"
                    "4 124245 Eldin Dzogovic 2003-06-08 Luxembourg\n"
                    "Bench\n"
                    "15 122681 Daniel Heber 1994-07-04 Germany",
    )

with col2:
    st.subheader("2. Online Source")

    fs_text = st.text_area(
        "Paste lineup:",
        height=300,
        placeholder="1\tMark Oxley\t1990-09-28\tEngland\tHOME\n"
                    "24\tLewis Cass\t2000-02-27\tEngland\tHOME",
    )
    use_url = False
    want_dob = False

    side_choice = st.radio(
        "Team to compare:",
        ["HOME (صاحب الأرض)", "AWAY (الضيف)", "ANY (الكل)"],
        horizontal=True,
        help="Shirt numbers repeat across teams, so pick your team. "
             "ANY works only if the pasted text contains a single team.",
    )
    ignore_extras = st.checkbox(
        "Extra players in source = other team (don't count as missing)",
        value=False,
        help="Check this only if you pasted both teams and chose ANY. "
             "If you filtered by HOME or AWAY, leave it unchecked — "
             "otherwise missing players won't show.",
    )

st.divider()

_meta = parse_meta(fs_text) if fs_text else {}

_auto_id = _meta.get("match_id", "")
_auto_name = _meta.get("match_name", "")
_auto_link = _meta.get("match_url", "")
_auto_source = _meta.get("source", "transfermarkt")

_src_label = {
    "transfermarkt": "Transfermarkt",
    "sofascore": "SofaScore",
    "zerozero": "ZeroZero",
    "soccerway": "Soccerway",
    "espn": "ESPN",
    "tribuna": "Tribuna",
    "ligafemenil": "Liga Femenil",
    "flashscore": "Flashscore",
}.get(_auto_source, _auto_source or "Source")

st.subheader("🆔 Match Details")
st.caption(
    "Left side from your system (you fill in), right side from the online source "
    "(auto-filled from the pasted text). Both are logged to the sheet."
)

sc, tc = st.columns(2)

with sc:
    st.markdown("**From your Gatekeeper system**")
    src_match_id = st.text_input(
        "Match ID (Gatekeeper)",
        placeholder="e.g. 1884213",
        help="The ID you use to look up the match in your database.",
    ).strip()
    src_match_name = st.text_input(
        "Match Name (Gatekeeper)",
        placeholder="e.g. Harrogate Town - Solihull Moors",
    ).strip()

with tc:
    st.markdown(f"**From {_src_label}**")
    match_id = st.text_input(
        f"Match ID ({_src_label})",
        value=_auto_id,
        help="Auto-filled from the pasted text.",
    ).strip()
    match_name = st.text_input(
        f"Match Name ({_src_label})",
        value=_auto_name,
        placeholder="Harrogate Town vs Solihull Moors",
    ).strip()

if fs_text and not _auto_id:
    st.caption(
        "ℹ️ The pasted text has no match metadata — either you copied it manually, "
        "or your userscript version is outdated. Fill in the details manually or "
        "update the userscript."
    )

st.divider()

if st.button("🚀 Start Comparison", type="primary", use_container_width=True):
    email = (st.session_state.get("reviewer_email") or "").strip()
    if not email:
        st.error("❌ Please enter your email first — every review is logged under your name.")
        st.stop()
    if not EMAIL_RE.match(email):
        st.error(f"❌ Email `{email}` doesn't look right. Use the format name@company.com")
        st.stop()
    if not src_match_id:
        st.error("❌ Match ID from your system is required — it links the review to the match.")
        st.stop()
    if not src_match_name:
        st.error("❌ Match name from your system is required.")
        st.stop()
    if not match_id:
        st.error(
            f"❌ Match ID from {_src_label} is required. "
            "If the pasted text doesn't include it, take it from the match URL."
        )
        st.stop()

    if not internal_text.strip():
        st.warning("⚠️ Please paste your Gatekeeper lineup first.")
        st.stop()
    if not fs_text.strip():
        st.warning("⚠️ Please paste the lineup in the right-hand box.")
        st.stop()

    source_players = parse_internal(internal_text)
    if not source_players:
        st.error(
            "❌ Could not parse the Gatekeeper text. Each line must be: "
            "shirt, ID, name, DOB (YYYY-MM-DD), nationality."
        )
        st.stop()

    want_side = side_choice.split()[0]

    fs_players = parse_flashscore(fs_text, want_side)

    match_link = _auto_link
    source_name = _auto_source

    if not fs_players:
        st.error(
            "❌ No players to compare. If you filtered by team, try ANY."
        )
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("Gatekeeper Players", len(source_players))
    c2.metric("Online Players", len(fs_players))
    c3.metric("With DOB", sum(1 for p in fs_players if p["dob"]))

    if not any(p["dob"] for p in fs_players):
        st.info(
            "ℹ️ No DOBs found in the pasted text — matching will run on names "
            "and shirt numbers. The DOB Match column will show ➖."
        )

    pairs, only_src, only_fs = match_squads(source_players, fs_players)

    has_side_info = any(p.get("side") in ("HOME", "AWAY") for p in fs_players)
    filtered_by_side = want_side in ("HOME", "AWAY")
    extras_are_other_team = ignore_extras and not (filtered_by_side and has_side_info)

    if ignore_extras and filtered_by_side and has_side_info:
        st.info(
            "ℹ️ Ignored the 'other team' option because you filtered by team and "
            "the text contains team data — any extra player in the source is a real gap."
        )

    # Determine source label for column headers
    source_col_label = _src_label if _src_label else "Online Source"

    df = build_report(pairs, only_src, [] if extras_are_other_team else only_fs, source_label=source_col_label)

    full = sum(1 for r in df["Status"] if r.startswith("✅"))
    wrong_num = sum(1 for r in df["Status"] if r.startswith("🔢"))
    review = sum(1 for r in df["Status"] if r.startswith("⚠️"))
    n_missing_src = len(only_src)
    n_missing_fs = 0 if extras_are_other_team else len(only_fs)

    st.subheader("📊 Result")

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("✅ Exact Match", full)
    k2.metric("🔢 Shirt Differs in Source", wrong_num,
              help="Player identified by name/DOB but source has wrong shirt — not an identity error")
    k3.metric("⚠️ Needs Review", review)
    k4.metric("🚫 Missing at Source", n_missing_src,
              help="Players in your Gatekeeper with no match in the source")
    k5.metric("🚫 Missing in Gatekeeper", n_missing_fs,
              help="Players in the source not found in your Gatekeeper")

    if n_missing_src or n_missing_fs:
        st.warning(
            f"⚠️ {n_missing_src + n_missing_fs} player(s) have no match. "
            "See the 'Missing Players' section below."
        )

    def color_status(val):
        text = str(val)
        if "✅" in text:
            return "background-color: #1e4620; color: white;"
        if "🔢" in text:
            return "background-color: #1a3a5c; color: #aad4f5;"
        if "⚠️" in text:
            return "background-color: #856404; color: white;"
        return "background-color: #721c24; color: white;"

    st.dataframe(
        df.style.map(color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    # --- Missing Players ---
    st.divider()
    st.subheader("🚫 Missing Players")

    ms1, ms2 = st.columns(2)

    with ms1:
        st.markdown(f"**Missing at Source ({n_missing_src})**")
        st.caption("In your Gatekeeper but no match found in the source")
        if only_src:
            st.dataframe(
                pd.DataFrame([{
                    "Shirt": p["number"],
                    "Name": p["name"],
                    "DOB": p["dob"] or "—",
                    "Nationality": p["nationality"] or "—",
                    "Internal ID": p["internal_id"],
                    "Type": p["type"],
                } for p in only_src]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.success("All clear — every Gatekeeper player was found in the source ✓")

    with ms2:
        st.markdown(f"**Missing in Gatekeeper ({n_missing_fs})**")
        st.caption("In the source but not found in your Gatekeeper")
        if only_fs and not extras_are_other_team:
            st.dataframe(
                pd.DataFrame([{
                    "Shirt": p["shirt"] if p["shirt"] is not None else "—",
                    "Name": p["name"],
                    "DOB": p["dob"] or "—",
                    "Nationality": p["nationality"] or "—",
                    "Source ID": p["fs_id"],
                    "Team": p["side"],
                } for p in only_fs]),
                use_container_width=True, hide_index=True,
            )
        elif only_fs and extras_are_other_team:
            st.info(
                f"{len(only_fs)} extra player(s) in the source, treated as the other team. "
                "See the collapsed section below."
            )
        else:
            st.success("All clear — every source player is in your Gatekeeper ✓")

    st.divider()

    st.download_button(
        "⬇️ Download CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        "lineup_check.csv",
        "text/csv",
    )

    # --- Log to Google Sheet ---
    details = mismatch_details(df)

    ok_log, log_err = log_review([
        now_str(),
        email,
        src_match_id,
        src_match_name,
        match_id,
        match_name or "—",
        match_link or "—",
        want_side,
        len(source_players),
        len(fs_players),
        full,
        review,
        len(only_src),
        n_missing_fs,
        details[:4000],
        source_name,
    ])

    # --- Log player-level rows to the players tab ---
    ok_players, players_err = log_players(df, {
        "ts":        now_str(),
        "email":     email,
        "gk_id":     src_match_id,
        "gk_name":   src_match_name,
        "src_id":    match_id,
        "src_name":  match_name or "—",
        "source":    source_name,
        "team":      want_side,
        "src_label": source_col_label,
    })

    if ok_log:
        players_note = (
            f" · {len(df)} player rows → '{st.secrets.get('players_tab', 'players')}' tab"
            if ok_players else " · ⚠️ player rows not saved"
        )
        st.success(
            f"📝 Logged to Google Sheet — {src_match_name} "
            f"(Gatekeeper {src_match_id} / {_src_label} {match_id}) "
            f"by {email}{players_note}"
        )
        if log_err:
            st.warning(log_err)
        if players_err and not ok_players:
            st.warning(f"⚠️ Match log saved but player rows failed: {players_err}")
    else:
        st.error(
            f"⚠️ Results displayed but **logging failed**: {log_err}\n\n"
            "Please notify the tool owner. If logging is required for audit, "
            "save the CSV as a backup."
        )
        if BLOCK_ON_LOG_FAILURE:
            st.stop()

    if extras_are_other_team and only_fs:
        with st.expander(
            f"👥 {len(only_fs)} extra player(s) in source (treated as other team)"
        ):
            st.caption(
                "Quickly scan this list: if you spot a player who should be in your team, "
                "there may be a real issue."
            )
            st.dataframe(
                pd.DataFrame([
                    {
                        "Shirt": p["shirt"] if p["shirt"] is not None else "—",
                        "Name": p["name"],
                        "DOB": p["dob"] or "—",
                        "Team": p["side"],
                    }
                    for p in only_fs
                ]),
                use_container_width=True, hide_index=True,
            )
