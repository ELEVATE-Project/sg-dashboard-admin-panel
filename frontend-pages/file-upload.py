import streamlit as st
import pandas as pd
import re

# ✅ Import script functions
from tabs_scripts.community_led_details import community_led_programs_sum_with_codes, pie_chart_community_led
from tabs_scripts.goals import goals
from tabs_scripts.key_progress_indicators import key_progress_indicators
from tabs_scripts.line_chart import extract_district_line_chart, extract_micro_improvements, extract_state_line_chart, update_voices_json_line_chart, update_leaders_engaged_dual_axis_chart
from tabs_scripts.network_map_data import get_network_map_data
from tabs_scripts.partners import get_partners
from tabs_scripts.extract_state_details import update_district_view_indicators
from tabs_scripts.pie_chart import pie_chart
from tabs_scripts.testimonials import testimonials
from tabs_scripts.programs import generate_program_reports
from tabs_scripts.extract_district_details import extract_district_details
from tabs_scripts.extract_community_details import extract_community_details , delete_district_community_pie_charts
from tabs_scripts.upload_images_from_excel import upload_images_from_excel
from tabs_scripts.upload_excel_to_gcs import upload_excel_to_gcs
from tabs_scripts.voices_tab_big_numbers import voices_tab_big_numbers

from constants import ALLOWED_TABS as allowed_tabs


# ✅ Utility: Clean DataFrame for display
def sanitize_dataframe(df):
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].astype(str)
    return df

# ✅ Normalize sheet names to match (remove spaces/symbols and lowercase)
def normalize_name(name):
    return re.sub(r'[^a-z0-9]', '', name.strip().lower())


# ✅ Map sheet names to upload processing functions
upload_actions = {
    "Data on homepage": key_progress_indicators,
    "Dashboard first page": pie_chart,
    "Goals": goals,
    "States details": update_district_view_indicators,
    "District Details": extract_district_details,
    "Programs": generate_program_reports,
    "Micro improvements progress": extract_micro_improvements,
    "Graph_VoiceTab_MI": update_voices_json_line_chart,
    "Partners": get_partners,
    "Network Map": get_network_map_data,
    "Testimonials": testimonials,
    "Imagesicons": upload_images_from_excel,
    "Voices Tab Big Numbers": voices_tab_big_numbers
}

# ✅ Create a mapping of normalized names → clean display names
normalized_to_display = {normalize_name(name): name for name in allowed_tabs}

# ✅ Streamlit Page Setup
st.set_page_config(page_title="File Upload App", page_icon=":page_facing_up:")

# ✅ Custom Styling
st.markdown("""
<style>
    [data-testid="stHeader"] { display: none; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }

    h1 {
        color: #2e7d32;
        font-size: 36px;
        font-weight: bold;
    }

    .stFileUploader {
        border: 2px dashed #4caf50;
        padding: 20px;
        border-radius: 10px;
        background-color: #e8f5e9;
    }

    .stTabs [data-baseweb="tab"] {
        font-size: 18px;
        color: #1565c0;
    }

    .stDataFrame th {
        background-color: #dcedc8;
        font-weight: bold;
    }

    .st-emotion-cache-1w723zb {
        padding:0;
        max-width:90%;
        display:flex;
        justify-content:center;
    }

    .st-emotion-cache-pa57uv {
        width:80%;
        align-items:center;
    }

    .st-emotion-cache-pa57uv > img {
        width:236px !important;
        max-width:100% !important;
    }
</style>
""", unsafe_allow_html=True)

# ✅ File Upload Input
uploaded_file = st.file_uploader("Choose a file", type=["csv", "txt", "xlsx"])

# ✅ Main logic
if uploaded_file is not None:
    st.success("✅ File uploaded successfully!")

    try:
        file_type = uploaded_file.name.split(".")[-1]

        # ✅ Handle CSV preview
        if file_type == 'csv':
            df = pd.read_csv(uploaded_file)
            st.subheader("📄 Preview of CSV File")
            st.dataframe(sanitize_dataframe(df), height=400)

        # ✅ Handle TXT preview
        elif file_type == 'txt':
            df = pd.read_csv(uploaded_file, delimiter="\t")
            st.subheader("📄 Preview of TXT File")
            st.dataframe(sanitize_dataframe(df), height=400)

        # ✅ Handle XLSX preview and tab-wise uploads
        elif file_type == 'xlsx':
            excel_upload_key = (
                uploaded_file.name,
                uploaded_file.size,
                getattr(uploaded_file, "file_id", None)
            )
            if st.session_state.get("uploaded_excel_gcs_key") != excel_upload_key:
                uploaded_excel_details = upload_excel_to_gcs(uploaded_file)
                st.session_state["uploaded_excel_gcs_key"] = excel_upload_key
                st.session_state["uploaded_excel_gcs_path"] = (
                    uploaded_excel_details["gcs_path"]
                    if uploaded_excel_details
                    else None
                )

            if st.session_state.get("uploaded_excel_gcs_path"):
                st.info(f"📦 Source Excel stored at {st.session_state['uploaded_excel_gcs_path']}")

            excel_data = pd.read_excel(uploaded_file, sheet_name=None)
            sheet_names = list(excel_data.keys())

            # ✅ Normalize sheet names for matching
            visible_sheet_names = []
            sheet_to_display = {}  # Map Excel sheet → clean display name
            for name in sheet_names:
                norm = normalize_name(name)
                if norm in normalized_to_display:
                    visible_sheet_names.append(name)
                    sheet_to_display[name] = normalized_to_display[norm]

            if visible_sheet_names:
                tabs = st.tabs([sheet_to_display[name] for name in visible_sheet_names])

                for i, sheet_name in enumerate(visible_sheet_names):
                    display_name = sheet_to_display[sheet_name]
                    with tabs[i]:
                        st.markdown(f"### Sheet: {display_name}")
                        cleaned_df = sanitize_dataframe(excel_data[sheet_name])
                        st.dataframe(cleaned_df, height=400)

                        # 🔽 Sub-tab for upload
                        with st.expander(f"📤 Upload `{display_name}`", expanded=False):
                            if st.button(f"Upload {display_name}", key=f"upload_btn_{display_name}"):
                                try:
                                    with st.status(f"🔄 Uploading `{display_name}`...", expanded=True) as status:

                                        # ✅ Find upload function using normalized names
                                        norm_sheet = normalize_name(sheet_name)
                                        upload_function = None
                                        for key, func in upload_actions.items():
                                            if normalize_name(key) == norm_sheet:
                                                upload_function = func
                                                break

                                        if upload_function:
                                            upload_function(uploaded_file)
                                            status.update(label=f"✅ `{display_name}` uploaded successfully!", state="complete")
                                        else:
                                            status.update(label=f"⚠️ No function mapped for `{display_name}`", state="error")
                                except Exception as e:
                                    st.error(f"❌ Error uploading `{display_name}`: {e}")
            else:
                st.warning("⚠️ No allowed sheets found to preview.")

        else:
            st.error("❌ Unsupported file format.")
            st.stop()

        # ✅ Upload All Files Button (Placed at Bottom)
        st.markdown("---")
        if st.button("🚀 Upload all files"):
            try:
                with st.status("🔄 Uploading and processing all allowed sheets...", expanded=True) as status:
                    key_progress_indicators(uploaded_file)
                    get_partners(uploaded_file)
                    get_network_map_data(uploaded_file)
                    update_district_view_indicators(uploaded_file)
                    extract_district_details(uploaded_file)
                    pie_chart(uploaded_file)
                    testimonials(uploaded_file)
                    pie_chart_community_led(uploaded_file)   #commenting this line as per phase enhancement 1
                    community_led_programs_sum_with_codes(uploaded_file)
                    generate_program_reports(uploaded_file)
                    extract_community_details(uploaded_file)
                    extract_micro_improvements(uploaded_file)
                    upload_images_from_excel(uploaded_file)
                    voices_tab_big_numbers(uploaded_file)
                    update_voices_json_line_chart(uploaded_file)
                    update_leaders_engaged_dual_axis_chart(uploaded_file)
                    goals(uploaded_file)
                    status.update(label="✅ All files uploaded successfully!", state="complete")
            except Exception as e:
                st.error(f"❌ Error during full upload: {e}")

    except Exception as e:
        st.error(f"❌ Error processing file: {e}")
