import re
from fuzzywuzzy import fuzz
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="أداة مطابقة التشكيلات النصية", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة مطابقة التشكيلات (إدخال نصي + تحديد الفريق)")
st.write(
    "انسخ نص الجدول من نظامك الداخلي، وحدد ما إذا كان الفريق Home أم Away لمطابقة البيانات بدون أخطاء."
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. بيانات الفريق الداخلي (انسخ الجدول هنا)")
    raw_text = st.text_area(
        "انسخ محتوى الجدول بالكامل (Started & Bench) والصقه هنا:",
        height=260,
        placeholder="مثال:\n1 1040197 Florian Hellstern 2007-10-18 Germany\n27 28709 Gian-Luca Itter 1999-01-05 Germany...",
    )

    team_side = st.radio(
        "هذا الفريق يمثل في المباراة:",
        ["Home (صاحب الأرض)", "Away (الضيف)"],
        horizontal=True,
    )

with col2:
    st.subheader("2. رابط المباراة من Flashscore")
    flashscore_url = st.text_input(
        "أدخل رابط المباراة من Flashscore:",
        placeholder="https://www.flashscore.com/match/football/...",
    )
    st.info(
        "💡 تحديد (Home/Away) يضمن مطابقة القائمة بالفريق الصحيح داخل Flashscore وليس الفريق المنافس."
    )


def parse_pasted_text(text):
    """تفكيك النص المنسوخ من جدول النظام واستخراج البيانات بدقة"""
    players = []
    lines = text.strip().split("\n")
    current_section = "Started"

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # التمييز بين الأساسيين والبدلاء عند إيجاد العناوين
        if "Bench" in line:
            current_section = "Bench"
            continue
        elif "Started" in line:
            current_section = "Started"
            continue

        # تفكيك السطر المنسوخ (رقم القميص - ID - الاسم الكامل - تاريخ الميلاد - الجنسية)
        match = re.search(
            r"^(\d{1,2})\s+(\d+)\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s*(.*)$", line
        )
        if match:
            players.append(
                {
                    "number": int(match.group(1)),
                    "id": match.group(2),
                    "name": match.group(3).strip(),
                    "dob": match.group(4).strip(),
                    "nationality": match.group(5).strip(),
                    "type": (
                        "أساسي" if current_section == "Started" else "بديل"
                    ),
                }
            )
        else:
            # تجربة الفصل بـ Tab (\t) في حال نسخ الجدول مباشرة من المتصفح
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if len(parts) >= 4 and parts[0].isdigit():
                players.append(
                    {
                        "number": int(parts[0]),
                        "id": parts[1],
                        "name": parts[2],
                        "dob": parts[3],
                        "nationality": parts[4] if len(parts) > 4 else "",
                        "type": (
                            "أساسي" if current_section == "Started" else "بديل"
                        ),
                    }
                )

    return players


st.divider()

if st.button("🚀 ابدأ تحليل النص والمطابقة", type="primary", use_container_width=True):
    if not raw_text.strip():
        st.warning("⚠️ يرجى لصق نص الجدول في المربع أولاً!")
    else:
        # استخراج بيانات النص
        source_players = parse_pasted_text(raw_text)

        if not source_players:
            st.error(
                "❌ لم يتم التعرف على بنية النص! احرص على تحديد كامل الجدول بما فيه الرقم والاسم وتاريخ الميلاد."
            )
        else:
            st.success(
                f"✅ تم تحليل النص بنجاح واستخراج {len(source_players)} لاعباً من فئة ({team_side.split()[0]})!"
            )

            # تحويل البيانات لجدول منظم
            df = pd.DataFrame(source_players)

            df_display = df.rename(
                columns={
                    "number": "رقم القميص",
                    "id": "ID اللاعب",
                    "name": "اسم اللاعب الكامل",
                    "dob": "تاريخ الميلاد",
                    "nationality": "الجنسية",
                    "type": "المركز",
                }
            )

            st.subheader(
                f"📋 قائمة البيانات المستخرجة من نظامك الداخلي"
            )
            st.dataframe(df_display, use_container_width=True)
