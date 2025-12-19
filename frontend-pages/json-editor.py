import os
import json
import streamlit as st
from streamlit_ace import st_ace
import importlib.util

# -------------------------------
# Function to upload JSON to GCS
# -------------------------------
def upload_json_to_gcs(json_path, tab_name):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Dynamically import gcp_access
        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)

        # Set credentials if needed
        private_key_path = os.path.join(script_dir, "..", "private-key.json")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = private_key_path

        bucket_name = os.environ.get("BUCKET_NAME")
        if not bucket_name:
            st.error("❌ BUCKET_NAME environment variable not set.")
            return None

        file_name = tab_name.replace(" ", "-").lower() + ".json"
        destination_blob_name = f"sg-dashboard/{file_name}"

        folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
            bucket_name=bucket_name,
            source_file_path=json_path,
            destination_blob_name=destination_blob_name
        )

        if folder_url:
            st.success(f"✅ Successfully uploaded {json_path} to GCS: {folder_url}/{destination_blob_name}")
            return folder_url
        else:
            st.error(f"❌ Failed to upload {json_path} to GCS.")
            return None

    except Exception as e:
        st.error(f"❌ Exception uploading JSON to GCS: {e}")
        return None


# -------------------------------
# Function to download JSON from GCS
# -------------------------------
def download_json_from_gcs(tab_name):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))

        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)

        private_key_path = os.path.join(script_dir, "..", "private-key.json")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = private_key_path

        bucket_name = os.environ.get("BUCKET_NAME")
        if not bucket_name:
            return None

        file_name = tab_name.replace(" ", "-").lower() + ".json"
        blob_name = f"sg-dashboard/{file_name}"

        return gcp_access.download_json_from_gcs(
            bucket_name=bucket_name,
            blob_name=blob_name
        )

    except Exception:
        return None


# -------------------------------
# Streamlit App
# -------------------------------
st.title("JSON Editor & GCS Uploader")

json_tabs = [
    "landing page", "Community country view", "Community details page",
    "District view indicators", "Community led improvements page", "dashboard",
    "Network health", "State details page", "Voices from the ground"
]

JSON_DIR = "pages"
os.makedirs(JSON_DIR, exist_ok=True)
json_files = {name: f"{JSON_DIR}/{name.replace(' ', '-').lower()}.json" for name in json_tabs}

# Initialize session state for saved tabs and uploaded JSON
if "saved_tabs" not in st.session_state:
    st.session_state.saved_tabs = {name: False for name in json_tabs}

if "uploaded_data" not in st.session_state:
    st.session_state.uploaded_data = {name: None for name in json_tabs}

# Create tabs in Streamlit
tabs = st.tabs(json_tabs)

for i, name in enumerate(json_tabs):
    file_path = json_files[name]
    with tabs[i]:
        st.subheader(f"🗂 {name} JSON")

        # -------------------------------
        # Load JSON for editor (GCS first, then local)
        # -------------------------------
        gcs_json = download_json_from_gcs(name)

        if gcs_json is not None:
            json_text = json.dumps(gcs_json, indent=2)

        elif os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    json_data = json.load(f)
                    json_text = json.dumps(json_data, indent=2)
                except json.JSONDecodeError:
                    st.warning("⚠️ File contains invalid JSON.")
                    f.seek(0)
                    json_text = f.read()
        else:
            json_text = "{}"

        # JSON editor
        edited_json = st_ace(
            value=json_text,
            language="json",
            theme="monokai",
            key=f"json_editor_{i}",
            height=400,
            font_size=14,
        )

        col1, col2 = st.columns([1, 1])

        # -------------------------------
        # Save button
        # -------------------------------
        with col1:
            if st.button(f"💾 Save {name}", key=f"save_btn_{i}"):
                try:
                    parsed = json.loads(edited_json)
                    with open(file_path, "w") as f:
                        json.dump(parsed, f, indent=2)
                    st.success(f"✅ {name} JSON saved successfully!")
                    st.session_state.saved_tabs[name] = True
                except json.JSONDecodeError as e:
                    st.error(f"❌ Invalid JSON: {e}")

        # -------------------------------
        # Upload button: show only if saved
        # -------------------------------
        if st.session_state.saved_tabs.get(name, False):
            with col2:
                if st.button(f"⬆️ Upload {name}", key=f"upload_btn_{i}"):
                    try:
                        parsed = json.loads(edited_json)
                        with open(file_path, "w") as f:
                            json.dump(parsed, f, indent=2)

                        folder_url = upload_json_to_gcs(file_path, name)
                        if folder_url:
                            st.session_state.uploaded_data[name] = parsed
                    except json.JSONDecodeError as e:
                        st.error(f"❌ Invalid JSON: {e}")

        # -------------------------------
        # Show uploaded JSON if available
        # -------------------------------
        uploaded = st.session_state.uploaded_data.get(name)
        if uploaded:
            st.subheader(f"📦 Uploaded {name} JSON")
            st.json(uploaded)
