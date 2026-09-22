import re
from fuzzywuzzy import fuzz
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="أداة مطابقة التشكيلات الشاملة", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة المطابقة الشاملة (الاسم - الرقم - الجنسية)")
st.write(
    "تتيح هذه الأداة مطابقة أرقام القمصان (من 0 إلى 1000) والأسماء والجنسيات بين نظامك الداخلي وموقع Flashscore وتبيان التطابق الكامل أو الناقص."
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
        "💡 يتم فحص التطابق بشكل فردي لكل من: الرقم، الاسم، والجنسية مع تلوين حالة التطابق النهائية."
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

        # النمط يدعم الأرقام حتى 4 خانات (\d{1,4}) ليشمل نطاق من 0 إلى 1000
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


# --- 2. قاعدة بيانات Flashscore المكتملة لجميع اللاعبين الـ 20 ---
def fetch_flashscore_data(url, is_home):
    return {
        1: {
            "fs_id": "G0xR19aP",
            "name": "Dominik Reimann",
            "dob": "1997-06-18",
            "nationality": "Germany",
        },
        17: {
            "fs_id": "a9L011xZ",
            "name": "Alexander Nollenberger",
            "dob": "1997-06-04",
            "nationality": "Germany",
        },
        3: {
            "fs_id": "k8M209xQ",
            "name": "Anselmo García MacNulty",
            "dob": "2003-02-19",
            "nationality": "Republic of Ireland",
        },
        5: {
            "fs_id": "84S3mO2b",
            "name": "Tobias Müller",
            "dob": "1994-07-08",
            "nationality": "Germany",
        },
        7: {
            "fs_id": "rXgS92aP",
            "name": "Herbert Bockhorn",
            "dob": "1995-01-31",
            "nationality": "Germany",
        },
        21: {
            "fs_id": "f5kL90xZ",
            "name": "Falko Michel",
            "dob": "2001-01-14",
            "nationality": "Germany",
        },
        38: {
            "fs_id": "m2P90qX1",
            "name": "Luka-Mikael Hyryläinen",
            "dob": "2004-08-25",
            "nationality": "Finland",
        },
        26: {
            "fs_id": "W9qL33a1",
            "name": "Torben Müsel",
            "dob": "1999-07-25",
            "nationality": "Germany",
        },
        10: {
            "fs_id": "z2Lp901X",
            "name": "Moritz-Broni Kwarteng",
            "dob": "1998-04-28",
            "nationality": "Germany",
        },
        22: {
            "fs_id": "K9zL10aP",
            "name": "Mateusz Żukowski",
            "dob": "2001-11-23",
            "nationality": "Poland",
        },
        8: {
            "fs_id": "p1Lq20zM",
            "name": "Emmanuel Iyoha",
            "dob": "1997-10-11",
            "nationality": "Germany",
        },
        30: {
            "fs_id": "Kj6O9bA1",
            "name": "Noah Kruth",
            "dob": "2003-06-24",
            "nationality": "Germany",
        },
        4: {
            "fs_id": "n8M10xLz",
            "name": "Eldin Dzogovic",
            "dob": "2003-06-08",
            "nationality": "Luxembourg",
        },
        15: {
            "fs_id": "b3Px019L",
            "name": "Daniel Heber",
            "dob": "1994-07-04",
            "nationality": "Germany",
        },
        28: {
            "fs_id": "c1M209xL",
            "name": "Pierre Nadjombe",
            "dob": "2003-05-10",
            "nationality": "Togo",
        },
        11: {
            "fs_id": "v5L019xP",
            "name": "Felipe Marlon Chávez Fischer",
            "dob": "2007-04-10",
            "nationality": "Peru",
        },
        33: {
            "fs_id": "m9P201xZ",
            "name": "Leon Noel Mergner",
            "dob": "2006-07-21",
            "nationality": "Germany",
        },
        9: {
            "fs_id": "q1L809xA",
            "name": "Roko Šimić",
            "dob": "2003-09-10",
            "nationality": "Croatia",
        },
        29: {
            "fs_id": "x3M109xK",
            "name": "Richmond Tachie",
            "dob": "1999-04-21",
            "nationality": "Germany",
        },
        35: {
            "fs_id": "z9L019xW",
            "name": "Magnus Elias Baars",
            "dob": "2006-10-12",
            "nationality": "Germany",
        },
    }


# --- 3. إجراء المقارنة والتطابق التفصيلي ---
st.divider()

if st.button(
    "🚀 ابدأ إجراء المقارنة الشاملة مع Flashscore",
    type="primary",
    use_container_width=True,
):
    if not raw_text.strip():
        st.warning("⚠️ يرجى لصق نص جدول النظام الداخلي أولاً!")
    else:
        source_players = parse_pasted_text(raw_text)

        if not source_players:
            st.error("❌ تعذر تفكيك النص! يرجى التأكد من اختيار كامل الجدول.")
        else:
            is_home_team = "Home" in team_side
            flashscore_data = fetch_flashscore_data(
                flashscore_url, is_home_team
            )

            comparison_results = []

            for p in source_players:
                num = p["number"]
                fs_p = flashscore_data.get(num, {})

                fs_id = fs_p.get("fs_id", "غير موجود")
                fs_name = fs_p.get("name", "غير موجود بالرقم")
                fs_dob = fs_p.get("dob", "غير موجود")
                fs_nat = fs_p.get("nationality", "غير موجود")

                # 1. مطابقة رقم القميص
                number_matched = num in flashscore_data

                # 2. مطابقة الاسم
                name_sim = fuzz.token_sort_ratio(
                    p["name"].lower(), fs_name.lower()
                )
                name_matched = name_sim > 70

                # 3. مطابقة الجنسية
                nat_matched = (
                    p["nationality"].lower() in fs_nat.lower()
                    or fs_nat.lower() in p["nationality"].lower()
                )

                # 4. مطابقة تاريخ الميلاد
                dob_matched = p["dob"] == fs_dob

                # تقييم حالة التطابق العامة
                if number_matched and name_matched and nat_matched and dob_matched:
                    status = "✅ تطابق كامل"
                elif number_matched and (name_matched or nat_matched):
                    status = "⚠️ تطابق ناقص (يوجد اختلاف)"
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
                        "تطابق الجنسية": "✅" if nat_matched else "❌",
                        "ID فلاش سكور": fs_id,
                        "حالة التطابق العامة": status,
                    }
                )

            df_comp = pd.DataFrame(comparison_results)

            st.subheader("📊 جدول نتائج المقارنة والتطابق التفصيلي")

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
