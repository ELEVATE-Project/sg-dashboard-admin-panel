import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv
from streamlit.testing.v1 import AppTest

from constants import BUCKET_PREFIX_FOR_IMAGES


PAGE = Path(__file__).resolve().parents[1] / 'frontend-pages' / 'handle-all-images.py'
SPEC = importlib.util.spec_from_file_location('handle_images_test_page', PAGE)
page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(page)


class Listing(list):
    def __init__(self, items=(), next_page_token=None):
        super().__init__(items)
        self.next_page_token = next_page_token


class HandleImagesTests(unittest.TestCase):
    def setUp(self):
        counter = patch.object(page, 'get_image_total', return_value=30)
        self.counter = counter.start()
        self.addCleanup(counter.stop)

    def app(self):
        with patch.dict(sys.modules, {'handle_images_test_page': page}):
            result = AppTest.from_string('from handle_images_test_page import main\nmain()').run()
        self.assertFalse(result.exception)
        return result

    def blob(self, name='image.svg'):
        blob = MagicMock()
        blob.name = BUCKET_PREFIX_FOR_IMAGES + name
        blob.public_url = 'https://storage.googleapis.com/test-bucket/' + blob.name
        return blob

    def test_direct_visit_loads_dotenv_and_normalizes_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text('BUCKET_NAME=test-bucket\nPROJECT_ID=test-project\n'
                                'CLIENT_EMAIL=test@example.com\nTOKEN_URI=https://oauth2.googleapis.com/token\n'
                                "PRIVATE_KEY='first\\nsecond'\n")
            with patch.dict(os.environ, {}, clear=True), \
                    patch('dotenv.load_dotenv', side_effect=lambda: load_dotenv(env_file)), \
                    patch('google.oauth2.service_account.Credentials.from_service_account_info') as credentials, \
                    patch('google.cloud.storage.Client') as client:
                client.return_value.bucket.return_value.list_blobs.return_value = Listing()
                app = self.app()
                self.assertFalse(app.error)
                self.assertEqual(credentials.call_args.args[0]['private_key'], 'first\nsecond')
                client.return_value.bucket.assert_called_once_with('test-bucket')

    def test_no_private_key_uses_default_credentials(self):
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}, clear=True), \
                patch('dotenv.load_dotenv'), \
                patch('google.oauth2.service_account.Credentials.from_service_account_info') as credentials, \
                patch('google.cloud.storage.Client') as client:
            client.return_value.bucket.return_value.list_blobs.return_value = Listing()
            app = self.app()
            self.assertFalse(app.error)
            credentials.assert_not_called()
            client.assert_called_once_with(project=None)

    def test_missing_bucket_does_not_connect(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(page, 'load_gcp_access') as loader:
            app = self.app()
            self.assertIn('BUCKET_NAME', app.error[0].value)
            loader.return_value.get_storage_client.assert_not_called()

    def test_configuration_failure_is_reported_without_secret_details(self):
        with patch.object(page, 'load_gcp_access', side_effect=ValueError('private credential data')), \
                self.assertLogs(page.logger, level='ERROR') as logs:
            app = self.app()
            self.assertIn('Could not load images', app.error[0].value)
            self.assertNotIn('private credential data', app.error[0].value)
            self.assertIn('Could not load image listing', logs.output[0])
            self.assertIsInstance(logs.records[0].exc_info[1], ValueError)
            self.assertFalse(app.info)

    def test_listing_failure_during_iteration_is_reported(self):
        def failed_listing():
            yield self.blob()
            raise PermissionError('denied')

        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader:
            bucket = loader.return_value.get_storage_client.return_value.bucket.return_value
            bucket.list_blobs.return_value = failed_listing()
            app = self.app()
            self.assertIn('Could not load images', app.error[0].value)
            self.assertFalse(app.button)

    def test_listing_skips_folders_and_preserves_full_url(self):
        image = self.blob()
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader:
            bucket = loader.return_value.get_storage_client.return_value.bucket.return_value
            bucket.list_blobs.return_value = Listing([self.blob(''), image])
            app = self.app()
            bucket.list_blobs.assert_called_once_with(
                prefix=BUCKET_PREFIX_FOR_IMAGES, max_results=10, page_size=10,
                page_token=None, timeout=30)
            self.assertEqual(len(app.button), 1)
            text = [item.value for item in app.markdown]
            self.assertIn('image.svg', text)
            self.assertIn(image.public_url, text)

    def test_gallery_is_rendered_before_total_count_request(self):
        image = self.blob()
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.object(page.st, 'image', wraps=page.st.image) as preview:
            loader.return_value.get_storage_client.return_value.bucket.return_value.list_blobs.return_value = Listing([image])
            def count_after_preview(*args, **kwargs):
                preview.assert_called_once_with(image.public_url, width=64)
                return 30
            self.counter.side_effect = count_after_preview
            app = self.app()
            self.assertIn('Showing 1–1 of 30 images', [item.value for item in app.caption])

    def test_reopening_reuses_current_page_listing(self):
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.dict(sys.modules, {'handle_images_test_page': page}):
            bucket = loader.return_value.get_storage_client.return_value.bucket.return_value
            bucket.list_blobs.return_value = Listing([self.blob()])
            app = self.app()
            app.run()
            self.assertFalse(app.exception)
            bucket.list_blobs.assert_called_once()

    def test_downloads_are_on_demand_and_keep_each_images_bytes_and_filename(self):
        first, second = self.blob('first.svg'), self.blob('second.png')
        first.download_as_bytes.return_value = b'<svg>first</svg>'
        second.download_as_bytes.return_value = b'original-png-bytes'
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.object(page.st, 'download_button') as download:
            loader.return_value.get_storage_client.return_value.bucket.return_value.list_blobs.return_value = Listing([first, second])
            self.app()
            first.download_as_bytes.assert_not_called()
            second.download_as_bytes.assert_not_called()
            self.assertEqual(download.call_count, 2)
            first_button, second_button = [call.kwargs for call in download.call_args_list]
            self.assertEqual(first_button['file_name'], 'first.svg')
            self.assertEqual(first_button['mime'], 'image/svg+xml')
            self.assertEqual(second_button['file_name'], 'second.png')
            self.assertEqual(second_button['mime'], 'image/png')
            self.assertEqual(first_button['on_click'], 'ignore')
            self.assertEqual(first_button['data'](), b'<svg>first</svg>')
            self.assertEqual(second_button['data'](), b'original-png-bytes')
            first.download_as_bytes.assert_called_once_with(timeout=30)
            second.download_as_bytes.assert_called_once_with(timeout=30)
            first.delete.assert_not_called()
            second.delete.assert_not_called()

    def test_pagination_fetches_only_requested_page_and_never_deletes(self):
        first, second = self.blob('first.svg'), self.blob('second.svg')
        def listing(**kwargs):
            if kwargs['page_token'] is None:
                return Listing([first], next_page_token='page-two')
            self.assertEqual(kwargs['page_token'], 'page-two')
            return Listing([second])

        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.dict(sys.modules, {'handle_images_test_page': page}):
            bucket = loader.return_value.get_storage_client.return_value.bucket.return_value
            bucket.list_blobs.side_effect = listing
            app = self.app()
            self.assertTrue(app.button(key='images_previous').disabled)
            self.assertIn(first.public_url, [item.value for item in app.markdown])
            self.assertIn('Showing 1–1 of 30 images', [item.value for item in app.caption])
            app.button(key='images_next').click().run()
            self.assertFalse(app.exception)
            self.assertIn(second.public_url, [item.value for item in app.markdown])
            self.assertIn('Showing 2–2 of 30 images', [item.value for item in app.caption])
            self.assertNotIn(first.public_url, [item.value for item in app.markdown])
            self.assertTrue(app.button(key='images_next').disabled)
            app.button(key='images_previous').click().run()
            self.assertFalse(app.exception)
            self.assertIn(first.public_url, [item.value for item in app.markdown])
            first.delete.assert_not_called()
            second.delete.assert_not_called()
            self.assertTrue(all(call.kwargs['max_results'] == 10
                                for call in bucket.list_blobs.call_args_list))

    def test_count_failure_does_not_block_gallery(self):
        self.counter.side_effect = PermissionError('denied')
        image = self.blob()
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                self.assertLogs(page.logger, level='ERROR') as logs:
            loader.return_value.get_storage_client.return_value.bucket.return_value.list_blobs.return_value = Listing([image])
            app = self.app()
            self.assertFalse(app.error)
            self.assertIn('Could not count images', logs.output[0])
            self.assertIsInstance(logs.records[0].exc_info[1], PermissionError)
            self.assertIn('Showing 1–1 images · Total unavailable', [item.value for item in app.caption])
            self.assertIn(image.public_url, [item.value for item in app.markdown])

    def test_delete_failure_keeps_page_usable(self):
        image = self.blob()
        image.delete.side_effect = PermissionError('private error details')
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.dict(sys.modules, {'handle_images_test_page': page}), \
                self.assertLogs(page.logger, level='ERROR') as logs:
            loader.return_value.get_storage_client.return_value.bucket.return_value.list_blobs.return_value = Listing([image])
            app = self.app()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertIn('Could not delete', app.error[0].value)
            self.assertFalse(app.success)
            image.delete.assert_called_once()
            self.assertIn('Could not delete image', logs.output[0])
            self.assertIsInstance(logs.records[0].exc_info[1], PermissionError)

    def test_successful_delete_refreshes_listing(self):
        image = self.blob()
        with patch.dict(os.environ, {'BUCKET_NAME': 'test-bucket'}), \
                patch.object(page, 'load_gcp_access') as loader, \
                patch.dict(sys.modules, {'handle_images_test_page': page}):
            bucket = loader.return_value.get_storage_client.return_value.bucket.return_value
            bucket.list_blobs.side_effect = [Listing([image]), Listing()]
            app = self.app()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            image.delete.assert_called_once()
            self.assertFalse(app.button)
            self.assertEqual(app.info[0].value, 'No images found.')


class ImageTotalTests(unittest.TestCase):
    def test_count_uses_names_only_skips_folders_and_reuses_cached_total(self):
        bucket = MagicMock()
        folder, image = MagicMock(), MagicMock()
        folder.name = BUCKET_PREFIX_FOR_IMAGES
        image.name = BUCKET_PREFIX_FOR_IMAGES + 'image.svg'
        bucket.list_blobs.return_value = [folder, image]
        state = {}
        with patch.object(page.st, 'session_state', state), patch.object(page.time, 'monotonic', return_value=100):
            self.assertEqual(page.get_image_total(bucket), 1)
            self.assertEqual(page.get_image_total(bucket), 1)
            bucket.list_blobs.assert_called_once_with(
                prefix=BUCKET_PREFIX_FOR_IMAGES, fields='items(name),nextPageToken', timeout=30)
        with patch.object(page.st, 'session_state', state), patch.object(page.time, 'monotonic', return_value=161):
            page.get_image_total(bucket)
            self.assertEqual(bucket.list_blobs.call_count, 2)

    def test_successful_delete_invalidates_total(self):
        image = MagicMock()
        image.name = 'image.svg'
        state = {'_image_total': {'count': 30, 'checked_at': 100}}
        with patch.object(page.st, 'session_state', state):
            page.delete_image(image)
        self.assertNotIn('_image_total', state)
        image.delete.assert_called_once()


if __name__ == '__main__':
    unittest.main()
