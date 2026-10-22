import builtins
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import admin_auth
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class AdminLoginTests(unittest.TestCase):
    def setUp(self):
        admin_auth._login_failures.clear()
        self.addCleanup(admin_auth._login_failures.clear)
        admin_auth._global_login_failures.clear()
        self.addCleanup(admin_auth._global_login_failures.clear)
        self.ip_patch = patch("admin_auth._client_ip", return_value="198.51.100.1")
        self.ip_patch.start()
        self.addCleanup(self.ip_patch.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = Path(directory.name) / ".env"
        self.env_file.write_text("ADMIN_USERNAME=test-admin\nADMIN_PASSWORD=test-password\n")
        self.protected_script = Path(directory.name) / "protected_admin.py"
        self.protected_script.write_text(
            "import streamlit as st\nfrom admin_auth import require_login\n"
            "require_login()\nst.file_uploader('Protected upload')\n"
        )
        env_patch = patch("admin_auth.ENV_FILE", self.env_file)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def app(self, actual=False):
        app = AppTest.from_file(str(ROOT / "app.py") if actual else str(self.protected_script)).run()
        self.assertFalse(app.exception)
        return app

    def login(self, app, password="test-password", username="test-admin"):
        app.text_input(key="_login_username").set_value(username)
        app.text_input(key="_login_password").set_value(password)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        return app

    def test_anonymous_and_invalid_login_cannot_access_admin(self):
        app = self.app(actual=True)
        self.assertFalse(app.get("file_uploader"))
        self.assertFalse(app.get("segmented_control"))
        self.login(app, "wrong-password")
        self.assertEqual(app.error[0].value, "Invalid username or password.")
        self.assertNotIn("_admin_session", app.session_state)
        self.assertFalse(app.get("file_uploader"))

    def test_login_is_fast_path_and_logout_clears_session(self):
        original_import = builtins.__import__

        def reject_slow_imports(name, *args, **kwargs):
            if name.startswith(("backend_voices_data", "tabs_scripts", "google.cloud", "streamlit_ace")):
                raise AssertionError(f"Unexpected import on first page: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_slow_imports):
            actual_app = self.login(self.app(actual=True))
            self.assertEqual(len(actual_app.get("file_uploader")), 1)
            self.assertFalse(actual_app.sidebar.button)
            app = self.login(self.app())
            self.assertEqual(len(app.get("file_uploader")), 1)
            self.assertFalse(app.sidebar.button)
            self.assertEqual([button.label for button in app.button], ["Log out"])
            self.assertNotIn("_login_password", app.session_state)
            app.run()
            self.assertEqual(len(app.get("file_uploader")), 1)
            app.session_state["private_data"] = "upload"
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertNotIn("private_data", app.session_state)
        self.assertNotIn("_admin_session", app.session_state)
        self.assertFalse(app.get("file_uploader"))
        self.assertEqual(app.button[0].label, "Log in")

    def test_env_edits_invalidate_session_and_missing_credentials_block_access(self):
        app = self.login(self.app())
        app.session_state["private_data"] = "sensitive upload"
        self.env_file.write_text("ADMIN_USERNAME=test-admin\nADMIN_PASSWORD=new-password\n")
        app.run()
        self.assertFalse(app.get("file_uploader"))

        self.assertNotIn("_admin_session", app.session_state)
        self.assertNotIn("private_data", app.session_state)
        self.login(app, "new-password")
        self.assertEqual(len(app.get("file_uploader")), 1)
        self.env_file.write_text("ADMIN_USERNAME=\nADMIN_PASSWORD=\n")
        app.run()
        self.assertFalse(app.exception)
        self.assertIn("not configured", app.error[0].value)
        self.assertNotIn("_admin_session", app.session_state)
        self.assertFalse(app.get("file_uploader"))

    def test_retry_limit_survives_new_browser_sessions_and_recovers(self):
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000)):
            app = self.app()
            for _ in range(admin_auth.MAX_LOGIN_FAILURES):
                self.login(app, "wrong-password")
            # Even a fresh browser with the right credentials must wait.
            new_browser = self.login(self.app())
            self.assertIn("Too many sign-in attempts", new_browser.error[0].value)
            self.assertFalse(new_browser.get("file_uploader"))
            self.assertEqual(new_browser.text_input(key="_login_password").value, "")
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1061)):
            self.login(new_browser)
            self.assertEqual(len(new_browser.get("file_uploader")), 1)

    def test_idle_timeout_clears_private_data_and_requires_login(self):
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000)):
            app = self.login(self.app())
        app.session_state["private_data"] = "sensitive upload"
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000 + admin_auth.IDLE_TIMEOUT)):
            app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.get("file_uploader"))
        self.assertNotIn("private_data", app.session_state)
        self.assertNotIn("_admin_session", app.session_state)
        self.assertIn("session has ended", app.info[0].value)
        self.login(app)
        self.assertEqual(len(app.get("file_uploader")), 1)

    def test_one_source_cannot_lock_out_another_source(self):
        attacker = self.app()
        for _ in range(admin_auth.MAX_LOGIN_FAILURES):
            self.login(attacker, "wrong-password")
        with patch("admin_auth._client_ip", return_value="198.51.100.2"):
            administrator = self.login(self.app())
        self.assertEqual(len(administrator.get("file_uploader")), 1)
        self.login(attacker)
        self.assertIn("Too many sign-in attempts", attacker.error[0].value)
        self.assertFalse(attacker.get("file_uploader"))

    def test_ip_budget_blocks_username_rotation(self):
        app = self.app()
        for index in range(admin_auth.MAX_IP_LOGIN_FAILURES):
            self.login(app, "wrong", username=f"user-{index}")
        self.login(app)
        self.assertIn("Too many sign-in attempts", app.error[0].value)
        self.assertFalse(app.get("file_uploader"))
        self.assertEqual(len(admin_auth._global_login_failures), admin_auth.MAX_IP_LOGIN_FAILURES)
        # Usernames are hashed in the rate-limit keys.
        self.assertFalse(any("user-" in key[1] for key in admin_auth._login_failures))

    def test_coarse_global_backstop_blocks_distributed_guesses(self):
        with patch("admin_auth.MAX_GLOBAL_LOGIN_FAILURES", 2):
            for index in range(2):
                with patch("admin_auth._client_ip", return_value=f"198.51.100.{index + 1}"):
                    self.login(self.app(), "wrong")
            with patch("admin_auth._client_ip", return_value="198.51.100.3"):
                app = self.login(self.app())
            self.assertIn("Too many sign-in attempts", app.error[0].value)
            self.assertFalse(app.get("file_uploader"))

    def test_limiter_storage_is_bounded_and_expired_keys_are_pruned(self):
        with patch("admin_auth.MAX_LOGIN_KEYS", 2):
            with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000)):
                self.login(self.app(), "wrong")
                self.assertEqual(len(admin_auth._login_failures), 2)
                with patch("admin_auth._client_ip", return_value="198.51.100.2"):
                    app = self.login(self.app(), "wrong")
                self.assertIn("Too many sign-in attempts", app.error[0].value)
                self.assertEqual(len(admin_auth._login_failures), 2)
            with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1061)):
                self.login(app)
                self.assertEqual(len(app.get("file_uploader")), 1)
                self.assertFalse(admin_auth._login_failures)

    def test_client_ip_ignores_untrusted_headers_and_validates_proxy_input(self):
        self.ip_patch.stop()
        context = types.SimpleNamespace(
            ip_address="198.51.100.1", headers={"X-Forwarded-For": "203.0.113.2"}
        )
        with patch("admin_auth.st.context", context), patch.dict(
            "os.environ", {"ADMIN_TRUST_PROXY_HEADERS": "false"}
        ):
            self.assertEqual(admin_auth._client_ip(), "198.51.100.1")
            self.env_file.write_text("ADMIN_TRUST_PROXY_HEADERS=true\n")
            self.assertEqual(admin_auth._client_ip(), "203.0.113.2")
            for address in ("198.51.100.2, 203.0.113.3", "invalid", ""):
                context.headers["X-Forwarded-For"] = address
                self.assertEqual(admin_auth._client_ip(), "unknown")
            context.headers["X-Forwarded-For"] = "2001:0db8::1"
            self.assertEqual(admin_auth._client_ip(), "2001:db8::1")
            self.env_file.write_text("ADMIN_TRUST_PROXY_HEADERS=false\n")
            context.ip_address = None
            self.assertEqual(admin_auth._client_ip(), "unknown")

    def test_sample_credentials_fail_closed(self):
        self.env_file.write_text((ROOT / "sample.env").read_text())
        app = self.app()
        self.assertIn("not configured", app.error[0].value)
        self.assertFalse(app.get("file_uploader"))

    def test_absolute_timeout_applies_even_with_recent_activity(self):
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000)):
            app = self.login(self.app())
        deadline = 1000 + admin_auth.SESSION_TIMEOUT
        app.session_state["_admin_last_active"] = deadline - 1
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: deadline)):
            app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.get("file_uploader"))
        self.assertNotIn("_admin_session", app.session_state)

    def test_activity_extends_idle_timeout(self):
        with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: 1000)):
            app = self.login(self.app())
        for now in (2000, 3000, 4000):
            with patch("admin_auth.time", types.SimpleNamespace(monotonic=lambda: now)):
                app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("file_uploader")), 1)

    def test_invalid_session_values_fail_closed(self):
        for token in (True, {}, "forged-token", "non-ascii-🔒"):
            with self.subTest(token=token):
                app = self.app()
                app.session_state["_admin_session"] = token
                app.session_state["private_data"] = "sensitive upload"
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.get("file_uploader"))
                self.assertNotIn("private_data", app.session_state)

    def test_password_is_literal_and_supports_unicode(self):
        password = "p${AUTH_TEST_VALUE}🔒"
        self.env_file.write_text(f"ADMIN_USERNAME=test-admin\nADMIN_PASSWORD='{password}'\n")
        with patch.dict("os.environ", {"AUTH_TEST_VALUE": "expanded"}):
            app = self.login(self.app(), password)
        self.assertEqual(len(app.get("file_uploader")), 1)
        self.assertNotIn("_login_password", app.session_state)

    def test_failed_submission_clears_password_and_returns_generic_error(self):
        app = self.app()
        app.text_input(key="_login_username").set_value("unknown-user-🔒")
        app.text_input(key="_login_password").set_value("incorrect")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.error[0].value, "Invalid username or password.")
        self.assertEqual(app.text_input(key="_login_password").value, "")
        self.assertFalse(app.get("file_uploader"))

    def test_missing_config_blocks_even_previously_authenticated_users(self):
        app = self.login(self.app())
        app.session_state["private_data"] = "sensitive upload"
        with patch("admin_auth.dotenv_values", side_effect=PermissionError("unreadable")):
            app.run()
        self.assertFalse(app.exception)
        self.assertIn("not configured", app.error[0].value)
        self.assertFalse(app.get("file_uploader"))
        self.assertNotIn("private_data", app.session_state)

    def test_credentials_environment_fallback_and_explicit_empty_override(self):
        self.env_file.write_text("")
        with patch.dict("os.environ", {"ADMIN_USERNAME": "test-admin", "ADMIN_PASSWORD": "test-password"}):
            app = self.login(self.app())
            self.assertEqual(len(app.get("file_uploader")), 1)
            self.env_file.write_text("ADMIN_USERNAME=\nADMIN_PASSWORD=\n")
            app.run()
        self.assertFalse(app.get("file_uploader"))
        self.assertIn("not configured", app.error[0].value)

    def test_login_style_is_removed_from_authenticated_dashboard(self):
        app = self.app(actual=True)
        self.assertTrue(any(".st-key-login_panel" in item.value for item in app.markdown))
        self.login(app)
        self.assertFalse(any(".st-key-login_panel" in item.value for item in app.markdown))

    def test_actual_dashboard_reruns_and_logs_out(self):
        app = self.login(self.app(actual=True))
        self.assertEqual(app.radio(key="admin_section").value, "📁 File Upload Dashboard")
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("file_uploader")), 1)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.radio)
        self.assertFalse(app.get("file_uploader"))
        self.assertEqual(app.button[0].label, "Log in")

    def test_section_switching_executes_only_the_selected_page(self):
        loaded = []
        original_spec = importlib.util.spec_from_file_location

        class PageLoader:
            def create_module(self, spec):
                return None

            def exec_module(self, module):
                loaded.append(module.__name__)

        def page_spec(name, location, *args, **kwargs):
            if str(location).startswith("frontend-pages/"):
                return importlib.util.spec_from_loader(name, PageLoader())
            return original_spec(name, location, *args, **kwargs)

        backend = types.ModuleType("backend_voices_data.backend_data_uploader")
        backend.CSV_TYPES = ["Test CSV"]
        backend.upload_to_gcp = lambda *args: self.fail("Navigation must not upload data")
        with patch("importlib.util.spec_from_file_location", side_effect=page_spec), patch.dict(
            sys.modules, {"backend_voices_data.backend_data_uploader": backend}
        ):
            app = self.login(self.app(actual=True))
            options = app.radio(key="admin_section").options
            page_names = ["file_upload", "json_editor", "upload_images", "handle_all_images", "source_excels"]
            self.assertEqual(loaded, page_names[:1])
            for index, option in enumerate(options):
                loaded.clear()
                app.radio(key="admin_section").set_value(option).run()
                self.assertFalse(app.exception)
                self.assertEqual(loaded, [page_names[index]] if index < 5 else [])
                self.assertEqual(app.radio(key="admin_section").value, option)
            self.assertEqual(len(app.get("file_uploader")), 1)
            self.assertEqual(app.selectbox[0].label, "Select Data Type *")



if __name__ == "__main__":
    unittest.main()
