import streamlit as st
import os
import importlib.util
import uuid
import tempfile
from io import BytesIO
from cloud_listing_cache import invalidate_cloud_listings
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

        invalidate_cloud_listings()

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

def remember_selected_svg(widget_key):
    selected = st.session_state.get(widget_key)
    if selected is None:
        st.session_state.pop('_selected_svg_draft', None)
    else:
        st.session_state['_selected_svg_draft'] = {
            'name': selected.name, 'data': selected.getvalue()
        }


svg_file = st.file_uploader(
    "Choose SVG image", type=["svg"], key=file_key,
    on_change=remember_selected_svg, args=(file_key,),
)
if svg_file is not None:
    st.session_state['_selected_svg_draft'] = {
        'name': getattr(svg_file, 'name', 'image.svg'), 'data': svg_file.getvalue()
    }
elif st.session_state.get('_selected_svg_draft'):
    retained = st.session_state['_selected_svg_draft']
    svg_file = BytesIO(retained['data'])
    svg_file.name = retained['name']
    st.caption(f"Selected image: {svg_file.name}")

st.markdown(
    f"<style>.st-key-{name_key} input {{"
    "border: 1px solid #9ca3af; background-color: #ffffff; border-radius: 6px;"
    "}</style>",
    unsafe_allow_html=True,
)
image_name = st.text_input(
    "Enter image name", value=st.session_state.get("_svg_name_draft", ""),
    key=name_key, placeholder="Enter image name"
)

st.session_state["_svg_name_draft"] = image_name

if svg_file is not None and image_name.strip() and st.button("Upload SVG", key="upload_svg"):
    if svg_file is None:
        st.error("Choose an SVG image before uploading.")
    elif not image_name.strip():
        st.error("Enter an image name before uploading.")
    else:
        try:
            # Create a temporary file only when submitting, and always clean it up.
            with tempfile.TemporaryDirectory(prefix="sg-image-") as temp_dir:
                file_path = os.path.join(temp_dir, "upload.svg")
                with open(file_path, "wb") as output:
                    output.write(svg_file.getbuffer())
                success, result = upload_svg_from_local(file_path, image_name.strip())
        except Exception:
            st.error("Could not upload the image. Check cloud storage configuration and try again.")
        else:
            if success:
                invalidate_cloud_listings()
                st.session_state.pop("_selected_svg_draft", None)
                st.session_state.pop("_svg_name_draft", None)
                st.session_state.uploaded_svgs.append({
                    "id": str(uuid.uuid4()),
                    "name": image_name.strip(),
                    "blob_path": result["blob_path"],
                    "url": result["url"]
                })
                st.session_state.upload_session_id += 1
                st.session_state.removed_message = ":white_check_mark: SVG uploaded successfully"
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
