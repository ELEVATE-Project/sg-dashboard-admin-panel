import streamlit as st

from tabs_scripts.upload_excel_to_gcs import (
    delete_uploaded_excel,
    download_uploaded_excel,
    list_uploaded_excels,
)


def format_file_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"


def rerun_app():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


st.title("Source Excels")

try:
    excel_uploads = list_uploaded_excels()
except Exception as e:
    st.error(f"❌ Error loading uploaded Excel files: {e}")
    excel_uploads = []

if not excel_uploads:
    st.info("No source Excel files uploaded yet.")

for item in excel_uploads:
    col_name, col_uploaded, col_size, col_download, col_delete = st.columns([3, 2, 1, 1, 1])

    with col_name:
        st.write(item["file_name"])
        st.caption(item["gcs_path"])

    with col_uploaded:
        st.write(item["uploaded_at"])

    with col_size:
        st.write(format_file_size(item["size_bytes"]))

    with col_download:
        try:
            file_bytes = download_uploaded_excel(item["blob_name"])
            st.download_button(
                "Download",
                data=file_bytes,
                file_name=item["file_name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download_excel_{item['blob_name']}",
            )
        except Exception as e:
            st.error(f"Download failed: {e}")

    with col_delete:
        confirm_key = f"confirm_delete_excel_{item['blob_name']}"
        if st.button("Delete", key=f"delete_excel_{item['blob_name']}"):
            st.session_state[confirm_key] = True

        if st.session_state.get(confirm_key):
            st.warning(f"Delete {item['file_name']}?")
            confirm_col, cancel_col = st.columns(2)

            with confirm_col:
                confirm_delete = st.button("Confirm", key=f"confirm_btn_{item['blob_name']}")

            with cancel_col:
                cancel_delete = st.button("Cancel", key=f"cancel_btn_{item['blob_name']}")

            if cancel_delete:
                st.session_state[confirm_key] = False
                rerun_app()

            if confirm_delete:
                st.session_state[confirm_key] = False
                try:
                    delete_uploaded_excel(item["blob_name"])
                    st.success(f"Deleted {item['file_name']}")
                    rerun_app()
                except Exception as e:
                    st.error(f"❌ Error deleting {item['file_name']}: {e}")
