import streamlit as st
import os
from functools import partial

from cloud_listing_cache import get_listing, invalidate_cloud_listings

from tabs_scripts.upload_excel_to_gcs import (
    delete_uploaded_excel,
    download_uploaded_excel,
    list_uploaded_excels,
)

PAGE_SIZE = 5


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


def change_page(page):
    st.session_state['_source_excels_page'] = page


st.title("Source Excels")

try:
    excel_uploads = get_listing('_source_excel_listing', os.getenv('BUCKET_NAME'), list_uploaded_excels)
    listing_loaded = True
except Exception as e:
    st.error(f"❌ Error loading uploaded Excel files: {e}")
    excel_uploads = []
    listing_loaded = False

if listing_loaded and not excel_uploads:
    st.info("No source Excel files uploaded yet.")

total = len(excel_uploads)
page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(max(st.session_state.get('_source_excels_page', 0), 0), page_count - 1)
st.session_state['_source_excels_page'] = page
start = page * PAGE_SIZE
end = min(start + PAGE_SIZE, total)
if total:
    st.caption(f'Showing {start + 1}–{end} of {total} files · Newest first')

for item in excel_uploads[start:end]:
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
            st.download_button(
                "Download",
                data=partial(download_uploaded_excel, item["blob_name"]),
                on_click="ignore",
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
                    invalidate_cloud_listings()
                    st.success(f"Deleted {item['file_name']}")
                    rerun_app()
                except Exception as e:
                    st.error(f"❌ Error deleting {item['file_name']}: {e}")

if page_count > 1:
    st.divider()
    info, previous, following = st.columns([4, 1, 1], vertical_alignment='center')
    with info:
        st.caption(f'Page {page + 1} of {page_count} · Showing {start + 1}–{end} of {total} files')
    with previous:
        st.button('Previous', key='source_excels_previous', disabled=page == 0,
                  icon=':material/chevron_left:', use_container_width=True,
                  on_click=change_page, args=(page - 1,))
    with following:
        st.button('Next', key='source_excels_next', disabled=page == page_count - 1,
                  icon=':material/chevron_right:', type='primary', use_container_width=True,
                  on_click=change_page, args=(page + 1,))
