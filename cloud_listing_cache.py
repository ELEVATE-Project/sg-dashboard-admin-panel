"""Small, short-lived listing caches kept within the authenticated session."""
import time

import streamlit as st


CACHE_KEYS = ('_source_excel_listing', '_image_page_listing', '_image_total')


def get_listing(key, scope, load, ttl=30):
    cached = st.session_state.get(key)
    now = time.monotonic()
    if cached and cached['scope'] == scope and now - cached['checked_at'] < ttl:
        return cached['value']
    value = load()  # Failed requests are never cached.
    st.session_state[key] = {'scope': scope, 'checked_at': time.monotonic(), 'value': value}
    return value


def invalidate_cloud_listings():
    for key in CACHE_KEYS:
        st.session_state.pop(key, None)
