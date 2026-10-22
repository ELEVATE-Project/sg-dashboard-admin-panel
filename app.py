import streamlit as st
import importlib.util
from pathlib import Path
from admin_auth import require_login

st.set_page_config(
    page_title="SG Dashboard Admin Panel", page_icon=":bar_chart:",
    layout="wide", initial_sidebar_state="collapsed"
)


st.markdown("""
<style>
    [data-testid="stHeader"] { display: none; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }

    [data-testid="stElementContainer"][data-stale="true"] {
        opacity: 1 !important;
        filter: none !important;
        transition: none !important;
    }

    h1 {
        color: #2e7d32;
        font-size: 36px;
        font-weight: bold;
    }

    .stTabs [data-baseweb="tab"] {
        font-size: 18px !important;
        color: #1565c0 !important;
        font-weight: bold;
    }

    .stTabs [aria-selected="true"] {
        border-bottom: 4px solid #2e7d32 !important;
        color: #2e7d32 !important;
    }




    [data-testid="stMainBlockContainer"] {
        max-width: 1440px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .st-key-admin_section [role="radiogroup"] {
        gap: 0.5rem 1rem;
        border-bottom: 1px solid #dfe5df;
        flex-wrap: wrap;
    }
    .st-key-admin_section [data-baseweb="radio"] > div:first-child {
        display: none;
    }
    .st-key-admin_section [data-baseweb="radio"] {
        margin: 0;
        padding: 0.65rem 0.25rem;
        border-bottom: 3px solid transparent;
        color: #1565c0;
    }
    .st-key-admin_section [data-baseweb="radio"]:has(input:checked) {
        border-bottom-color: #2e7d32;
        color: #2e7d32;
        font-weight: 600;
    }
    .st-key-admin_section [data-baseweb="radio"]:has(input:focus-visible) {
        outline: 2px solid #2e7d32;
        outline-offset: 2px;
    }
</style>
""", unsafe_allow_html=True)

require_login()

# =========================
# 🧭 HEADER
# =========================
# st.title("SG Dashboard Admin Panel")
# st.image("main_logo.svg", caption="Shikshagraha Dashboard", use_container_width=True)


col1, col2 = st.columns([1, 4], vertical_alignment="center")

with col1:
    st.image("main_logo.svg", width=200)  # control logo size

with col2:
    st.markdown(
        "<h1>SG Dashboard Admin Panel</h1>",
        unsafe_allow_html=True
    )

# =========================
# 🗂️ TWO MAIN TABS
# =========================
sections = [
    "📁 File Upload Dashboard",
    "🧩 JSON Editor Section",
    "Upload image from local device",
    "Handle all images",
    "Source Excels",
    "Backend Data Uploader"
]
selected_section = st.radio(
    "Admin sections", sections, index=0, horizontal=True,
    key="admin_section", label_visibility="collapsed"
)


# =========================
# ✅ UTILITY: Load Page as Module
# =========================
def run_page(script_path: str):
    """Dynamically import and run a Streamlit page file."""
    script_path = Path(script_path)
    if not script_path.exists():
        st.error(f"❌ File not found: {script_path}")
        return

    # Create a unique module name per script (avoid caching)
    module_name = script_path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Run if page has a main() function, else run on import
    if hasattr(module, "main"):
        module.main()


# =========================
# TAB 1 → File Upload Page
# =========================
if selected_section == sections[0]:
    st.markdown("### 📤 Upload & Manage Data Files")
    st.caption("Upload your Excel/CSV/TXT files and process them with one click.")
    run_page("frontend-pages/file-upload.py")

# =========================
# TAB 2 → JSON Editor Page
# =========================
elif selected_section == sections[1]:
    st.markdown("### 🧩 Manage JSON Data Files")
    st.caption("Edit and save JSON files for landing pages, network data, and more.")
    run_page("frontend-pages/json-editor.py")



elif selected_section == sections[2]:
    st.markdown("### 🧩 Upload image from local device")
    st.caption("Upload svg images and get url of gcs")
    run_page("frontend-pages/upload-images.py")


elif selected_section == sections[3]:
    st.markdown("### 🖼️ Manage Images")
    st.caption("View and manage uploaded images.")
    run_page("frontend-pages/handle-all-images.py")


elif selected_section == sections[4]:
    st.markdown("### 📦 Source Excels")
    st.caption("View uploaded Excel source files and delete old ones.")
    run_page("frontend-pages/source-excels.py")

# =========================
# TAB 6 → Backend Data Uploader
# =========================

elif selected_section == sections[5]:
    from backend_voices_data.backend_data_uploader import CSV_TYPES, upload_to_gcp

    st.markdown("### 📤 Upload Your Backend CSV File")
    
    col1, col2 = st.columns(2)
    
    with col1:
        csv_type = st.selectbox(
            "Select Data Type *", 
            options=["Select..."] + CSV_TYPES,
            index=0,
            help="Choose the category of data you are uploading."
        )
        

    # File Uploader
    uploaded_file = st.file_uploader("Upload CSV File *", type=['csv'], key="backend_uploader")

    # Submit Button
    if st.button("Submit Upload", key="submit_backend"):
        if csv_type == "Select...":
            st.error("Please select a valid data type.", icon="🚨")
        elif uploaded_file is None:
            st.error("Please upload a file.", icon="🚨")
        else:
            # Upload
            with st.spinner("Uploading to GCP and processing..."):
                success, message = upload_to_gcp(uploaded_file, csv_type)

            if success:
                st.success(message or "JSON file uploaded successfully.", icon="✅")
                st.balloons()
            else:
                st.error(message or "Failed to upload JSON file.", icon="❌")
