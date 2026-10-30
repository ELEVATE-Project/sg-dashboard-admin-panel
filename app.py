import streamlit as st
import importlib.util
from pathlib import Path
from admin_auth import require_login
from constants import ADMIN_SECTIONS

sections = [section["label"] for section in ADMIN_SECTIONS]

st.set_page_config(
    page_title="SG Dashboard Admin Panel", page_icon=":bar_chart:",
    layout="wide", initial_sidebar_state="collapsed"
)


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

# These rules react to the radio input in the browser, before the server rerun.
# Radio values are option indexes in the pinned Streamlit frontend.
navigation_css = '\n'.join(
    f'body:has(.st-key-admin_section input[value="{index}"]:checked) '
    f'[class*="st-key-admin_page_"]:not(.st-key-admin_page_{index}) '
    '{ display: none !important; }'
    for index in range(len(sections))
)
st.markdown(f'<style>{navigation_css}</style>', unsafe_allow_html=True)

# Send this before authentication and cloud requests. Streamlit can keep the old
# section's DOM during a rerun, even when a placeholder is emptied and refilled.
active_section = st.session_state.get('admin_section', sections[0])
active_index = sections.index(active_section) if active_section in sections else 0
section_changed = st.session_state.get('_rendered_admin_section') != active_section
transition_css = """
    [class*="st-key-admin_page_"][data-stale="true"],
    [data-stale="true"] [class*="st-key-admin_page_"],
    [class*="st-key-admin_page_"] [data-stale="true"] {
        visibility: hidden !important;
    }
""" if section_changed else ''
st.markdown(
    '<style>[class*="st-key-admin_page_"]:not('
    f'.st-key-admin_page_{active_index}) {{ display: none !important; }}'
    f'{transition_css}</style>',
    unsafe_allow_html=True,
)

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
# 🗂️ ADMIN SECTIONS
# =========================
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


# Replace the prior section immediately, including while cloud data is loading.
section = ADMIN_SECTIONS[sections.index(selected_section)]
st.session_state['_rendered_admin_section'] = selected_section
with st.container(key='admin_content'):
    with st.container(key=f'admin_page_{sections.index(selected_section)}'):
        st.markdown(f"### {section['heading']}")
        if section['caption']:
            st.caption(section['caption'])

        if section['script']:
            run_page(section['script'])
        else:
            from backend_voices_data.backend_data_uploader import CSV_TYPES, upload_to_gcp

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
