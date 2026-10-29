import importlib.util
import logging
import mimetypes
import os
import time
from functools import partial
from pathlib import Path

import streamlit as st

from constants import BUCKET_PREFIX_FOR_IMAGES
from cloud_listing_cache import get_listing, invalidate_cloud_listings


ROOT = Path(__file__).resolve().parents[1]
PAGE_SIZE = 10
logger = logging.getLogger(__name__)


def load_gcp_access():
    """Use the same .env loading and credentials as image uploads."""
    path = ROOT / 'cloud-scripts' / 'gcp_access.py'
    spec = importlib.util.spec_from_file_location('gcp_access', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_image_total(bucket=None, *, bucket_factory=None):
    """Count names only; retain one small total per session for 60 seconds."""
    cached = st.session_state.get('_image_total')
    now = time.monotonic()
    if cached and now - cached['checked_at'] < 60:
        return cached['count']
    if bucket is None:
        bucket = bucket_factory()
    total = sum(
        1 for blob in bucket.list_blobs(
            prefix=BUCKET_PREFIX_FOR_IMAGES, fields='items(name),nextPageToken', timeout=30
        ) if not blob.name.endswith('/')
    )
    st.session_state['_image_total'] = {'count': total, 'checked_at': time.monotonic()}
    return total


def delete_image(blob):
    """Delete only the clicked image before the gallery refreshes."""
    try:
        blob.delete()
    except Exception:
        logger.exception('Could not delete image')
        st.session_state['_image_delete_error'] = (
            'Could not delete this image. Check storage delete permissions and try again.'
        )
    else:
        invalidate_cloud_listings()
        st.session_state['_image_delete_success'] = f'Deleted {blob.name}'


def main():
    st.title('GCS Images')
    loading = st.empty()
    loading.info('Loading images…')

    try:
        gcp_access = load_gcp_access()
        bucket_name = (os.environ.get('BUCKET_NAME') or '').strip()
        if not bucket_name:
            loading.empty()
            st.error('BUCKET_NAME is not configured. Set it in the root .env file or server environment.')
            return
        bucket = None

        def storage_bucket():
            nonlocal bucket
            if bucket is None:
                bucket = gcp_access.get_storage_client().bucket(bucket_name)
            return bucket
        scope = (bucket_name, BUCKET_PREFIX_FOR_IMAGES)
        if st.session_state.get('_image_listing_scope') != scope:
            st.session_state['_image_listing_scope'] = scope
            st.session_state['_image_page_tokens'] = [None]
            st.session_state['_image_page_counts'] = {}
            st.session_state.pop('_image_total', None)
        tokens = st.session_state['_image_page_tokens']
        def load_page():
            listing = storage_bucket().list_blobs(
                prefix=BUCKET_PREFIX_FOR_IMAGES, max_results=PAGE_SIZE,
                page_size=PAGE_SIZE, page_token=tokens[-1], timeout=30,
            )
            blobs = [blob for blob in listing if not blob.name.endswith('/')]
            return blobs, listing.next_page_token

        with st.spinner('Loading images…'):
            blobs, next_token = get_listing(
                '_image_page_listing', (scope, tokens[-1]), load_page
            )
    except Exception:
        logger.exception('Could not load image listing')
        loading.empty()
        st.error('Could not load images. Check the cloud credentials in the root .env file '
                 'or application default credentials, the bucket name, and storage list permissions.')
        return

    counts = st.session_state.setdefault('_image_page_counts', {})
    counts[tokens[-1]] = len(blobs)
    start = sum(counts.get(token, 0) for token in tokens[:-1]) + 1
    end = start + len(blobs) - 1
    # Show the bounded page before waiting for the full name-only count.
    range_base = f'Showing {start}–{end}' if blobs else 'Showing 0'
    cached_total = st.session_state.get('_image_total')
    range_label = (f"{range_base} of {cached_total['count']} images" if cached_total
                   else f'{range_base} images · Counting total…')
    loading.empty()

    with st.container(key='images_status'):
        error = st.session_state.pop('_image_delete_error', None)
        success = st.session_state.pop('_image_delete_success', None)
        if error:
            st.error(error)
        if success:
            st.success(success)
        if not blobs:
            st.info('No images found.' if len(tokens) == 1 else 'No images on this page.')

    with st.container(key='images_summary'):
        if blobs:
            summary, page_label = st.columns([4, 1], vertical_alignment='center')
            with summary:
                range_display = st.empty()
                range_display.caption(range_label)
            with page_label:
                st.caption(f'Page {len(tokens)}')

    with st.container(key='image_gallery'):
        for blob in blobs:
            with st.container(border=True):
                preview, details, actions = st.columns([1, 4, 3], vertical_alignment='center')

                with preview:
                    st.image(blob.public_url, width=64)

                with details:
                    st.write(blob.name.removeprefix(BUCKET_PREFIX_FOR_IMAGES))
                    st.write(blob.public_url)

                with actions:
                    filename = Path(blob.name).name
                    download, delete = st.columns([3, 2], gap='small')
                    with download:
                        st.download_button(
                            'Download', data=partial(blob.download_as_bytes, timeout=30),
                            file_name=filename,
                            mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                            key=f'download_image_{blob.name}', icon=':material/download:',
                            on_click='ignore', use_container_width=True,
                        )
                    with delete:
                        st.button('Delete', key=blob.name, use_container_width=True,
                                  on_click=delete_image, args=(blob,))

    try:
        total = get_image_total(bucket_factory=storage_bucket) if blobs or len(tokens) > 1 or next_token else 0
    except Exception:
        logger.exception('Could not count images')
        total = None  # A failed count must not prevent browsing the images.
    range_label = range_base
    range_label += f' of {total} images' if total is not None else ' images · Total unavailable'
    if blobs:
        range_display.caption(range_label)

    if len(tokens) > 1 or next_token:
        st.divider()
        with st.container(key='images_pagination'):
            page_info, previous, following = st.columns([4, 1, 1], vertical_alignment='center')
            with page_info:
                st.caption(range_label)
            with previous:
                if st.button('Previous', disabled=len(tokens) == 1, key='images_previous',
                             icon=':material/chevron_left:', use_container_width=True):
                    tokens.pop()
                    st.rerun()
            with following:
                if st.button('Next', disabled=not next_token, key='images_next',
                             icon=':material/chevron_right:', type='primary', use_container_width=True):
                    tokens.append(next_token)
                    st.rerun()


if __name__ == '__main__':
    main()
