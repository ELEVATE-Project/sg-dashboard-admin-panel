from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


PAGE = Path(__file__).resolve().parents[1] / 'frontend-pages' / 'upload-images.py'


class UploadImagesTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(PAGE)).run()
        self.assertFalse(app.exception)
        return app

    def test_name_field_visible_but_name_alone_does_not_show_upload(self):
        with patch('streamlit.file_uploader', return_value=None), \
                patch('tabs_scripts.upload_image_from_device.upload_svg_from_local') as upload:
            app = self.app()
            self.assertFalse(app.button)
            app.text_input[0].set_value('icon').run()
            self.assertEqual(app.text_input[0].label, 'Enter image name')
            self.assertFalse(app.button)
            upload.assert_not_called()

    def test_blank_name_keeps_button_hidden(self):
        with patch('streamlit.file_uploader', return_value=BytesIO(b'<svg/>')), \
                patch('tabs_scripts.upload_image_from_device.upload_svg_from_local') as upload:
            app = self.app()
            app.text_input[0].set_value('   ').run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button)
            upload.assert_not_called()

    def test_submit_uploads_file_then_shows_result_and_cleans_up(self):
        paths = []

        def upload(path, name):
            paths.append(Path(path))
            self.assertEqual(Path(path).read_bytes(), b'<svg/>')
            self.assertEqual(name, 'My icon')
            return True, {'blob_path': 'sg-dashboard/assets/icons/my_icon.svg',
                          'url': 'https://example.invalid/my_icon.svg'}

        with patch('streamlit.file_uploader', return_value=BytesIO(b'<svg/>')), \
                patch('tabs_scripts.upload_image_from_device.upload_svg_from_local', side_effect=upload) as submit:
            app = self.app()
            submit.assert_not_called()
            app.text_input[0].set_value(' My icon ').run()
            app.button(key='upload_svg').click().run()
            self.assertFalse(app.exception)
            submit.assert_called_once()
            self.assertFalse(paths[0].exists())
            self.assertEqual(app.code[0].value, 'https://example.invalid/my_icon.svg')
            self.assertIn('uploaded successfully', app.success[0].value)
            self.assertEqual(app.text_input[0].value, '')

    def test_upload_exception_shows_error_and_cleans_up(self):
        paths = []

        def upload(path, name):
            paths.append(Path(path))
            raise ValueError('private credential details')

        with patch('streamlit.file_uploader', return_value=BytesIO(b'<svg/>')), \
                patch('tabs_scripts.upload_image_from_device.upload_svg_from_local', side_effect=upload):
            app = self.app()
            app.text_input[0].set_value('icon').run()
            app.button(key='upload_svg').click().run()
            self.assertFalse(app.exception)
            self.assertIn('Could not upload', app.error[0].value)
            self.assertNotIn('private credential details', app.error[0].value)
            self.assertFalse(paths[0].exists())
            self.assertEqual(app.session_state['uploaded_svgs'], [])


if __name__ == '__main__':
    unittest.main()
