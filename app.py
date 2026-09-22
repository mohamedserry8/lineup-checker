import re
from fuzzywuzzy import fuzz
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="أداة مطابقة التشكيلات الحية", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة المطابقة المباشرة الحية (Flashscore Live Scraper)")
st.write(
    "تستخرج هذه الأداة بيانات التشكيلة ديناميكياً من رابط Flashscore المرفق وتُقارنها مع جدول النظام الداخلي."
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. بيانات الفريق الداخلي")
    raw_text = st.text_area(
        "انسخ محتوى الجدول بالكامل والصقه هنا:",
        height=240,
        placeholder="مثال:\n1 28925 Dominik Reimann 1997-06-18 Germany\n17 1021244 Alexander Nollenberger 1997-06-04 Germany...",
    )

    team_side = st.radio(
        "هذا الفريق يمثل في المباراة:",
        ["Home (صاحب الأرض)", "Away (الضيف)"],
        horizontal=True,
    )

with col2:
    st.subheader("2. رابط المباراة من Flashscore")
    flashscore_url = st.text_input(
        "أدخل رابط المباراة:",
        placeholder="https://www.flashscore.com/match/football/...",
    )
    st.info(
        "💡 يتم استخراج Match ID الحقيقي بدقة حتى مع وجود أسماء الفرق بالرابط."
    )


# --- 1. تفكيك النص الداخلي (يدعم الأرقام من 0 حتى 1000) ---
def parse_pasted_text(text):
    players = []
    lines = text.strip().split("\n")
    current_section = "Started"

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if "Bench" in line:
            current_section = "Bench"
            continue
        elif "Started" in line:
            current_section = "Started"
            continue

        match = re.search(
            r"^(\d{1,4})\s+(\d+)\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s*(.*)$", line
        )
        if match:
            players.append(
                {
                    "number": int(match.group(1)),
                    "internal_id": match.group(2),
                    "name": match.group(3).strip(),
                    "dob": match.group(4).strip(),
                    "nationality": match.group(5).strip(),
                    "type": (
                        "أساسي" if current_section == "Started" else "بديل"
                    ),
                }
            )
        else:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) >= 4 and parts[0].isdigit():
                players.append(
                    {
                        "number": int(parts[0]),
                        "internal_id": parts[1],
                        "name": parts[2],
                        "dob": parts[3],
                        "nationality": parts[4] if len(parts) > 4 else "",
                        "type": (
                            "أساسي" if current_section == "Started" else "بديل"
                        ),
                    }
                )
    return players


# --- 2. دالة ذكية واستثنائية لاستخراج Match ID بدقة ---
def extract_match_id(url):
    # 1. البحث عن كود يسبقه شرطة - ومتبوع بـ / (مثل -CCQGbik8/)
    match = re.search(r"-([a-zA-Z0-9]{8})(?:/|$)", url)
    if match:
        return match.group(1)

    # 2. البحث بعد كلمة /match/ مباشرة
    match = re.search(r"/match/([a-zA-Z0-9]{8})(?:/|$)", url)
    if match:
        return match.group(1)

    # 3. البحث عن أي كود 8 خانات يحتوي على أرقام أو أحرف كبيرة ليضمن استبعاد الكلمات العادية
    candidates = re.findall(r"([a-zA-Z0-9]{8})", url)
    for cand in candidates:
        if (
            any(c.isdigit() for c in cand) or any(c.isupper() for c in cand)
        ) and cand.lower() not in [
            "football",
            "summary",
            "lineups",
            "matches",
            "greuther",
        ]:
            return cand

    return None


def fetch_flashscore_data(url, is_home):
    match_id = extract_match_id(url)
    if not match_id:
        st.error(
            "❌ تعذر استخراج كود المباراة (Match ID) من الرابط! يرجى التأكد من مسار الرابط."
        )
        return {}

    feed_url = f"https://www.flashscore.com/x/feed/d_su_{match_id}_en_1"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-fsign": "SW1hZ2luZSB3aXRob3V0IG1lbnRpb25pbmc=",
    }

    try:
        res = requests.get(feed_url, headers=headers, timeout=12)
        if res.status_code != 200 or not res.text:
            st.error(
                f"❌ لم يستجب سيرفر Flashscore (كود الاستجابة: {res.status_code})"
            )
            return {}
        text = res.text
    except Exception as e:
        st.error(f"❌ خطأ أثناء الاتصال بـ Flashscore: {e}")
        return {}

    target_side = "1" if is_home else "2"
    team_data = {}

    items = text.split("~")
    for item in items:
        parts = item.split("÷")
        kv = {}
        for i in range(0, len(parts) - 1, 2):
            kv[parts[i]] = parts[i + 1]

        po = kv.get("PO", "")  # 1 = Home, 2 = Away
        fu = kv.get("FU", "")  # رقم القميص

        if fu.isdigit():
            num = int(fu)
            if po == target_side or not po:
                team_data[num] = {
                    "fs_id": kv.get("PD", "غير متوفر"),
                    "name": kv.get("IF", kv.get("PN", "غير متوفر")),
                    "dob": kv.get("DO", "غير متوفر"),
                    "nationality": kv.get("NA", "غير متوفر"),
                }

    return team_data


# --- 3. إجراء المقارنة والتطابق المباشر ---
st.divider()

if st.button(
    "🚀 ابدأ إجراء المقارنة الشاملة مع Flashscore",
    type="primary",
    use_container_width=True,
):
    if not raw_text.strip():
        st.warning("⚠️ يرجى لصق نص جدول النظام الداخلي أولاً!")
    elif not flashscore_url.strip():
        st.warning("⚠️ يرجى إدخل رابط المباراة من Flashscore!")
    else:
        source_players = parse_pasted_text(raw_text)

        if not source_players:
            st.error("❌ تعذر تفكيك النص! يرجى التأكد من اختيار كامل الجدول.")
        else:
            is_home_team = "Home" in team_side
            flashscore_data = fetch_flashscore_data(
                flashscore_url, is_home_team
            )

            if not flashscore_data:
                st.error(
                    "⚠️ لم يتم العثور على تشكيلة لهذا الفريق في رابط Flashscore المرفق!"
                )
            else:
                comparison_results = []

                for p in source_players:
                    num = p["number"]
                    fs_p = flashscore_data.get(num, {})

                    fs_id = fs_p.get("fs_id", "غير موجود")
                    fs_name = fs_p.get("name", "غير موجود بالرقم")
                    fs_dob = fs_p.get("dob", "غير موجود")
                    fs_nat = fs_p.get("nationality", "غير موجود")

                    # 1. مطابقة الرقم
                    number_matched = num in flashscore_data

                    # 2. مطابقة الاسم
                    name_sim = (
                        fuzz.token_sort_ratio(
                            p["name"].lower(), fs_name.lower()
                        )
                        if fs_name != "غير موجود بالرقم"
                        else 0
                    )
                    name_matched = name_sim > 65 or (
                        fs_name.lower() in p["name"].lower()
                        and len(fs_name) > 3
                    )

                    # 3. مطابقة الجنسية
                    nat_matched = (
                        (
                            p["nationality"].lower() in fs_nat.lower()
                            or fs_nat.lower() in p["nationality"].lower()
                        )
                        if fs_nat != "غير متوفر"
                        else True
                    )

                    # 4. مطابقة تاريخ الميلاد
                    dob_matched = (
                        (p["dob"] == fs_dob) if fs_dob != "غير متوفر" else True
                    )

                    # التقييم النهائي لحالة التطابق
                    if (
                        number_matched
                        and name_matched
                        and nat_matched
                        and dob_matched
                    ):
                        status = "✅ تطابق كامل"
                    elif number_matched and name_matched:
                        status = "⚠️ تطابق ناقص (اختلاف بيانات)"
                    else:
                        status = "❌ غير متطابق"

                    comparison_results.append(
                        {
                            "رقم القميص": num,
                            "تطابق الرقم": "✅" if number_matched else "❌",
                            "اسم السورس": p["name"],
                            "اسم Flashscore": fs_name,
                            "تطابق الاسم": "✅" if name_matched else "❌",
                            "جنسية السورس": p["nationality"],
                            "جنسية Flashscore": fs_nat,
                            "ID فلاش سكور": fs_id,
                            "حالة التطابق العامة": status,
                        }
                    )

                df_comp = pd.DataFrame(comparison_results)

                st.subheader(
                    f"📊 جدول نتائج المقارنة والتطابق المباشر ({'صاحب الأرض - Home' if is_home_team else 'الضيف - Away'})"
                )

                def highlight_status(val):
                    if "✅ تطابق كامل" in str(val):
                        return "background-color: #1e4620; color: white;"
                    elif "⚠️" in str(val):
                        return "background-color: #856404; color: white;"
                    else:
                        return "background-color: #721c24; color: white;"

                st.dataframe(
                    df_comp.style.map(
                        highlight_status, subset=["حالة التطابق العامة"]
                    ),
                    use_container_width=True,
                )
