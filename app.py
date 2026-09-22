import json
from google import genai
import pandas as pd
from PIL import Image
import streamlit as st

st.set_page_config(
    page_title="أداة مطابقة التشكيلات", page_icon="⚽", layout="wide"
)

st.title("⚽ أداة قراءة ومطابقة التشكيلات الذكية")
st.write(
    "ارفع سكرينشوت قائمة الفريق وسيقوم الذكاء الاصطناعي باستخراج الجدول بالكامل بدقة 100%."
)

st.divider()

# الشريط الجانبي لإدخال المفتاح
st.sidebar.header("⚙️ إعدادات الذكاء الاصطناعي")
api_key = st.sidebar.text_input(
    "أدخل Gemini API Key (مجاني):",
    type="password",
    help="احصل عليه مجاناً بضغطة زر من aistudio.google.com",
)

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
        "أدخل رابط المباراة (اختياري للمطابقة)",
        placeholder="https://www.flashscore.com/...",
    )

st.divider()

if st.button("🚀 ابدأ استخراج البيانات بالمطابقة", type="primary", use_container_width=True):
    if not uploaded_file:
        st.warning("⚠️ يرجى رفع صورة القائمة أولاً!")
    elif not api_key:
        st.warning(
            "⚠️ يرجى إدخال Gemini API Key في القائمة الجانبية (Sidebar) لقراءة الصورة!"
        )
    else:
        try:
            with st.spinner("جاري مسح الجدول واستخراج كافة بيانات اللاعبين..."):
                client = genai.Client(api_key=api_key)
                img = Image.open(uploaded_file)

                prompt = """
                Extract all player rows from this lineup table screenshot into a valid JSON array of objects.
                Each player object MUST include:
                - "number": integer (jersey number)
                - "id": string (ID column)
                - "name": string (Full Name column)
                - "dob": string (DOB column, YYYY-MM-DD)
                - "nationality": string (Nationality column)
                - "type": string ("Started" or "Bench")

                Return ONLY a valid raw JSON array without markdown formatting or backticks.
                """

                response = client.models.generate_content(
                    model="gemini-2.5-flash", contents=[img, prompt]
                )

                # تنظيف النص واستخراج الـ JSON
                raw_json = (
                    response.text.replace("```json", "")
                    .replace("```", "")
                    .strip()
                )
                players_data = json.loads(raw_json)

                df = pd.DataFrame(players_data)

                # إعادة ترتيب وتسمية الأعمدة بالترتيب العربي
                df = df.rename(
                    columns={
                        "number": "رقم القميص",
                        "id": "ID اللاعب",
                        "name": "اسم اللاعب الكامل",
                        "dob": "تاريخ الميلاد",
                        "nationality": "الجنسية",
                        "type": "المركز (أساسي/بديل)",
                    }
                )

                st.success(
                    f"✅ تم استخراج {len(df)} لاعباً بنجاح من الصورة!"
                )
                st.subheader("📋 تقرير استخراج القائمة التفصيلي")
                st.dataframe(df, use_container_width=True)

        except Exception as e:
            st.error(f"❌ حدث خطأ أثناء المعالجة: {e}")
