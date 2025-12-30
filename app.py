import streamlit as st
import importlib.util
from pathlib import Path

# =========================
# 🎨 PAGE CONFIG & STYLING
# =========================
# st.set_page_config(
#     page_title="SG Dashboard Admin Panel",
#     page_icon=":bar_chart:",
#     layout="wide"
# )

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

    .stTabs [data-baseweb="tab"] {
        font-size: 18px !important;
        color: #1565c0 !important;
        font-weight: bold;
    }

    .stTabs [aria-selected="true"] {
        border-bottom: 4px solid #2e7d32 !important;
        color: #2e7d32 !important;
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

    .st-emotion-cache-3uj0rx h1 {
      padding:0 !important;
    }
</style>
""", unsafe_allow_html=True)

# =========================
# 🧭 HEADER
# =========================
# st.title("SG Dashboard Admin Panel")
# st.image("main_logo.svg", caption="Shikshagraha Dashboard", use_container_width=True)


col1, col2 = st.columns([1, 4])  # adjust width ratio

with col1:
    st.image("main_logo.svg", width=200)  # control logo size

with col2:
    st.markdown(
        "<h1 style='margin-top: 20px;'>SG Dashboard Admin Panel</h1>",
        unsafe_allow_html=True
    )

# =========================
# 🗂️ TWO MAIN TABS
# =========================
tabs = st.tabs(["📁 File Upload Dashboard", "🧩 JSON Editor Section", "Upload image from local device","Handle all images", "Backend Data Uploader"])


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
with tabs[0]:
    st.markdown("### 📤 Upload & Manage Data Files")
    st.caption("Upload your Excel/CSV/TXT files and process them with one click.")
    run_page("frontend-pages/file-upload.py")

# =========================
# TAB 2 → JSON Editor Page
# =========================
with tabs[1]:
    st.markdown("### 🧩 Manage JSON Data Files")
    st.caption("Edit and save JSON files for landing pages, network data, and more.")
    run_page("frontend-pages/json-editor.py")



with tabs[2]:
    st.markdown("### 🧩 Upload image from local device")
    st.caption("Upload svg images and get url of gcs")
    run_page("frontend-pages/upload-images.py")


with tabs[3]:
    st.markdown("### 🧩 Upload image from local device")
    st.caption("Upload svg images and get url of gcs")
    run_page("frontend-pages/handle-all-images.py")

# =========================
# TAB 4 → Backend Data Uploader
# =========================
from tabs_scripts.backend_data_uploader import CSV_TYPES, upload_to_gcp

with tabs[4]:
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
            st.error("Please select a valid Data Type.", icon="🚨")
        elif uploaded_file is None:
            st.error("Please upload a file.", icon="🚨")
        else:
            # 3. Upload
            with st.spinner("Uploading to GCP and Processing..."):
                success, message = upload_to_gcp(uploaded_file, csv_type)
                
            if success:
                st.success("json file uploaded sucessfully", icon="✅")
                st.balloons()
            else:
                st.error("failed to upload json", icon="❌")

