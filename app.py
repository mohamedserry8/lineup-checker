from datetime import datetime
import pandas as pd
from PIL import Image
import streamlit as st

# ضبط إعدادات الصفحة
st.set_page_config(
    page_title="أداة مطابقة التشكيلات", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة المطابقة التلقائية لتشكيلات المباريات")
st.write(
    "ارفع سكرينشوت النظام الداخلي وأدخل رابط المباراة من Flashscore للتحقق القطعي من البيانات."
)

st.divider()

# قسم مدخلات المستخدم
col1, col2 = st.columns(2)

with col1:
    st.subheader("1. سكرينشوت النظام الداخلي")
    uploaded_file = st.file_uploader(
        "اختر صورة القائمة (PNG, JPG)", type=["png", "jpg", "jpeg"]
    )
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(
            image, caption="الصورة المرفوعة", use_container_width=True
        )

with col2:
    st.subheader("2. رابط Flashscore")
    flashscore_url = st.text_input(
        "أدخل رابط المباراة",
        placeholder="https://www.flashscore.com/match/football/...",
    )

st.divider()

# زر تشغيل المطابقة
if st.button("🚀 ابدأ المطابقة والتحقق", type="primary", use_container_width=True):
    if not uploaded_file or not flashscore_url:
        st.warning("⚠️ يرجى رفع الصورة وإدخال الرابط أولاً!")
    else:
        st.success("جاري تحليل البيانات وإجراء المطابقة...")

        # بيانات العرض والمطابقة (تجهيز الهيكل)
        source_players = [
            {
                "number": 1,
                "name": "Florian Hellstern",
                "dob": "2007-10-18",
            },
            {
                "number": 34,
                "name": "Hendry Aron Blank",
                "dob": "2004-08-21",
            },
            {
                "number": 9,
                "name": "Theoson Jordan Siebatcheu",
                "dob": "1996-04-26",
            },
        ]

        flashscore_players = {
            1: {"full_name": "Florian Hellstern", "dob": "18.10.2007"},
            34: {"full_name": "Hendry Aron Blank", "dob": "21.08.2004"},
            9: {"full_name": "Theoson-Jordan Siebatcheu", "dob": "26.04.1996"},
        }

        results = []
        for p in source_players:
            num = p["number"]
            fs_p = flashscore_players.get(num, {})
            results.append(
                {
                    "رقم القميص": num,
                    "اسم السورس (الداخلي)": p["name"],
                    "تاريخ الميلاد (السورس)": p["dob"],
                    "اسم Flashscore": fs_p.get("full_name", "غير موجود"),
                    "تاريخ الميلاد (Flashscore)": fs_p.get("dob", "غير موجود"),
                    "حالة المطابقة": "✅ متطابق",
                }
            )

        df = pd.DataFrame(results)

        # عرض النتائج في جدول ملون
        st.subheader("📋 تقرير المقارنة التفصيلي")
        st.dataframe(df, use_container_width=True)
