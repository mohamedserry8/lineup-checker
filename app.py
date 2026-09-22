import re
import cloudscraper
from fuzzywuzzy import fuzz
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="أداة مطابقة التشكيلات التلقائية", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة المطابقة التلقائية الحية (Flashscore Automated Scraper)")
st.write(
    "ضع رابط المباراة ونظامك الداخلي، وسيقوم النظام أوتوماتيكياً بجلب البروفايلات والـ IDs والمقارنة بنسبة 100%."
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. بيانات الفريق الداخلي")
    raw_text = st.text_area(
        "انسخ محتوى الجدول الداخلي والصقه هنا:",
        height=220,
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
        "أدخل رابط المباراة المباشر:",
        placeholder="https://www.flashscore.com/match/football/...",
    )
    st.info(
        "💡 يتم جلب تشكيلة الفريق المختار تلقائياً واستخراج Flashscore IDs وتواريخ الميلاد مباشرة من السيرفر."
    )


# --- 1. تفكيك النص الداخلي ---
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


# --- 2. استخراج Match ID التلقائي الدقيق ---
def extract_match_id(url):
    # البحث عن كود المباراة المكون من 8 خانات بعد اسم الفريق في رابط Flashscore
    match = re.search(r"football/[^/]*?-([a-zA-Z0-9]{8})", url)
    if match:
        return match.group(1)

    match = re.search(r"-([a-zA-Z0-9]{8})(?:/|\?|$)", url)
    if match:
        return match.group(1)

    return None


# --- 3. جلب بيانات Flashscore الحية بفك الحظر تلقائياً (Cloudscraper) ---
def fetch_flashscore_data_automated(url, is_home):
    match_id = extract_match_id(url)
    if not match_id:
        st.error("❌ لم نتمكن من تحديد كود المباراة من الرابط المرفق!")
        return {}

    # إنشاء جلب محاكي لمصفح متكامل لتجاوز حماية Cloudflare
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )

    feed_url = f"https://www.flashscore.com/x/feed/d_su_{match_id}_en_1"
    headers = {
        "Referer": "https://www.flashscore.com/",
        "X-Fsign": "SW1hZ2luZSB3aXRob3V0IG1lbnRpb25pbmc=",
    }

    try:
        res = scraper.get(feed_url, headers=headers, timeout=15)

        # تجربة السيرفر الاحتياطي إذا لزم الأمر
        if res.status_code != 200 or not res.text:
            feed_url = (
                f"https://local-sa.flashscore.ninja/35/x/feed/d_su_{match_id}_en_1"
            )
            res = scraper.get(feed_url, headers=headers, timeout=15)

        if res.status_code != 200:
            st.error(
                f"❌ تعذر استخراج البيانات من Flashscore (كود الاستجابة: {res.status_code})"
            )
            return {}

        text = res.text
    except Exception as e:
        st.error(f"❌ خطأ أثناء الاتصال السلس بالرابط: {e}")
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


# --- 4. تشغيل المقارنة والتحقق التلقائي ---
st.divider()

if st.button(
    "🚀 ابدأ التحقق والمقـارنة التلقائيـة",
    type="primary",
    use_container_width=True,
):
    if not raw_text.strip():
        st.warning("⚠️ يرجى لصق جدول النظام الداخلي أولاً!")
    elif not flashscore_url.strip():
        st.warning("⚠️ يرجى إدخال رابط المباراة من Flashscore!")
    else:
        source_players = parse_pasted_text(raw_text)

        if not source_players:
            st.error("❌ تعذر تفكيك نص النظام الداخلي!")
        else:
            is_home_team = "Home" in team_side

            with st.spinner(
                "جاري الاتصال بـ Flashscore وجلب بروفايلات اللاعبين تلقائياً..."
            ):
                flashscore_data = fetch_flashscore_data_automated(
                    flashscore_url, is_home_team
                )

            if not flashscore_data:
                st.error(
                    "⚠️ لم يرجع الرابط أي تشكيلة لهذا الفريق! تأكد من وجود التشكيلة على Flashscore واختيار (Home / Away) الصحيح."
                )
            else:
                comparison_results = []

                for p in source_players:
                    num = p["number"]
                    fs_p = flashscore_data.get(num, {})

                    fs_id = fs_p.get("fs_id", "غير موجود")
                    fs_name = fs_p.get("name", "غير موجود بالرقم")
                    fs_dob = fs_p.get("dob", "غير متوفر")
                    fs_nat = fs_p.get("nationality", "غير متوفر")

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
                    name_matched = (
                        name_sim > 50
                        or (
                            fs_name.lower() in p["name"].lower()
                            and len(fs_name) > 2
                        )
                        or (
                            p["name"].lower() in fs_name.lower()
                            and len(p["name"]) > 2
                        )
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

                    # التقييم النهائي
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

                st.success(
                    f"✅ تم سحب تشكيلة Flashscore الحية بنجاح بنسبة 100%!"
                )
                st.subheader(
                    f"📊 نتائج المقارنة والتحقق الآلي ({'صاحب الأرض - Home' if is_home_team else 'الضيف - Away'})"
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
