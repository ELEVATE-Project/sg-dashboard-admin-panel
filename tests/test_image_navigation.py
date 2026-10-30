import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class Listing(list):
    next_page_token = None


class ImageNavigationTests(unittest.TestCase):
    def test_repeated_switches_show_only_selected_section_and_keep_name(self):
        image = MagicMock()
        image.name = 'sg-dashboard/assets/icons/example.svg'
        image.public_url = 'https://example.invalid/example.svg'
        with patch('admin_auth.require_login'), \
                patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}, clear=True), \
                patch('dotenv.load_dotenv'), \
                patch('google.cloud.storage.Client') as client, \
                patch('streamlit.markdown', wraps=st.markdown) as markdown:
            bucket = client.return_value.bucket.return_value
            def load_images(**kwargs):
                # The outgoing upload section must be hidden before network I/O.
                styles = [call.args[0] for call in markdown.call_args_list
                          if call.args and '[class*="st-key-admin_page_"]:not(' in str(call.args[0])]
                self.assertIn('.st-key-admin_page_3)', styles[-1])
                self.assertIn('display: none !important', styles[-1])
                all_styles = '\n'.join(str(call.args[0]) for call in markdown.call_args_list if call.args)
                self.assertIn('body:has(.st-key-admin_section input[value="3"]:checked)', all_styles)
                self.assertIn('[class*="st-key-admin_page_"] [data-stale="true"]', all_styles)
                return Listing([image])

            bucket.list_blobs.side_effect = load_images
            app = AppTest.from_file(str(ROOT / 'app.py')).run()
            self.assertFalse(app.exception)
            for _ in range(3):
                app.radio(key='admin_section').set_value('Upload image from local device').run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.text_input), 1)
                self.assertEqual(len(app.get('file_uploader')), 1)
                self.assertEqual(len(app.get('imgs')), 1)  # Only the dashboard logo.
                if _ == 0:
                    app.text_input[0].set_value('My image').run()
                    app.session_state['_selected_svg_draft'] = {
                        'name': 'draft.svg', 'data': b'<svg>draft</svg>'
                    }
                else:
                    self.assertEqual(app.text_input[0].value, 'My image')
                    self.assertIn('Selected image: draft.svg', [item.value for item in app.caption])
                    self.assertEqual(app.button(key='upload_svg').label, 'Upload SVG')
                app.radio(key='admin_section').set_value('Handle all images').run()
                self.assertFalse(app.exception)
                self.assertFalse(app.text_input)
                self.assertFalse(app.get('file_uploader'))
                self.assertEqual(len(app.get('imgs')), 2)
                self.assertIn('example.svg', [item.value for item in app.markdown])
            client.assert_called_once()
            self.assertEqual(bucket.list_blobs.call_count, 2)  # Page plus name-only count.
            image.delete.assert_not_called()
            image.download_as_bytes.assert_not_called()
