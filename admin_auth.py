"""Server-side authentication for the admin panel."""

import hashlib
import hmac
import ipaddress
import math
import os
import time
from collections import deque
from pathlib import Path
from threading import Lock

import streamlit as st
from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parent / ".env"
IDLE_TIMEOUT = 30 * 60
SESSION_TIMEOUT = 8 * 60 * 60
MAX_LOGIN_FAILURES = 5
MAX_IP_LOGIN_FAILURES = 20
MAX_GLOBAL_LOGIN_FAILURES = 100
MAX_LOGIN_KEYS = 4096
LOGIN_WINDOW = 60
# Shared across browser sessions in this server process; refreshing cannot reset it.
_login_failures = {}
_global_login_failures = deque(maxlen=MAX_GLOBAL_LOGIN_FAILURES)
_login_lock = Lock()


def _credentials():
    # Read the file every time so changes do not leave stale process credentials.
    try:
        # Passwords containing ${...} must be compared literally.
        config = dotenv_values(ENV_FILE, interpolate=False)
    except (OSError, UnicodeError):
        return "", ""
    return tuple(config.get(key, os.environ.get(key, "")) or ""
                 for key in ("ADMIN_USERNAME", "ADMIN_PASSWORD"))


def _session_token(username, password):
    return hmac.new(password.encode(), username.encode(), hashlib.sha256).hexdigest()


def _client_ip():
    # Forwarded headers are untrusted unless the operator explicitly opts in.
    # The proxy must overwrite this header with ONE IP and block direct access.
    try:
        config = dotenv_values(ENV_FILE, interpolate=False)
    except (OSError, UnicodeError):
        config = {}
    trust_proxy = config.get(
        "ADMIN_TRUST_PROXY_HEADERS", os.environ.get("ADMIN_TRUST_PROXY_HEADERS", "false")
    )
    if str(trust_proxy).lower() == "true":
        candidate = st.context.headers.get("X-Forwarded-For", "")
    else:
        candidate = st.context.ip_address
    try:
        return str(ipaddress.ip_address((candidate or "").strip()))
    except ValueError:
        # Missing/malformed addresses share a conservative fallback budget.
        return "unknown"


def _login_limits(client_ip, supplied_username, now):
    """Return (retry delay, buckets) while holding _login_lock."""
    for key, failures in list(_login_failures.items()):
        while failures and now - failures[0] >= LOGIN_WINDOW:
            failures.popleft()
        if not failures:
            del _login_failures[key]
    while _global_login_failures and now - _global_login_failures[0] >= LOGIN_WINDOW:
        _global_login_failures.popleft()

    user_hash = hashlib.sha256(f"{client_ip}\0{supplied_username}".encode()).hexdigest()
    limits = ((('ip', client_ip), MAX_IP_LOGIN_FAILURES),
              (('ip+user', user_hash), MAX_LOGIN_FAILURES))
    blocked = []
    for key, limit in limits:
        failures = _login_failures.get(key, ())
        if len(failures) >= limit:
            blocked.append(LOGIN_WINDOW - (now - failures[0]))
    if len(_global_login_failures) >= MAX_GLOBAL_LOGIN_FAILURES:
        blocked.append(LOGIN_WINDOW - (now - _global_login_failures[0]))
    missing = sum(key not in _login_failures for key, _ in limits)
    if len(_login_failures) + missing > MAX_LOGIN_KEYS:
        # Do not evict active buckets: that would allow cycling keys to reset limits.
        blocked.append(min(LOGIN_WINDOW - (now - failures[0])
                           for failures in _login_failures.values()))
    if blocked:
        return max(1, math.ceil(max(blocked))), ()
    return 0, limits


def _login():
    username, password = _credentials()
    supplied_username = st.session_state.pop("_login_username", "")
    supplied_password = st.session_state.pop("_login_password", "")
    client_ip = _client_ip()
    with _login_lock:
        now = time.monotonic()
        retry_after, limits = _login_limits(client_ip, supplied_username, now)
        if retry_after:
            _logout()
            st.session_state["_login_error"] = (
                f"Too many sign-in attempts. Try again in "
                f"{retry_after} seconds."
            )
            return
        username_matches = hmac.compare_digest(supplied_username.encode(), username.encode())
        password_matches = hmac.compare_digest(supplied_password.encode(), password.encode())
        _logout()
        if username and password and username_matches and password_matches:
            # A successful login must not reset other sources' or the IP's budget.
            _login_failures.pop(limits[1][0], None)
            st.session_state["_admin_session"] = _session_token(username, password)
            st.session_state["_admin_signed_in_at"] = now
            st.session_state["_admin_last_active"] = now
        else:
            for key, limit in limits:
                _login_failures.setdefault(key, deque(maxlen=limit)).append(now)
            _global_login_failures.append(now)
            st.session_state["_login_error"] = "Invalid username or password."


def _logout():
    st.session_state.clear()


def _valid_session(username, password):
    token = st.session_state.get("_admin_session")
    signed_in = st.session_state.get("_admin_signed_in_at")
    last_active = st.session_state.get("_admin_last_active")
    now = time.monotonic()
    if not (
        isinstance(token, str) and token.isascii()
        and hmac.compare_digest(token, _session_token(username, password))
        and isinstance(signed_in, (int, float))
        and isinstance(last_active, (int, float))
        and 0 <= now - signed_in < SESSION_TIMEOUT
        and 0 <= now - last_active < IDLE_TIMEOUT
    ):
        return False
    st.session_state["_admin_last_active"] = now
    return True


LOGIN_STYLE = """
<style>
    [data-testid="stAppViewContainer"]:has(.st-key-login_panel) {
        background: radial-gradient(ellipse at 15% 10%, #e7f1e8 0, transparent 48%),
                    radial-gradient(ellipse at 95% 95%, #edf2e5 0, transparent 45%), #f7f9f6;
    }
    [data-testid="stMainBlockContainer"]:has(.st-key-login_panel) {
        max-width: 560px;
        padding: clamp(2rem, 7vh, 5rem) 1.25rem 2rem;
    }
    .st-key-login_panel {
        background: #ffffff;
        border: 1px solid #e0e8df;
        border-top: 4px solid #2e7d32;
        border-radius: 20px;
        padding: 2.5rem;
        box-shadow: 0 18px 55px rgba(30, 64, 36, 0.07);
        color: #17221c;
    }
    .st-key-login_panel h1 {
        color: #17221c;
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: -0.04em;
        padding-top: 0.25rem;
        padding-bottom: 0;
    }
    .st-key-login_panel .login-eyebrow {
        color: #527358;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.16em;
        margin: 1.25rem 0 0;
    }
    .st-key-login_panel [data-testid="stCaptionContainer"] {
        color: #5c6b60;
        line-height: 1.6;
    }
    .st-key-login_panel [data-testid="stForm"] { padding: 0; border: 0; }
    .st-key-login_panel [data-testid="stTextInput"] label { color: #273b2c; }
    .st-key-login_panel [data-baseweb="input"] {
        background: #f8faf7;
        border-color: #d9e2d7;
        border-radius: 10px;
        min-height: 48px;
        color: #17221c;
    }
    .st-key-login_panel input { color: #17221c; }
    .st-key-login_panel [data-baseweb="input"]:focus-within {
        border-color: #2e7d32;
        box-shadow: 0 0 0 3px rgba(46, 125, 50, 0.12);
    }
    .st-key-login_panel [data-testid="stFormSubmitButton"] button {
        min-height: 48px;
        border-radius: 10px;
        background: #2e7d32;
        border-color: #2e7d32;
        color: white;
        font-weight: 600;
        margin-top: 0.5rem;
    }
    .st-key-login_panel [data-testid="stFormSubmitButton"] button:hover {
        background: #236328;
        border-color: #236328;
    }
    .st-key-login_panel button:focus-visible {
        outline: 3px solid #598f5c;
        outline-offset: 3px;
    }
    .st-key-login_panel .login-footer {
        border-top: 1px solid #edf1eb;
        padding-top: 1.25rem;
        margin: 0.5rem 0 0;
        color: #667268;
        font-size: 0.75rem;
        line-height: 1.6;
    }
    @media (max-width: 480px) {
        .st-key-login_panel { padding: 1.5rem; border-radius: 16px; }
        .st-key-login_panel h1 { font-size: 1.75rem; }
    }
</style>
"""


def require_login():
    """Stop before any admin functionality loads for an unauthenticated user."""
    username, password = _credentials()
    if not username or not password:
        _logout()
        st.error("Admin login is not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD on the server.")
        st.stop()

    # Keep login and logout in separate, fixed slots. Clearing the login slot
    # replaces its entire tree before any dashboard elements are sent.
    login_slot = st.empty()
    header_slot = st.empty()
    if _valid_session(username, password):
        login_slot.empty()
        with header_slot.container():
            with st.container(key="authenticated_header", height=40, border=False):
                _, logout_column = st.columns([8, 1])
                with logout_column:
                    st.button("Log out", on_click=_logout, use_container_width=True)
        return

    if "_admin_session" in st.session_state:
        _logout()
        st.session_state["_login_notice"] = "Your session has ended. Please sign in again."
    header_slot.empty()
    with login_slot.container():
        st.markdown(LOGIN_STYLE, unsafe_allow_html=True)
        with st.container(key="login_panel"):
            st.image(str(Path(__file__).resolve().parent / "main_logo.svg"), width=180)
            st.markdown('<p class="login-eyebrow">SG DASHBOARD · ADMIN ACCESS</p>', unsafe_allow_html=True)
            st.title("Welcome back")
            st.caption("Sign in to manage your dashboard data and images.")
            if st.session_state.get("_login_notice"):
                st.info(st.session_state["_login_notice"])
            with st.form("admin_login", clear_on_submit=True, border=False):
                st.text_input("Username", key="_login_username", placeholder="Enter your username", autocomplete="username")
                st.text_input("Password", type="password", key="_login_password", placeholder="Enter your password", autocomplete="current-password")
                st.form_submit_button("Log in", on_click=_login, type="primary", use_container_width=True)
            if st.session_state.get("_login_error"):
                st.error(st.session_state["_login_error"])
            st.markdown(
                '<p class="login-footer">Authorized administrators only.<br>'
                'Need access? Contact your dashboard administrator.</p>',
                unsafe_allow_html=True,
            )
    st.stop()
