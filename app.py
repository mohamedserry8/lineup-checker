# -*- coding: utf-8 -*-
"""
Lineup Checker -- مقارنة تشكيلة السيستم الداخلي بتشكيلة Transfermarkt
=================================================================

النسخة دي مفيهاش أي اتصال بالإنترنت. بتقارن نصين ملزوقين:
  - يسار: جدول السيستم الداخلي
  - يمين: مخرج سكريبت Transfermarkt-extract.js (أو نسخ يدوي من الصفحة)

ليه؟ فييدات فلاش سكور بقت GraphQL بـ persisted queries، والهاش بتاعها
بيتغير مع كل ديبلوي، فأي سكرابينج بيفصل كل أسبوعين. اللصق مش بيفصل أبداً.

المطابقة بتمشي بالترتيب ده:
  1. تاريخ الميلاد (أقوى مفتاح)
  2. الاسم بعد التطبيع (شيل التشكيل، وفهم الاختصارات زي "Kruth N.")
  3. رقم القميص لوحده -- وبتتعلّم كمطابقة ضعيفة محتاجة مراجعة

التشغيل: streamlit run app.py
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
except ImportError:  # pragma: no cover
    from fuzzywuzzy import fuzz

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:  # pragma: no cover
    HAVE_BS4 = False

import requests

try:
    import cloudscraper
    HAVE_SCRAPER = True
except ImportError:  # pragma: no cover
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

# لو السورس مسجل "United Kingdom" وترانسفرماركت مسجل "England"،
# الاتنين مقبولين -- المجموعات دي بتعتبر بعضها متطابقة.
COUNTRY_GROUPS = [
    {"united kingdom", "great britain", "gb", "uk",
     "england", "scotland", "wales", "northern ireland"},
]


# ---------------------------------------------------------------------------
# تطبيع
# ---------------------------------------------------------------------------

def strip_diacritics(text: str) -> str:
    """Šimić -> Simic  ،  Chávez -> Chavez"""
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
    """يرجّع (الكلمات الكاملة، الحروف المختصرة)."""
    full, initials = [], []
    for tok in normalize_name(name).split():
        clean = tok.rstrip(".")
        if not clean:
            continue
        (initials if len(clean) == 1 else full).append(clean)
    return full, initials


def name_similarity(src_name: str, fs_name: str) -> int:
    """
    نسبة تشابه واعية بالاختصارات.

    فلاش سكور بيكتب "Chávez Fischer F." والسورس عندك
    "Felipe Marlon Chávez Fischer" -- المقارنة النصية العادية بتفشل،
    فبنعتبر الحرف المختصر حرف أول لاسم من أسماء السورس.
    """
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
    """أي صيغة شائعة -> YYYY-MM-DD، أو '' لو فشل."""
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


def country_keys(value: str) -> set:
    """
    يرجّع مجموعة الجنسيات المطبَّعة. اللاعب ممكن يكون له أكتر من
    جنسية، مفصولين بـ | أو / أو فاصلة.
    """
    if not value:
        return set()
    out = set()
    for part in re.split(r"[|/,;]", str(value)):
        norm = normalize_name(part).replace(".", "").strip()
        if not norm:
            continue
        hit = next(
            (canon for canon, al in COUNTRY_ALIASES.items() if norm in al), norm
        )
        out.add(hit)
    return out


def countries_agree(src_val: str, fs_val: str) -> str:
    """
    ✅ لو فيه أي جنسية مشتركة، ❌ لو مفيش، ➖ لو حد منهم فاضي.
    اللاعب بجنسيتين على ترانسفرماركت لازم يتطابق لو السورس مسجل
    واحدة منهم بس.
    """
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
            "type": "أساسي" if section == "Started" else "بديل",
        })
    return players


# ---------------------------------------------------------------------------
# تفكيك نص Flashscore
# ---------------------------------------------------------------------------

# علامات زي (G) للحارس و (C) للكابتن -- بتتشال من الاسم
MARKER_RE = re.compile(r"\(\s*(?:G|C|GK|VC)\s*\)", re.I)

# سطور عناوين الأقسام -- بتتجاهل
SECTION_WORDS = (
    "substitutes", "subs", "bench", "starting", "lineup", "formation",
    "coach", "manager", "missing players", "injuries", "suspended",
)


def parse_flashscore(text: str, want_side: str):
    """
    بيقبل تلات صيغ:

    أ) مخرج السكريبت (TSV: number, name, dob, country, side, fs_id)
    ب) نسخ مباشر من الصفحة بالرقم ملزوق في الاسم: "7Bockhorn H."
    ج) اسم بدون رقم: "Reimann D."

    want_side: "HOME" أو "AWAY" أو "ANY"
    """
    players = []

    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "\t" in line:
            cols = [c.strip() for c in line.split("\t")]
            cols += [""] * (6 - len(cols))
            number, name, dob, country, side, fs_id = cols[:6]
        else:
            low = line.lower()
            if len(line) < 32 and any(w in low for w in SECTION_WORDS):
                continue

            cleaned = MARKER_RE.sub(" ", line).strip()
            if not cleaned or not re.search(r"[A-Za-zÀ-ÿ]", cleaned):
                continue

            # الرقم ملزوق أو مفصول أو مش موجود خالص
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
    """الصفحة الحقيقية فيها لينكات لاعبين. صفحة تحدي Cloudflare مفيهاش."""
    return bool(html) and "/spieler/" in html


@st.cache_data(ttl=900, show_spinner=False)
def tm_get(url: str):
    """
    يرجّع (html, status, error).

    بيجرب مكتبتين -- بصمة الطلب بتفرق مع Cloudflare. لو الاتنين
    رجعوا صفحة تحدي، بيرجع أطول رد عشان نشخّص منه.
    """
    attempts = []

    if HAVE_SCRAPER:
        try:
            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
            r = s.get(url, headers=TM_HEADERS, timeout=25)
            attempts.append(("cloudscraper", r.text, r.status_code))
        except Exception as exc:
            attempts.append(("cloudscraper", "", f"خطأ: {exc}"))

    try:
        r = requests.get(url, headers=TM_HEADERS, timeout=25,
                         allow_redirects=True)
        attempts.append(("requests", r.text, r.status_code))
    except Exception as exc:
        attempts.append(("requests", "", f"خطأ: {exc}"))

    # أي رد فيه لينكات لاعبين = نجاح، مهما كان الكود
    for name, html, status in attempts:
        if _looks_like_lineup(html):
            return html, 200, None

    if not attempts:
        return "", None, "مفيش مكتبة جلب متاحة"

    best = max(attempts, key=lambda a: len(a[1] or ""))
    name, html, status = best
    detail = "; ".join(
        f"{n}: كود={s} طول={len(h or '')}" for n, h, s in attempts
    )
    return html, status, (
        f"كل المحاولات رجعت صفحة مش فيها لاعبين ({detail}). "
        "على الأغلب تحدي Cloudflare."
    )


def tm_match_url(raw: str) -> str:
    """
    يحوّل أي لينك ماتش لصفحة التشكيلة.
    /spielbericht/index/spielbericht/123  ->  /aufstellung/spielbericht/123
    """
    raw = raw.strip()
    if not raw:
        return ""
    if not raw.startswith("http"):
        # لينك ملزوق بدون https:// -- بدومين أو بمسار بس
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
    """
    يطلّع اللاعبين من صفحة التشكيلة.

    الشكل اللي بنعتمد عليه (متأكدين منه من الصفحة الحقيقية):
      <a title="Mark Oxley" href="/mark-oxley/leistungsdatendetails/spieler/67232/...">
      <a href="/mark-oxley/profil/spieler/67232"><img title="Mark Oxley" ...>
    فالمشترك هو /spieler/{id}. والفريق بيتحدد من لينك النادي
    (/startseite/verein/{id}) اللي في نفس الصندوق.
    """
    if not HAVE_BS4:
        return [], "مكتبة beautifulsoup4 مش متثبتة"

    soup = BeautifulSoup(html, "html.parser")

    # الفريقين من لينك الماتش: {home}_{away}
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

        # الصندوق = أقرب أب فيه لينك نادي
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

        # الجنسيات من أعلام نفس الصف
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

        # العمر ورقم القميص من نص الصف
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
        note = "معرفتش أحدد الفريقين من لينكات الأندية"
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
    """(dob, citizenship, note) من صفحة بروفايل اللاعب. بيتكاش يوم كامل."""
    html, status, err = tm_get(url)
    if err:
        return "", "", f"فشل: {err}"
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

    return dob, ctry, "ok" if dob else "مفيش تاريخ في البروفايل"


def tm_load(match_url: str, want_dob: bool, progress=None):
    """
    يرجّع (players, messages). كل لاعب بنفس شكل مخرج parse_flashscore
    عشان باقي الأداة تشتغل من غير تعديل.
    """
    msgs = []
    url = tm_match_url(match_url)
    if not url:
        return [], ["اللينك فاضي"]

    msgs.append(f"بجيب: {url}")
    html, status, err = tm_get(url)

    if err:
        msgs.append(f"❌ {err}")
        if html:
            head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[:1200]))
            msgs.append("عينة من الرد: " + head[:300])
        return [], msgs
    if status and status >= 400:
        return [], msgs + [f"❌ الصفحة رجعت كود {status}"]

    raw, note = tm_parse_lineup(html, url)
    if note:
        msgs.append("⚠️ " + note)
    if not raw:
        return [], msgs + [
            "❌ ملقيتش لاعبين في الصفحة. اتأكد إن اللينك لماتش خلص "
            "وتشكيلته منشورة."
        ]

    msgs.append(f"✅ {len(raw)} لاعب اتقروا من صفحة التشكيلة")

    if want_dob:
        for i, p in enumerate(raw):
            dob, ctry, _note = tm_profile(p["profile"])
            p["dob"] = dob
            if not p["nationality"]:
                p["nationality"] = ctry
            if progress:
                progress((i + 1) / len(raw),
                         f"تواريخ الميلاد {i + 1}/{len(raw)}")
            time.sleep(0.25)

            # لو أول 4 كلهم فشلوا، بلاش نكمل على الفاضي
            if i == 3 and not any(x["dob"] for x in raw[:4]):
                msgs.append(
                    "⚠️ أول 4 بروفايلات مجابوش تاريخ — وقفت. "
                    f"({_note})"
                )
                break

        ok = sum(1 for p in raw if p["dob"])
        msgs.append(f"{'✅' if ok else '⚠️'} {ok} من {len(raw)} بتاريخ ميلاد")

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
    """
    يقرا سطور #key=value اللي اليوزرسكريبت بيحطها في أول المخرج.
    مثال: #match_id=4940060
    """
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

# لو خليتها True، المقارنة بتتوقف لو التسجيل فشل.
# False = المقارنة تكمل بس بتحذير أحمر إن التسجيل فشل.
BLOCK_ON_LOG_FAILURE = False

SHEET_HEADER = [
    "الوقت", "الإيميل",
    "Match ID (السيستم)", "اسم الماتش (السيستم)",
    "Match ID (ترانسفرماركت)", "اسم الماتش (ترانسفرماركت)",
    "لينك الماتش", "الفريق",
    "لاعبين السيستم", "لاعبين المصدر", "تطابق كامل", "محتاج مراجعة",
    "عندنا ومش عندهم", "عندهم ومش عندنا", "تفاصيل الاختلاف", "المصدر",
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
    """
    يرجّع (worksheet, error). محتاج في st.secrets:

      sheet_id = "..."
      [gcp_service_account]
      type = "service_account"
      ... باقي مفاتيح ملف الـ JSON ...

    التفاصيل في SETUP-GOOGLE-SHEET.md
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None, "مكتبات gspread / google-auth ناقصة في requirements.txt"

    try:
        if "gcp_service_account" not in st.secrets:
            return None, "مفيش gcp_service_account في إعدادات الـ secrets"
        if "sheet_id" not in st.secrets:
            return None, "مفيش sheet_id في إعدادات الـ secrets"

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

        # نحط العناوين لو الشيت فاضية، وننبّه لو قديمة
        try:
            first = ws.row_values(1)
            if not first:
                ws.update("A1", [SHEET_HEADER])
            elif first != SHEET_HEADER:
                return ws, (
                    f"⚠️ عناوين الشيت قديمة ({len(first)} عمود بدل "
                    f"{len(SHEET_HEADER)}). التسجيل شغال بس الأعمدة "
                    "ممكن تبقى مش في مكانها. صحّح السطر الأول في الشيت "
                    "أو اعمل تاب جديد فاضي."
                )
        except Exception:
            pass

        return ws, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def log_review(row: list):
    """يرجّع (نجح؟, رسالة الخطأ)."""
    ws, err = _sheet()
    if ws is None:
        return False, err
    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True, err   # err ممكن يكون تحذير عناوين بس
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def mismatch_details(df: pd.DataFrame) -> str:
    """
    يلخّص الاختلافات في سطر واحد للتسجيل.
    مثال: "Tachie: رقم 29≠7 | Heber: جنسية Germany≠Austria"
    """
    out = []
    for _, r in df.iterrows():
        state = str(r["الحالة"])
        if state.startswith("✅"):
            continue

        name = r["اسم السورس"]
        if "— غير موجود —" in str(name):
            out.append(f"{r['اسم Flashscore']}: عندهم ومش عندنا")
            continue
        if "— غير موجود —" in str(r["اسم Flashscore"]):
            out.append(f"{name}: عندنا ومش عندهم")
            continue

        bits = []
        if r["تطابق الرقم"] == "❌":
            bits.append(f"رقم {r['رقم السورس']}≠{r['رقم Flashscore']}")
        if r["تطابق الميلاد"] == "❌":
            bits.append(f"ميلاد {r['ميلاد السورس']}≠{r['ميلاد Flashscore']}")
        if r["تطابق الجنسية"] == "❌":
            bits.append(f"جنسية {r['جنسية السورس']}≠{r['جنسية Flashscore']}")
        if int(r["تشابه الاسم %"]) < NAME_MATCH_THRESHOLD:
            bits.append(f"اسم {name}≠{r['اسم Flashscore']}")
        if "رقم القميص" in str(r["طريقة المطابقة"]):
            bits.append("اتطابق بالرقم لوحده")

        out.append(f"{name}: " + (", ".join(bits) if bits else state))

    return " | ".join(out) if out else "مفيش اختلافات"


# ---------------------------------------------------------------------------
# المطابقة
# ---------------------------------------------------------------------------

def match_squads(source, flashscore):
    """يرجّع (أزواج متطابقة، اللي عندك بس، اللي عندهم بس)."""
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
        pairs.append((src, best, "تاريخ الميلاد", 100))
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
            pairs.append((src, best, "الاسم", score))
            src_left.remove(src)
            fs_left.remove(best)

    # 3) رقم القميص لوحده -- ضعيف
    for src in list(src_left):
        best = next((f for f in fs_left if f["shirt"] == src["number"]), None)
        if best is not None:
            pairs.append((src, best, "رقم القميص فقط ⚠", 40))
            src_left.remove(src)
            fs_left.remove(best)

    return pairs, src_left, fs_left


def build_report(pairs, only_source, only_fs):
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

        if method.startswith("رقم القميص"):
            status = "⚠️ مطابقة ضعيفة — راجعه يدوي"
        elif "❌" in (dob_state, nat_state) or not name_ok or not num_ok:
            status = "⚠️ تطابق ناقص (اختلاف بيانات)"
        else:
            status = "✅ تطابق كامل"

        rows.append({
            "رقم السورس": src["number"],
            "رقم Flashscore": fs["shirt"] if fs["shirt"] is not None else "—",
            "تطابق الرقم": "✅" if num_ok else "❌",
            "اسم السورس": src["name"],
            "اسم Flashscore": fs["name"],
            "تشابه الاسم %": score,
            "ميلاد السورس": src["dob"] or "—",
            "ميلاد Flashscore": fs["dob"] or "—",
            "تطابق الميلاد": dob_state,
            "جنسية السورس": src["nationality"] or "—",
            "جنسية Flashscore": fs["nationality"] or "—",
            "تطابق الجنسية": nat_state,
            "ID داخلي": src["internal_id"],
            "ID فلاش سكور": fs["fs_id"],
            "طريقة المطابقة": method,
            "الحالة": status,
        })

    blank = {k: "—" for k in (
        "رقم السورس", "رقم Flashscore", "اسم السورس", "اسم Flashscore",
        "ميلاد السورس", "ميلاد Flashscore", "جنسية السورس",
        "جنسية Flashscore", "ID داخلي", "ID فلاش سكور",
    )}

    for src in only_source:
        rows.append({**blank,
            "رقم السورس": src["number"],
            "تطابق الرقم": "❌",
            "اسم السورس": src["name"],
            "اسم Flashscore": "— غير موجود —",
            "تشابه الاسم %": 0,
            "ميلاد السورس": src["dob"] or "—",
            "تطابق الميلاد": "❌",
            "جنسية السورس": src["nationality"] or "—",
            "تطابق الجنسية": "❌",
            "ID داخلي": src["internal_id"],
            "طريقة المطابقة": "—",
            "الحالة": "❌ عندك ومش عند Flashscore",
        })

    for fs in only_fs:
        rows.append({**blank,
            "رقم Flashscore": fs["shirt"] if fs["shirt"] is not None else "—",
            "تطابق الرقم": "❌",
            "اسم السورس": "— غير موجود —",
            "اسم Flashscore": fs["name"],
            "تشابه الاسم %": 0,
            "ميلاد Flashscore": fs["dob"] or "—",
            "تطابق الميلاد": "❌",
            "جنسية Flashscore": fs["nationality"] or "—",
            "تطابق الجنسية": "❌",
            "ID فلاش سكور": fs["fs_id"],
            "طريقة المطابقة": "—",
            "الحالة": "❌ عند Flashscore ومش عندك",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# الواجهة
# ---------------------------------------------------------------------------

st.set_page_config(page_title="مطابقة التشكيلات", page_icon="⚽", layout="wide")
st.title("⚽ مطابقة التشكيلات: السيستم الداخلي ضد Flashscore")
st.caption(
    "المطابقة بتمشي على تاريخ الميلاد أولاً، بعدين الاسم المطبَّع، "
    "وبعدين رقم القميص كآخر حل."
)

with st.expander("📋 طريقة الاستخدام", expanded=False):
    st.markdown(
        """
### مرة واحدة لكل جهاز

ثبّت واحدة من دول (الأولى أحسن):

- **إضافة Tampermonkey** + اليوزرسكريبت `transfermarkt-userscript.user.js`.
  بعد كده بيظهر زرار أخضر على صفحة التشكيلة في ترانسفرماركت.
- **بوكمارك** في شريط المفضلة — تعليماته في
  `transfermarkt-bookmarklet.md`. مفيش إضافات، بس ممكن يتمنع
  على بعض الصفحات.

### كل مرة

1. افتح صفحة الماتش على ترانسفرماركت، تاب **LINE-UPS**.
2. دوس الزرار الأخضر (أو البوكمارك) ← استنى ← **انسخ**.
3. الصق هنا في الخانة اليمين.
4. الصق جدول السيستم في الخانة الشمال.
5. اختار فريقك (**HOME** صاحب الأرض / **AWAY** الضيف) — مهمة دي،
   لأن أرقام القمصان بتتكرر بين الفريقين.
6. دوس **ابدأ المقارنة**.

### ليه مش بضغطة واحدة من هنا؟

ترانسفرماركت بيحجب الطلبات الجاية من السيرفرات (AWS WAF)، فالأداة
مش بتقدر تجيب الصفحة بنفسها. الاستخراج لازم يحصل من متصفحك، وعشان
كده فيه خطوة النسخ واللصق.

### إزاي المطابقة بتمشي

تاريخ الميلاد الأول (أقوى مفتاح)، بعدين الاسم بعد التطبيع
(بيفهم الاختصارات وبيشيل التشكيل)، وبعدين رقم القميص كآخر حل.
أي مطابقة بالرقم لوحده بتتعلّم ⚠️ لأنها مش موثوقة.

**ملحوظة للمراجعة:** ترانسفرماركت مصدر بشري ومش معصوم. أي اختلاف
معناه "راجع الحالة دي" مش "بياناتك غلط".
        """
    )

st.divider()

_ws, _log_err = _sheet()
email = st.text_input(
    "📧 إيميلك (إجباري — كل مراجعة بتتسجل باسمك)",
    value=st.session_state.get("reviewer_email", ""),
    placeholder="name@company.com",
)
if email:
    st.session_state["reviewer_email"] = email.strip()

if _ws is None and _log_err:
    st.warning(
        f"⚠️ تسجيل جوجل شيت مش مفعّل: {_log_err}\n\n"
        "المقارنة هتشتغل عادي بس مش هتتسجل. "
        "التفاصيل في SETUP-GOOGLE-SHEET.md"
    )
elif _log_err:
    st.warning(_log_err)

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. السيستم الداخلي")
    internal_text = st.text_area(
        "الصق الجدول:",
        height=300,
        placeholder="30 1001243 Noah Kruth 2003-06-24 Germany\n"
                    "4 124245 Eldin Dzogovic 2003-06-08 Luxembourg\n"
                    "Bench\n"
                    "15 122681 Daniel Heber 1994-07-04 Germany",
    )

with col2:
    st.subheader("2. ترانسفرماركت")

    fetch_mode = st.radio(
        "طريقة الجلب:",
        ["📋 لصق نص", "🔗 لينك الماتش (محجوب عادةً)"],
        horizontal=True,
        help="ترانسفرماركت بيحجب الطلبات الجاية من السيرفرات (AWS WAF)، "
             "فطريقة اللينك بتفشل غالباً. استخدم الإضافة أو البوكمارك "
             "في متصفحك وبعدين الصق النتيجة هنا.",
    )
    use_url = fetch_mode.startswith("🔗")

    tm_url, fs_text = "", ""

    if use_url:
        tm_url = st.text_input(
            "لينك ماتش ترانسفرماركت:",
            placeholder="https://www.transfermarkt.com/.../aufstellung/spielbericht/4940060",
            help="أي لينك للماتش ينفع — الأداة بتحوّله لصفحة التشكيلة لوحدها.",
        )
        want_dob = st.checkbox(
            "اجلب تواريخ الميلاد",
            value=True,
            help="بيفتح بروفايل كل لاعب. بيزود الوقت ~15 ثانية، "
                 "وبيتكاش يوم كامل فالمرة التانية فورية.",
        )
        st.caption(
            "لو رجع خطأ 403، يبقى ترانسفرماركت حجب IP السيرفر — "
            "حوّل على 📋 لصق نص."
        )
    else:
        fs_text = st.text_area(
            "الصق التشكيلة:",
            height=240,
            placeholder="1\tMark Oxley\t1990-09-28\tEngland\tHOME\n"
                        "24\tLewis Cass\t2000-02-27\tEngland\tHOME",
        )
        want_dob = False

    mixed_teams = st.checkbox(
        "النص فيه الفريقين مع بعض",
        value=True,
        help="لاعبين الفريق التاني هيتحطوا في قسم منفصل بدل ما "
             "يظهروا كأخطاء في الجدول الأساسي.",
    )
    side_choice = st.radio(
        "الفريق اللي بتقارنه:",
        ["ANY (الكل)", "HOME (صاحب الأرض)", "AWAY (الضيف)"],
        horizontal=True,
        help="مهم: أرقام القمصان بتتكرر بين الفريقين، فاختار فريقك.",
    )

st.divider()

st.divider()

# --- بيانات الماتش للتسجيل ---
# لازم تكون برة بلوك الزرار: في Streamlit أي خانة بتتعمل جوه البلوك
# قيمتها بترجع فاضية في نفس الجولة، فالتسجيل كان بياخد قيمة فاضية.
_meta = parse_meta(fs_text) if (not use_url and fs_text) else {}

_auto_id = _meta.get("match_id", "")
_auto_name = _meta.get("match_name", "")
_auto_link = _meta.get("match_url", "")
_auto_source = _meta.get("source", "transfermarkt")

if use_url and tm_url.strip():
    _mm = re.search(r"spielbericht/(\d+)", tm_match_url(tm_url))
    _auto_id = _mm.group(1) if _mm else ""
    _auto_link = tm_match_url(tm_url)
    _slug = urlparse(_auto_link).path.lstrip("/").split("/")[0]
    if "_" in _slug:
        _h, _, _a = _slug.partition("_")
        pretty = lambda s: " ".join(w.capitalize() for w in s.split("-") if w)
        _auto_name = f"{pretty(_h)} vs {pretty(_a)}"

st.subheader("🆔 بيانات الماتش")
st.caption(
    "الشمال من سيستمك (بتكتبه)، واليمين من ترانسفرماركت "
    "(بيتعبّى لوحده). الاتنين بيتسجلوا في الشيت."
)

sc, tc = st.columns(2)

with sc:
    st.markdown("**من السيستم عندك**")
    src_match_id = st.text_input(
        "Match ID (السيستم)",
        placeholder="مثال: 1884213",
        help="الـ ID اللي بتبحث بيه في الداتابيز بتاعتك.",
    ).strip()
    src_match_name = st.text_input(
        "اسم الماتش (السيستم)",
        placeholder="مثال: Harrogate Town - Solihull Moors",
    ).strip()

with tc:
    st.markdown("**من ترانسفرماركت**")
    match_id = st.text_input(
        "Match ID (ترانسفرماركت)",
        value=_auto_id,
        help="بيتعبّى لوحده من النص الملزوق.",
    ).strip()
    match_name = st.text_input(
        "اسم الماتش (ترانسفرماركت)",
        value=_auto_name,
        placeholder="Harrogate Town vs Solihull Moors",
    ).strip()

if fs_text and not _auto_id:
    st.caption(
        "ℹ️ النص الملزوق مفيهوش بيانات الماتش — يعني إما نسخته بالماوس، "
        "أو نسخة اليوزرسكريبت عندك قديمة. اكتب البيانات يدوي أو حدّث "
        "اليوزرسكريبت."
    )

st.divider()

if st.button("🚀 ابدأ المقارنة", type="primary", use_container_width=True):
    email = (st.session_state.get("reviewer_email") or "").strip()
    if not email:
        st.error("❌ لازم تحط إيميلك الأول — كل مراجعة بتتسجل باسم صاحبها.")
        st.stop()
    if not EMAIL_RE.match(email):
        st.error(f"❌ الإيميل `{email}` شكله مش صح. اكتبه بالشكل name@company.com")
        st.stop()
    if not src_match_id:
        st.error("❌ لازم Match ID من سيستمك — هو اللي يربط المراجعة بالماتش عندك.")
        st.stop()
    if not src_match_name:
        st.error("❌ لازم اسم الماتش من سيستمك.")
        st.stop()
    if not match_id:
        st.error(
            "❌ لازم Match ID من ترانسفرماركت. لو النص مفيهوش، "
            "خده من لينك الماتش — الرقم اللي في آخره."
        )
        st.stop()

    if not internal_text.strip():
        st.warning("⚠️ الصق جدول السيستم الداخلي الأول.")
        st.stop()
    if use_url and not tm_url.strip():
        st.warning("⚠️ حط لينك ماتش ترانسفرماركت.")
        st.stop()
    if not use_url and not fs_text.strip():
        st.warning("⚠️ الصق التشكيلة في الخانة اليمين.")
        st.stop()

    source_players = parse_internal(internal_text)
    if not source_players:
        st.error(
            "❌ معرفتش أفكك نص السيستم. لازم كل سطر يكون: "
            "رقم، ID، اسم، تاريخ ميلاد (YYYY-MM-DD)، جنسية."
        )
        st.stop()

    want_side = side_choice.split()[0]

    if use_url:
        if not HAVE_BS4:
            st.error(
                "❌ مكتبة beautifulsoup4 ناقصة. ضيف `beautifulsoup4` "
                "في requirements.txt واعمل redeploy."
            )
            st.stop()

        bar = st.progress(0.0, "بجيب صفحة التشكيلة...")
        fetched, msgs = tm_load(
            tm_url, want_dob,
            progress=lambda f, t: bar.progress(f, t),
        )
        bar.empty()

        with st.expander("📡 تفاصيل الجلب", expanded=not fetched):
            for m in msgs:
                st.write(m)

        if not fetched:
            st.error(
                "❌ الجلب فشل. شوف التفاصيل فوق. لو السبب 403، "
                "حوّل على 📋 لصق نص."
            )
            st.stop()

        fs_players = [
            p for p in fetched
            if want_side == "ANY" or p["side"] not in ("HOME", "AWAY")
            or p["side"] == want_side
        ]
    else:
        fs_players = parse_flashscore(fs_text, want_side)

    match_link = _auto_link
    source_name = _auto_source

    if not fs_players:
        st.error(
            "❌ مفيش لاعبين للمقارنة. لو فلترت بالفريق، جرّب ANY."
        )
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("لاعبين السورس", len(source_players))
    c2.metric("لاعبين Flashscore", len(fs_players))
    c3.metric("بتاريخ ميلاد", sum(1 for p in fs_players if p["dob"]))

    if not any(p["dob"] for p in fs_players):
        st.info(
            "ℹ️ مفيش تواريخ ميلاد في نص Flashscore، فالمطابقة هتمشي "
            "بالأسماء والأرقام. عمود تطابق الميلاد هيبان ➖."
        )

    pairs, only_src, only_fs = match_squads(source_players, fs_players)

    # لما النص فيه الفريقين، اللاعبين الزيادة هما الفريق التاني --
    # مش أخطاء، فبيروحوا قسم منفصل تحت.
    df = build_report(pairs, only_src, [] if mixed_teams else only_fs)

    full = sum(1 for r in df["الحالة"] if r.startswith("✅"))
    headline = (
        f"📊 النتيجة — {full} تطابق كامل، {len(pairs) - full} محتاج مراجعة، "
        f"{len(only_src)} عندك ومش عندهم"
    )
    if not mixed_teams:
        headline += f"، {len(only_fs)} عندهم ومش عندك"
    st.subheader(headline)

    def color_status(val):
        text = str(val)
        if "✅" in text:
            return "background-color: #1e4620; color: white;"
        if "⚠️" in text:
            return "background-color: #856404; color: white;"
        return "background-color: #721c24; color: white;"

    st.dataframe(
        df.style.map(color_status, subset=["الحالة"]),
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇️ تحميل CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        "lineup_check.csv",
        "text/csv",
    )

    # --- التسجيل في جوجل شيت ---
    review = len(pairs) - full
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
        len(only_fs) if not mixed_teams else 0,
        details[:4000],
        source_name,
    ])

    if ok_log:
        st.success(
            f"📝 اتسجلت في جوجل شيت — {src_match_name} "
            f"(سيستم {src_match_id} / ترانسفرماركت {match_id}) "
            f"باسم {email}"
        )
        if log_err:
            st.warning(log_err)
    else:
        st.error(
            f"⚠️ النتيجة ظهرت بس **التسجيل فشل**: {log_err}\n\n"
            "قول للمسؤول عن الأداة. لو التسجيل مطلوب للتوثيق، "
            "احفظ الـ CSV كبديل."
        )
        if BLOCK_ON_LOG_FAILURE:
            st.stop()

    if mixed_teams and only_fs:
        with st.expander(
            f"👥 {len(only_fs)} لاعب في نص Flashscore ملهمش مقابل عندك "
            "(على الأغلب الفريق التاني)"
        ):
            st.caption(
                "راجع القائمة دي بسرعة: لو لقيت فيها لاعب المفروض يكون "
                "في فريقك، يبقى فيه مشكلة حقيقية."
            )
            st.dataframe(
                pd.DataFrame([
                    {
                        "رقم": p["shirt"] if p["shirt"] is not None else "—",
                        "الاسم": p["name"],
                        "الميلاد": p["dob"] or "—",
                        "الفريق": p["side"],
                    }
                    for p in only_fs
                ]),
                use_container_width=True,
                hide_index=True,
            )
