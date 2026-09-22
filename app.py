# -*- coding: utf-8 -*-
"""
Lineup Checker -- مقارنة تشكيلة السيستم الداخلي بتشكيلة Flashscore
=================================================================

النسخة دي مفيهاش أي اتصال بالإنترنت. بتقارن نصين ملزوقين:
  - يسار: جدول السيستم الداخلي
  - يمين: مخرج سكريبت flashscore-extract.js (أو نسخ يدوي من الصفحة)

ليه؟ فييدات فلاش سكور بقت GraphQL بـ persisted queries، والهاش بتاعها
بيتغير مع كل ديبلوي، فأي سكرابينج بيفصل كل أسبوعين. اللصق مش بيفصل أبداً.

المطابقة بتمشي بالترتيب ده:
  1. تاريخ الميلاد (أقوى مفتاح)
  2. الاسم بعد التطبيع (شيل التشكيل، وفهم الاختصارات زي "Kruth N.")
  3. رقم القميص لوحده -- وبتتعلّم كمطابقة ضعيفة محتاجة مراجعة

التشغيل: streamlit run app.py
"""

import re
import unicodedata
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    from fuzzywuzzy import fuzz


NAME_MATCH_THRESHOLD = 85

COUNTRY_ALIASES = {
    "germany": {"germany", "ger", "deutschland", "de"},
    "luxembourg": {"luxembourg", "lux", "lu"},
    "togo": {"togo", "tog", "tg"},
    "peru": {"peru", "per", "pe"},
    "croatia": {"croatia", "cro", "hrvatska", "hr"},
    "austria": {"austria", "aut", "osterreich", "at"},
    "switzerland": {"switzerland", "sui", "sui.", "schweiz", "ch"},
    "netherlands": {"netherlands", "ned", "holland", "nl"},
    "usa": {"usa", "united states", "united states of america", "us"},
    "south korea": {"south korea", "korea republic", "republic of korea", "kor"},
    "ivory coast": {"ivory coast", "cote divoire", "civ"},
    "dr congo": {"dr congo", "congo dr", "democratic republic of congo", "cod"},
}


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


def country_key(value: str) -> str:
    norm = normalize_name(value).replace(".", "").strip()
    for canon, aliases in COUNTRY_ALIASES.items():
        if norm in aliases:
            return canon
    return norm


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

def parse_flashscore(text: str, want_side: str):
    """
    بيقبل صيغتين:

    أ) مخرج السكريبت (TSV بترتيب: number, name, dob, country, side, fs_id)
    ب) نسخ يدوي من الصفحة -- رقم + اسم، والتاريخ لو موجود في أي مكان

    want_side: "HOME" أو "AWAY" أو "ANY"
    """
    players = []

    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if "\t" in line:
            cols = [c.strip() for c in line.split("\t")]
            cols += [""] * (6 - len(cols))
            number, name, dob, country, side, fs_id = cols[:6]
        else:
            m = re.match(r"^\s*(\d{1,2})[.\s]+(.+)$", line)
            if not m:
                continue
            number, rest = m.group(1), m.group(2).strip()
            dob_m = re.search(
                r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", rest
            )
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

        sk, fk = country_key(src["nationality"]), country_key(fs["nationality"])
        nat_state = "➖" if not sk or not fk else ("✅" if sk == fk else "❌")

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

with st.expander("📋 إزاي تجيب تشكيلة Flashscore", expanded=False):
    st.markdown(
        """
1. افتح صفحة الماتش على تاب **Lineups** واستنى التشكيلة تظهر.
2. `F12` ← تاب **Console**. لو كروم طلب، اكتب `allow pasting` واضغط Enter.
3. الصق محتوى `flashscore-extract.js` كله واضغط Enter.
4. استنى لحد ما يقولك تم النسخ، وبعدين الصق هنا في الخانة اليمين.

السكريبت بيجيب رقم القميص والاسم والفريق، وبيحاول يجيب تاريخ الميلاد
والجنسية من بروفايل كل لاعب. لو تواريخ الميلاد مجاتش، المقارنة هتمشي
بالأسماء والأرقام بس وهتلاقي ➖ في عمود تطابق الميلاد.

**بديل بدون سكريبت:** علّم على التشكيلة في الصفحة بالماوس، `Ctrl+C`،
والصق هنا. هتجيب الأرقام والأسماء بس.
        """
    )

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
    st.subheader("2. Flashscore")
    fs_text = st.text_area(
        "الصق مخرج السكريبت:",
        height=300,
        placeholder="#number\tname\tdob\tcountry\tside\tfs_id\n"
                    "30\tKruth N.\t2003-06-24\tGermany\tAWAY\tGbn7WgOf",
    )
    side_choice = st.radio(
        "الفريق اللي بتقارنه:",
        ["AWAY (الضيف)", "HOME (صاحب الأرض)", "ANY (كل اللي ملزوق)"],
        horizontal=False,
    )

st.divider()

if st.button("🚀 ابدأ المقارنة", type="primary", use_container_width=True):
    if not internal_text.strip():
        st.warning("⚠️ الصق جدول السيستم الداخلي الأول.")
        st.stop()
    if not fs_text.strip():
        st.warning("⚠️ الصق تشكيلة Flashscore في الخانة اليمين.")
        st.stop()

    source_players = parse_internal(internal_text)
    if not source_players:
        st.error(
            "❌ معرفتش أفكك نص السيستم. لازم كل سطر يكون: "
            "رقم، ID، اسم، تاريخ ميلاد (YYYY-MM-DD)، جنسية."
        )
        st.stop()

    want_side = side_choice.split()[0]
    fs_players = parse_flashscore(fs_text, want_side)
    if not fs_players:
        st.error(
            "❌ معرفتش أفكك نص Flashscore.\n\n"
            "لو مستخدم السكريبت، تأكد إنك لزقت كل المخرج بالسطر الأول "
            "اللي فيه #number. ولو الفريق المختار غلط، جرّب ANY."
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
    df = build_report(pairs, only_src, only_fs)

    full = sum(1 for r in df["الحالة"] if r.startswith("✅"))
    st.subheader(
        f"📊 النتيجة — {full} تطابق كامل، {len(pairs) - full} محتاج مراجعة، "
        f"{len(only_src)} عندك بس، {len(only_fs)} عندهم بس"
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
        "lineup_check.csv",
        "text/csv",
    )
