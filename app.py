# -*- coding: utf-8 -*-
"""
Lineup Gatekeeper vs Flashscore -- نسخة مصححة
=============================================

أهم التعديلات عن النسخة القديمة:
  1. استخراج Match ID صح (القديم كان بياخد ID الفريق بالغلط) + خانة يدوية.
  2. بارسر فييد صحيح يحترم الفواصل التلاتة: ~ و ¬ و ÷
  3. فصل حقيقي بين تشكيلة Home و Away (مفيش fallback بيخلط الفريقين).
  4. مطابقة بتاريخ الميلاد أولاً، بعدين بالاسم المطبَّع، وبعدين بالرقم.
  5. مقارنة في الاتجاهين: لاعيبك اللي مش هناك + لاعيبهم اللي مش عندك.
  6. لوحة Debug تدمب الـ raw feed والمفاتيح، عشان تظبط الـ mapping بنفسك.

ملحوظة مهمة: فييدات فلاش سكور الداخلية غير موثقة وبتتغير. الإندبوينتس
والمفاتيح اللي تحت هي نقطة بداية -- استخدم لوحة الـ Debug وظبطها من
DevTools (Network -> فلتر "feed") قبل ما تعتمد على النتايج.

التشغيل:  streamlit run app.py
المتطلبات: streamlit pandas rapidfuzz cloudscraper requests
"""

import re
import unicodedata
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    from rapidfuzz import fuzz  # أسرع وأدق، ومش deprecated
except ImportError:  # pragma: no cover
    from fuzzywuzzy import fuzz

import cloudscraper


# ---------------------------------------------------------------------------
# إعدادات قابلة للتعديل من مكان واحد
# ---------------------------------------------------------------------------

# فواصل فييدات فلاش سكور -- دي كانت أكبر باج في النسخة القديمة
SEP_SECTION = "~"
SEP_FIELD = "\u00ac"  # ¬
SEP_KV = "\u00f7"     # ÷

DEFAULT_FEED_HOST = "local-global.flashscore.ninja"
DEFAULT_PROJECT_ID = "2"

# قوالب الفييد. {pid} = project id, {mid} = match id
# ظبطهم من DevTools لو اختلفوا.
FEED_TEMPLATES = {
    "lineups": "https://{host}/{pid}/x/feed/df_li_1_{mid}",
    "summary": "https://{host}/{pid}/x/feed/d_su_{mid}",
}

PLAYER_FEED_TEMPLATE = "https://{host}/{pid}/x/feed/df_pt_1_{plid}"

# --- خرايطة المفاتيح ---------------------------------------------------------
# كل قيمة دلالية ليها قائمة مفاتيح مرشحة، وأول واحد موجود هو اللي بيتاخد.
# لما تدمب الفييد من لوحة الـ Debug وتلاقي المفتاح الحقيقي، زوده هنا وبس.
FIELD_ALIASES = {
    "side": ["PO", "TE", "PS"],          # الفريق: عادة "1" = Home و "2" = Away
    "shirt": ["FU", "PJN", "SN"],        # رقم القميص
    "player_id": ["PID", "PD", "PI"],    # ID اللاعب على فلاش سكور
    "name": ["PN", "IF", "PNM"],         # اسم اللاعب
    "slug": ["PU", "PURL"],              # سلاج اللينك (لجلب البروفايل)
    "dob": ["DO", "PBD", "BD"],          # تاريخ الميلاد (لو موجود)
    "country": ["NA", "CN", "PCN"],      # الجنسية / البلد
    "role": ["PPN", "PP"],               # أساسي/بديل
}

# ألياس بسيط للجنسيات. زوّد عليه حسب اللي بيظهر عندك.
COUNTRY_ALIASES = {
    "germany": {"germany", "ger", "deutschland", "de"},
    "luxembourg": {"luxembourg", "lux", "lu"},
    "togo": {"togo", "tog", "tg"},
    "peru": {"peru", "per", "pe"},
    "croatia": {"croatia", "cro", "hrvatska", "hr"},
    "usa": {"usa", "united states", "united states of america", "us"},
    "south korea": {"south korea", "korea republic", "republic of korea", "kor"},
    "ivory coast": {"ivory coast", "cote d'ivoire", "cote divoire", "civ"},
}

NAME_MATCH_THRESHOLD = 85  # أعلى بكتير من 50 اللي كانت في النسخة القديمة


# ---------------------------------------------------------------------------
# 1. تطبيع الأسماء والتواريخ
# ---------------------------------------------------------------------------

def strip_diacritics(text: str) -> str:
    """Šimić -> Simic ، Chávez -> Chavez"""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    if not name:
        return ""
    out = strip_diacritics(str(name)).lower()
    out = out.replace("ø", "o").replace("ß", "ss").replace("đ", "d")
    out = out.replace("ł", "l").replace("æ", "ae")
    out = re.sub(r"[^a-z0-9 .]", " ", out)   # نسيب النقطة عشان الاختصارات
    out = re.sub(r"\s+", " ", out).strip()
    return out


def name_tokens(name: str):
    """يرجع (tokens_كاملة, initials) -- الاختصار زي 'F.' يتعامل كحرف أول."""
    full, initials = [], []
    for tok in normalize_name(name).split():
        clean = tok.rstrip(".")
        if not clean:
            continue
        if len(clean) == 1:
            initials.append(clean)
        else:
            full.append(clean)
    return full, initials


def name_similarity(src_name: str, fs_name: str) -> int:
    """
    نسبة تشابه واعية بالاختصارات.

    فلاش سكور بيكتب 'Chávez Fischer F.' والسورس عندك
    'Felipe Marlon Chávez Fischer' -- المقارنة النصية العادية بتفشل،
    فبنتعامل مع الحروف المختصرة كحروف أولى للأسماء.
    """
    if not src_name or not fs_name:
        return 0

    src_full, src_init = name_tokens(src_name)
    fs_full, fs_init = name_tokens(fs_name)

    base = fuzz.token_set_ratio(" ".join(src_full), " ".join(fs_full))

    # كل توكن كامل عند فلاش سكور لازم يلاقي مقابل في السورس
    remaining = list(src_full)
    ok = True
    for tok in fs_full:
        hit = next(
            (r for r in remaining if fuzz.ratio(tok, r) >= 88),
            None,
        )
        if hit is None:
            ok = False
            break
        remaining.remove(hit)

    # والحروف المختصرة لازم تطابق أول حرف في اسم متبقي
    if ok:
        for ini in fs_init + src_init:
            hit = next((r for r in remaining if r.startswith(ini)), None)
            if hit is not None:
                remaining.remove(hit)

    if ok and fs_full:
        return max(base, 96)
    return int(base)


def normalize_dob(value) -> str:
    """يرجّع ISO (YYYY-MM-DD) من أي فورمات شائع، أو '' لو فشل."""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw or raw in {"-", "غير متوفر"}:
        return ""

    # timestamp
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
    return ""


def country_key(value: str) -> str:
    norm = normalize_name(value).replace(".", "").strip()
    for canon, aliases in COUNTRY_ALIASES.items():
        if norm in aliases:
            return canon
    return norm


# ---------------------------------------------------------------------------
# 2. تفكيك النص الداخلي
# ---------------------------------------------------------------------------

ROW_RE = re.compile(
    r"^(\d{1,3})\s+(\d{3,})\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s*(.*)$"
)

def parse_pasted_text(text: str):
    players = []
    section = "Started"

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        low = line.lower()
        if low.startswith("bench") or low == "bench":
            section = "Bench"
            continue
        if low.startswith("started") or low.startswith("starting"):
            section = "Started"
            continue
        # سطر العنوان بتاع الجدول
        if "remove from lineup" in low or low.startswith("id "):
            continue

        m = ROW_RE.match(line)
        if m:
            number, internal_id, name, dob, nat = m.groups()
        else:
            parts = [p.strip() for p in re.split(r"\t+|\s{2,}", line) if p.strip()]
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            number, internal_id, name, dob = parts[0], parts[1], parts[2], parts[3]
            nat = parts[4] if len(parts) > 4 else ""

        players.append(
            {
                "number": int(number),
                "internal_id": internal_id,
                "name": name.strip(),
                "dob": normalize_dob(dob),
                "nationality": nat.strip(),
                "type": "أساسي" if section == "Started" else "بديل",
            }
        )
    return players


# ---------------------------------------------------------------------------
# 3. الشبكة: Match ID + الفييدات
# ---------------------------------------------------------------------------

def build_scraper():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


def match_id_from_url(url: str):
    """
    الفورمات القديم فقط: /match/{8 chars}/
    الفورمات الجديد (/match/football/team-ID1/team-ID2/) فيه IDs الفرق
    مش الماتش -- فبنرجع None ونروح نجيبه من الصفحة. ده كان الباج الأصلي.
    """
    m = re.search(r"/match/([A-Za-z0-9]{8})(?:[/#?]|$)", url)
    return m.group(1) if m else None


MATCH_ID_PATTERNS = [
    r'"matchId"\s*:\s*"([A-Za-z0-9]{8})"',
    r"matchId\s*[:=]\s*[\"']([A-Za-z0-9]{8})[\"']",
    r"d_su_([A-Za-z0-9]{8})",
    r"df_li_1_([A-Za-z0-9]{8})",
    r'"eventId"\s*:\s*"([A-Za-z0-9]{8})"',
]


@st.cache_data(ttl=180, show_spinner=False)
def resolve_match_id(url: str):
    """يحاول من اللينك، وبعدين من HTML الصفحة. يرجّع (match_id, debug_note)."""
    direct = match_id_from_url(url)
    if direct:
        return direct, "اتاخد من اللينك مباشرة (فورمات قديم)."

    scraper = build_scraper()
    try:
        res = scraper.get(url, timeout=20)
    except Exception as exc:
        return None, f"فشل تحميل الصفحة: {exc}"

    if res.status_code != 200:
        return None, f"الصفحة رجعت كود {res.status_code}"

    html = res.text
    for pat in MATCH_ID_PATTERNS:
        m = re.search(pat, html)
        if m:
            return m.group(1), f"اتاخد من HTML الصفحة بالباترن: {pat}"

    return None, (
        "مش لاقي match id في HTML الصفحة. الصفحة على الأغلب بتحمّل بالجافاسكريبت. "
        "استخدم خانة الـ Match ID اليدوية (هاتها من DevTools -> Network -> فلتر feed)."
    )


@st.cache_data(ttl=180, show_spinner=False)
def fetch_feed(feed_url: str):
    """يرجّع (text, status_code, error)."""
    scraper = build_scraper()
    headers = {
        "Referer": "https://www.flashscore.com/",
        "Origin": "https://www.flashscore.com",
        "Accept": "*/*",
        # القيمة دي بتتغير. لو رجع 404/فاضي، هاتها من DevTools:
        # افتح أي ريكوست فييد -> Request Headers -> x-fsign
        "X-Fsign": "SW9D1eZo",
    }
    try:
        res = scraper.get(feed_url, headers=headers, timeout=20)
        return res.text, res.status_code, None
    except Exception as exc:
        return "", None, str(exc)


# ---------------------------------------------------------------------------
# 4. بارسر الفييد -- الإصلاح الأساسي
# ---------------------------------------------------------------------------

def parse_feed(text: str):
    """
    فييدات فلاش سكور: سكشنز بـ '~'، حقول بـ '¬'، ومفتاح/قيمة بـ '÷'.
    النسخة القديمة كانت بتنسى '¬' تماماً، فكل القيم كانت بتطلع ملزوقة
    بالمفتاح اللي بعدها.
    """
    records = []
    for section in text.split(SEP_SECTION):
        if not section.strip():
            continue
        kv = {}
        for field in section.split(SEP_FIELD):
            if SEP_KV not in field:
                continue
            key, val = field.split(SEP_KV, 1)
            key = key.strip()
            if key:
                kv[key] = val.strip()
        if kv:
            records.append(kv)
    return records


def pick(record: dict, semantic: str, default=""):
    for key in FIELD_ALIASES.get(semantic, []):
        if record.get(key):
            return record[key]
    return default


def extract_lineup(records, want_home: bool):
    """
    يطلّع لاعيبي فريق واحد بس. مفيش fallback بياخد اللاعبين اللي
    مالهمش side -- ده اللي كان بيخلط الفريقين في النسخة القديمة.
    """
    target = "1" if want_home else "2"
    current_side = None
    players = []

    for rec in records:
        side = pick(rec, "side")
        if side in ("1", "2"):
            current_side = side

        name = pick(rec, "name")
        if not name:
            continue

        effective = side if side in ("1", "2") else current_side
        if effective != target:
            continue

        shirt = pick(rec, "shirt")
        players.append(
            {
                "fs_id": pick(rec, "player_id", "—"),
                "name": name,
                "shirt": int(shirt) if str(shirt).isdigit() else None,
                "dob": normalize_dob(pick(rec, "dob")),
                "nationality": pick(rec, "country", ""),
                "slug": pick(rec, "slug", ""),
                "role": pick(rec, "role", ""),
                "_raw": rec,
            }
        )
    return players


@st.cache_data(ttl=600, show_spinner=False)
def fetch_player_dob(player_id: str, host: str, pid: str):
    """
    تاريخ الميلاد عادةً مش موجود في فييد التشكيلة، فمحتاج ريكوست
    لبروفايل اللاعب. لو الإندبوينت ده مش شغال عندك، هاته من DevTools
    وعدّل PLAYER_FEED_TEMPLATE.
    """
    if not player_id or player_id == "—":
        return "", "مفيش player id"

    url = PLAYER_FEED_TEMPLATE.format(host=host, pid=pid, plid=player_id)
    text, status, err = fetch_feed(url)
    if err or status != 200 or not text:
        return "", f"فشل ({err or status})"

    for rec in parse_feed(text):
        dob = normalize_dob(pick(rec, "dob"))
        if dob:
            return dob, "ok"

    m = re.search(r"(\d{2}[./-]\d{2}[./-]\d{4})", text)
    if m:
        return normalize_dob(m.group(1)), "regex fallback"
    return "", "مش لاقي تاريخ ميلاد في الرد"


# ---------------------------------------------------------------------------
# 5. المطابقة -- تاريخ الميلاد أولاً، بعدين الاسم، بعدين الرقم
# ---------------------------------------------------------------------------

def match_squads(source, flashscore):
    """
    يرجّع (pairs, only_source, only_flashscore).
    كل pair = (لاعب السورس, لاعب فلاش سكور, طريقة المطابقة, ثقة).
    """
    pairs = []
    src_left = list(source)
    fs_left = list(flashscore)

    # --- مرحلة 1: تاريخ الميلاد (أقوى مفتاح) ---
    for src in list(src_left):
        if not src["dob"]:
            continue
        cands = [f for f in fs_left if f["dob"] and f["dob"] == src["dob"]]
        if not cands:
            continue
        if len(cands) > 1:  # نفس تاريخ الميلاد -> نفضّ بالاسم
            cands.sort(key=lambda f: name_similarity(src["name"], f["name"]),
                       reverse=True)
        best = cands[0]
        pairs.append((src, best, "تاريخ الميلاد", 100))
        src_left.remove(src)
        fs_left.remove(best)

    # --- مرحلة 2: الاسم المطبَّع ---
    for src in list(src_left):
        scored = [(name_similarity(src["name"], f["name"]), f) for f in fs_left]
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= NAME_MATCH_THRESHOLD:
            score, best = scored[0]
            pairs.append((src, best, "الاسم", score))
            src_left.remove(src)
            fs_left.remove(best)

    # --- مرحلة 3: رقم القميص فقط (ضعيف -- للمراجعة اليدوية) ---
    for src in list(src_left):
        best = next((f for f in fs_left if f["shirt"] == src["number"]), None)
        if best is not None:
            pairs.append((src, best, "رقم القميص فقط ⚠", 40))
            src_left.remove(src)
            fs_left.remove(best)

    return pairs, src_left, fs_left


def build_report(pairs, only_source, only_fs):
    rows = []

    for src, fs, method, conf in pairs:
        num_ok = fs["shirt"] is not None and fs["shirt"] == src["number"]
        name_score = name_similarity(src["name"], fs["name"])
        name_ok = name_score >= NAME_MATCH_THRESHOLD

        if src["dob"] and fs["dob"]:
            dob_state = "✅" if src["dob"] == fs["dob"] else "❌"
        else:
            dob_state = "➖"

        sk, fk = country_key(src["nationality"]), country_key(fs["nationality"])
        nat_state = "➖" if not sk or not fk else ("✅" if sk == fk else "❌")

        hard_fail = "❌" in (dob_state, nat_state) or not name_ok or not num_ok
        if method.startswith("رقم القميص"):
            status = "⚠️ مطابقة ضعيفة — راجعه يدوي"
        elif not hard_fail:
            status = "✅ تطابق كامل"
        else:
            status = "⚠️ تطابق ناقص (اختلاف بيانات)"

        rows.append(
            {
                "رقم السورس": src["number"],
                "رقم Flashscore": fs["shirt"] if fs["shirt"] is not None else "—",
                "تطابق الرقم": "✅" if num_ok else "❌",
                "اسم السورس": src["name"],
                "اسم Flashscore": fs["name"],
                "تشابه الاسم %": name_score,
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
            }
        )

    for src in only_source:
        rows.append(
            {
                "رقم السورس": src["number"],
                "رقم Flashscore": "—",
                "تطابق الرقم": "❌",
                "اسم السورس": src["name"],
                "اسم Flashscore": "— غير موجود —",
                "تشابه الاسم %": 0,
                "ميلاد السورس": src["dob"] or "—",
                "ميلاد Flashscore": "—",
                "تطابق الميلاد": "❌",
                "جنسية السورس": src["nationality"] or "—",
                "جنسية Flashscore": "—",
                "تطابق الجنسية": "❌",
                "ID داخلي": src["internal_id"],
                "ID فلاش سكور": "—",
                "طريقة المطابقة": "—",
                "الحالة": "❌ عندك ومش عند Flashscore",
            }
        )

    for fs in only_fs:
        rows.append(
            {
                "رقم السورس": "—",
                "رقم Flashscore": fs["shirt"] if fs["shirt"] is not None else "—",
                "تطابق الرقم": "❌",
                "اسم السورس": "— غير موجود —",
                "اسم Flashscore": fs["name"],
                "تشابه الاسم %": 0,
                "ميلاد السورس": "—",
                "ميلاد Flashscore": fs["dob"] or "—",
                "تطابق الميلاد": "❌",
                "جنسية السورس": "—",
                "جنسية Flashscore": fs["nationality"] or "—",
                "تطابق الجنسية": "❌",
                "ID داخلي": "—",
                "ID فلاش سكور": fs["fs_id"],
                "طريقة المطابقة": "—",
                "الحالة": "❌ عند Flashscore ومش عندك",
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. الواجهة
# ---------------------------------------------------------------------------

st.set_page_config(page_title="مطابقة التشكيلات", page_icon="⚽", layout="wide")
st.title("⚽ مطابقة التشكيلات: السيستم الداخلي ضد Flashscore")
st.caption(
    "المطابقة بتمشي على تاريخ الميلاد أولاً، بعدين الاسم المطبَّع، "
    "وبعدين رقم القميص كآخر حل."
)

with st.sidebar:
    st.header("⚙️ إعدادات الفييد")
    st.caption(
        "هات القيم دي من DevTools: افتح صفحة الماتش، F12 → Network، "
        "فلتر بكلمة feed، واضغط تاب Lineups."
    )
    feed_host = st.text_input("Feed host", DEFAULT_FEED_HOST)
    project_id = st.text_input("Project ID", DEFAULT_PROJECT_ID)
    feed_kind = st.selectbox("نوع الفييد", list(FEED_TEMPLATES.keys()))
    manual_match_id = st.text_input("Match ID يدوي (اختياري)", "")
    enrich_dob = st.checkbox(
        "اجلب تاريخ الميلاد من بروفايل كل لاعب", value=True,
        help="ريكوست إضافي لكل لاعب. أطول، بس بيمكّن مطابقة الميلاد.",
    )
    show_debug = st.checkbox("لوحة Debug (الـ raw feed والمفاتيح)", value=True)

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. بيانات الفريق الداخلي")
    raw_text = st.text_area(
        "الصق الجدول هنا:",
        height=260,
        placeholder="30 1001243 Noah Kruth 2003-06-24 Germany\n"
                    "4 124245 Eldin Dzogovic 2003-06-08 Luxembourg\n"
                    "Bench\n"
                    "15 122681 Daniel Heber 1994-07-04 Germany",
    )
    team_side = st.radio(
        "الفريق ده في المباراة:",
        ["Home (صاحب الأرض)", "Away (الضيف)"],
        horizontal=True,
    )

with col2:
    st.subheader("2. رابط المباراة من Flashscore")
    flashscore_url = st.text_input(
        "اللينك:", placeholder="https://www.flashscore.com/match/football/..."
    )
    st.info(
        "الفورمات الجديد للينكات فيه IDs الفرق مش ID الماتش، فالأداة "
        "بتجيب الصفحة وتستخرجه. لو فشلت، استخدم خانة Match ID اليدوية "
        "في الشريط الجانبي."
    )
    st.warning(
        "الفييدات دي داخلية وغير موثقة، وبتخالف شروط استخدام Flashscore. "
        "للاستخدام الإنتاجي استخدم مصدر مرخص (Sportradar / Opta / StatsBomb)."
    )

st.divider()

if st.button("🚀 ابدأ المقارنة", type="primary", use_container_width=True):
    if not raw_text.strip():
        st.warning("⚠️ الصق جدول السيستم الداخلي الأول.")
        st.stop()
    if not flashscore_url.strip() and not manual_match_id.strip():
        st.warning("⚠️ محتاج لينك الماتش أو Match ID يدوي.")
        st.stop()

    source_players = parse_pasted_text(raw_text)
    if not source_players:
        st.error("❌ معرفتش أفكك النص. لازم كل سطر يكون: رقم / ID / اسم / تاريخ ميلاد / جنسية")
        st.stop()
    st.success(f"اتقرا {len(source_players)} لاعب من السيستم الداخلي.")

    # --- Match ID ---
    if manual_match_id.strip():
        match_id, note = manual_match_id.strip(), "يدوي"
    else:
        with st.spinner("بدور على Match ID..."):
            match_id, note = resolve_match_id(flashscore_url.strip())

    if not match_id:
        st.error(f"❌ مش قادر أحدد Match ID. {note}")
        st.stop()
    st.info(f"Match ID: `{match_id}` — {note}")

    # --- الفييد ---
    feed_url = FEED_TEMPLATES[feed_kind].format(
        host=feed_host.strip(), pid=project_id.strip(), mid=match_id
    )
    with st.spinner("بجيب الفييد..."):
        text, status, err = fetch_feed(feed_url)

    if show_debug:
        with st.expander("🔍 Debug — الـ raw feed", expanded=not text):
            st.code(feed_url, language="text")
            st.write(f"Status: `{status}` | Error: `{err}` | طول الرد: {len(text)}")
            st.text_area("أول 3000 حرف من الرد:", text[:3000], height=220)

    if err:
        st.error(
            f"❌ الاتصال فشل: {err}\n\n"
            "لو الرسالة فيها NameResolutionError يبقى الهوست غلط — "
            "عدّله من الشريط الجانبي."
        )
        st.stop()
    if status != 200 or not text.strip():
        st.error(
            f"❌ الفييد رجع كود {status} أو رد فاضي. جرّب تعدّل الـ X-Fsign "
            "أو الـ Project ID أو قالب الفييد."
        )
        st.stop()

    records = parse_feed(text)

    if show_debug:
        keys = {}
        for rec in records:
            for k, v in rec.items():
                keys.setdefault(k, v)
        with st.expander(f"🔍 Debug — المفاتيح المكتشفة ({len(keys)})"):
            st.caption("لو مفتاح مهم مش في FIELD_ALIASES، زوّده هناك.")
            st.dataframe(
                pd.DataFrame(
                    [{"المفتاح": k, "قيمة نموذجية": v} for k, v in sorted(keys.items())]
                ),
                use_container_width=True,
            )

    is_home = team_side.startswith("Home")
    fs_players = extract_lineup(records, is_home)

    if not fs_players:
        st.error(
            "❌ مفيش لاعيبين اتطلعوا للفريق ده.\n\n"
            "الأسباب المحتملة:\n"
            "- الفييد ده مش فييد التشكيلة (جرّب lineups مش summary)\n"
            "- مفاتيح الحقول مختلفة — شوف لوحة الـ Debug فوق\n"
            "- التشكيلة مش منزلة على Flashscore لسه (بتنزل ~ساعة قبل الماتش)\n"
            "- اختيار Home/Away معكوس"
        )
        st.stop()

    st.success(f"اتطلع {len(fs_players)} لاعب من Flashscore.")

    if enrich_dob and not any(p["dob"] for p in fs_players):
        bar = st.progress(0.0, "بجيب تواريخ الميلاد من البروفايلات...")
        for i, p in enumerate(fs_players):
            dob, why = fetch_player_dob(p["fs_id"], feed_host.strip(), project_id.strip())
            p["dob"] = dob
            p["_dob_note"] = why
            bar.progress((i + 1) / len(fs_players))
        bar.empty()
        if not any(p["dob"] for p in fs_players):
            st.warning(
                "⚠️ مجبتش أي تاريخ ميلاد. المطابقة هتمشي بالاسم والرقم بس. "
                "عدّل PLAYER_FEED_TEMPLATE من DevTools."
            )

    pairs, only_src, only_fs = match_squads(source_players, fs_players)
    df = build_report(pairs, only_src, only_fs)

    st.subheader(
        f"📊 النتيجة ({'صاحب الأرض' if is_home else 'الضيف'}) — "
        f"{len(pairs)} متطابق، {len(only_src)} عندك بس، {len(only_fs)} عندهم بس"
    )

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
        f"lineup_check_{match_id}.csv",
        "text/csv",
    )
