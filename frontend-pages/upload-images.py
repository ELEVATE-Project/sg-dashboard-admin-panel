import streamlit as st
import os
import importlib.util
import uuid
from tabs_scripts.upload_image_from_device import upload_svg_from_local

# -------------------------------
# Session state initialization
# -------------------------------
if "uploaded_svgs" not in st.session_state:
    st.session_state.uploaded_svgs = []  # List of dicts: {"id", "name", "blob_path", "url"}
if "upload_session_id" not in st.session_state:
    st.session_state.upload_session_id = 0
if "remove_queue" not in st.session_state:
    st.session_state.remove_queue = None  # Store image ID to remove
if "removed_message" not in st.session_state:
    st.session_state.removed_message = ""  # Top-level success message

# -------------------------------
# Perform removal if queued
# -------------------------------
if st.session_state.remove_queue:
    remove_id = st.session_state.remove_queue
    try:
        svg_to_remove = next(s for s in st.session_state.uploaded_svgs if s["id"] == remove_id)

        # Delete from GCS
        script_dir = os.path.dirname(os.path.abspath(__file__))
        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)
        gcp_access.delete_file_from_gcs(
            bucket_name=os.environ.get("BUCKET_NAME"),
            blob_name=svg_to_remove["blob_path"],
        )

        # Remove from session
        st.session_state.uploaded_svgs = [s for s in st.session_state.uploaded_svgs if s["id"] != remove_id]
        st.session_state.removed_message = f":wastebasket: {svg_to_remove['name']} removed successfully"
    except Exception as e:
        st.error(f":x: Could not remove SVG: {e}")
    finally:
        st.session_state.remove_queue = None  # Clear queue

# -------------------------------
# Show removal message
# -------------------------------
if st.session_state.removed_message:
    st.success(st.session_state.removed_message)
    st.session_state.removed_message = ""  # Reset message

st.subheader("SVG Upload Dashboard")

# -------------------------------
# Upload SVG section
# -------------------------------
file_key = f"svg_file_{st.session_state.upload_session_id}"
name_key = f"image_name_{st.session_state.upload_session_id}"

svg_file = st.file_uploader("Choose SVG image", type=["svg"], key=file_key)
image_name = st.text_input("Enter image name", value="", key=name_key)

if svg_file and image_name.strip():
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, svg_file.name)

    with open(file_path, "wb") as f:
        f.write(svg_file.getbuffer())

    if st.button("Upload SVG"):
        success, result = upload_svg_from_local(file_path, image_name)
        if success:
            st.session_state.uploaded_svgs.append({
                "id": str(uuid.uuid4()),
                "name": image_name,
                "blob_path": result["blob_path"],
                "url": result["url"]
            })
            st.session_state.upload_session_id += 1
            try:
                os.remove(file_path)
            except Exception as e:
                st.warning(f"Could not delete temp file: {e}")
            st.success(":white_check_mark: SVG uploaded successfully")
            st.rerun()
        else:
            st.error(f"Upload failed: {result}")

# -------------------------------
# Display all uploaded SVGs with remove buttons
# -------------------------------
if st.session_state.uploaded_svgs:
    st.markdown("### Uploaded SVGs")
    for svg in st.session_state.uploaded_svgs:
        with st.container():
            cols = st.columns([5, 1])
            with cols[0]:
                st.markdown(f"**{svg['name']}**")
                st.code(svg["url"])
            with cols[1]:
                if st.button("Remove", key=f"remove_{svg['id']}"):
                    # Queue removal instead of immediate rerun
                    st.session_state.remove_queue = svg["id"]
                    st.rerun()
