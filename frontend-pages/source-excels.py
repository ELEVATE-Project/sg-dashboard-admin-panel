import streamlit as st
import os
import logging
import re

from cloud_listing_cache import get_listing, invalidate_cloud_listings

from tabs_scripts.upload_excel_to_gcs import (
    delete_uploaded_excel,
    download_uploaded_excel,
    list_uploaded_excels,
)

PAGE_SIZE = 5
logger = logging.getLogger(__name__)


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
    st.session_state.pop('_source_excel_download', None)
    st.session_state.pop('_source_excel_download_error', None)

def prepare_download(blob_name, bucket_name):
    """Fetch before rendering so one action changes from Prepare to Download."""
    st.session_state.pop('_source_excel_download', None)
    st.session_state.pop('_source_excel_download_error', None)
    try:
        file_bytes = download_uploaded_excel(blob_name)
        if file_bytes is None:
            raise ValueError('The file is unavailable or cloud storage is not configured.')
        st.session_state['_source_excel_download'] = {
            'bucket': bucket_name, 'blob_name': blob_name, 'data': file_bytes,
        }
    except Exception as error:
        logger.exception('Could not prepare source Excel download')
        st.session_state['_source_excel_download_error'] = {
            'bucket': bucket_name, 'blob_name': blob_name, 'message': f'Download failed: {error}',
        }


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

download_scope = os.getenv('BUCKET_NAME')
prepared = st.session_state.get('_source_excel_download')
visible_names = {item['blob_name'] for item in excel_uploads[start:end]}
if prepared and (prepared['bucket'] != download_scope or prepared['blob_name'] not in visible_names):
    st.session_state.pop('_source_excel_download', None)

with st.container(key='source_excel_rows'):
    for item in excel_uploads[start:end]:
        with st.container(border=True):
            details, actions = st.columns([6, 3], vertical_alignment='center')
            with details:
                display_name = re.sub(r'^\d{8}T\d{6}Z_', '', item['file_name'])
                st.write(display_name)
                st.caption(f"Uploaded {item['uploaded_at']} · {format_file_size(item['size_bytes'])}")

            with actions:
                download, delete = st.columns([3, 2], gap='small')
                with download:
                    prepared = st.session_state.get('_source_excel_download')
                    if prepared and prepared['blob_name'] == item['blob_name']:
                        st.download_button(
                            'Download', data=prepared['data'], on_click='ignore',
                            file_name=item['file_name'],
                            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            key=f"download_excel_{item['blob_name']}", icon=':material/download:',
                            type='primary', use_container_width=True,
                        )
                    else:
                        st.button(
                            'Prepare', key=f"prepare_excel_{item['blob_name']}",
                            icon=':material/download:', use_container_width=True,
                            help='Prepare this workbook, then click Download to save it.',
                            on_click=prepare_download, args=(item['blob_name'], download_scope),
                        )

                with delete:
                    confirm_key = f"confirm_delete_excel_{item['blob_name']}"
                    if st.button('Delete', key=f"delete_excel_{item['blob_name']}", use_container_width=True):
                        st.session_state[confirm_key] = True

            error = st.session_state.get('_source_excel_download_error')
            if error and error['bucket'] == download_scope and error['blob_name'] == item['blob_name']:
                st.error(error['message'])

            if st.session_state.get(confirm_key):
                st.warning(f"Delete {item['file_name']}?")
                confirm_col, cancel_col, _ = st.columns([1, 1, 4])
                with confirm_col:
                    confirm_delete = st.button('Confirm', key=f"confirm_btn_{item['blob_name']}", use_container_width=True)
                with cancel_col:
                    cancel_delete = st.button('Cancel', key=f"cancel_btn_{item['blob_name']}", use_container_width=True)
                if cancel_delete:
                    st.session_state[confirm_key] = False
                    rerun_app()
                if confirm_delete:
                    st.session_state[confirm_key] = False
                    try:
                        delete_uploaded_excel(item['blob_name'])
                        prepared = st.session_state.get('_source_excel_download')
                        if prepared and prepared['blob_name'] == item['blob_name']:
                            st.session_state.pop('_source_excel_download', None)
                        invalidate_cloud_listings()
                        st.success(f"Deleted {item['file_name']}")
                        rerun_app()
                    except Exception as error:
                        st.error(f"❌ Error deleting {item['file_name']}: {error}")


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

if total:
    with st.expander('Storage details', expanded=False):
        st.code('\n'.join(item['gcs_path'] for item in excel_uploads[start:end]), language=None)
