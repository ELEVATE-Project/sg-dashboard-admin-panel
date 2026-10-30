from pathlib import Path
from datetime import datetime, timezone
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest
from dotenv import load_dotenv
from tabs_scripts import upload_excel_to_gcs as source_storage


PAGE = Path(__file__).resolve().parents[1] / 'frontend-pages' / 'source-excels.py'


class SourceExcelPaginationTests(unittest.TestCase):
    def setUp(self):
        self.files = [
            {'file_name': f'file-{day}.xlsx', 'blob_name': f'source/file-{day}.xlsx',
             'gcs_path': f'gs://test/source/file-{day}.xlsx',
             'uploaded_at': f'2026-10-{day:02d} 00:00:00 UTC', 'size_bytes': 1024}
            for day in range(12, 0, -1)
        ]
        listing = patch('tabs_scripts.upload_excel_to_gcs.list_uploaded_excels', return_value=self.files)
        self.listing = listing.start()
        self.addCleanup(listing.stop)
        download = patch('tabs_scripts.upload_excel_to_gcs.download_uploaded_excel', return_value=b'excel')
        self.download = download.start()
        self.addCleanup(download.stop)
        delete = patch('tabs_scripts.upload_excel_to_gcs.delete_uploaded_excel')
        self.delete = delete.start()
        self.addCleanup(delete.stop)

    def app(self):
        app = AppTest.from_file(str(PAGE)).run()
        self.assertFalse(app.exception)
        return app

    def names(self, app):
        return [item.value for item in app.markdown if item.value.startswith('file-')]

    def test_five_newest_per_page_and_navigation(self):
        app = self.app()
        self.assertEqual(self.names(app), [item['file_name'] for item in self.files[:5]])
        self.download.assert_not_called()
        self.assertTrue(app.button(key='source_excels_previous').disabled)
        app.button(key='source_excels_next').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.names(app), [item['file_name'] for item in self.files[5:10]])
        self.assertIn('Showing 6–10 of 12 files · Newest first', [item.value for item in app.caption])
        app.button(key='source_excels_next').click().run()
        self.assertEqual(self.names(app), [item['file_name'] for item in self.files[10:]])
        self.assertTrue(app.button(key='source_excels_next').disabled)
        app.button(key='source_excels_previous').click().run()
        self.assertEqual(self.names(app), [item['file_name'] for item in self.files[5:10]])
        self.delete.assert_not_called()

    def test_reopening_reuses_listing_and_downloads_only_on_request(self):
        with patch('streamlit.download_button') as button:
            app = self.app()
            self.listing.assert_called_once()
            self.download.assert_not_called()
            button.assert_not_called()
            app.button(key=f"prepare_excel_{self.files[0]['blob_name']}").click().run()
            self.assertFalse(app.exception)
            self.assertNotIn(f"prepare_excel_{self.files[0]['blob_name']}",
                             [action.key for action in app.button])
            request = button.call_args_list[0].kwargs
            self.assertEqual(request['file_name'], self.files[0]['file_name'])
            self.assertEqual(request['on_click'], 'ignore')
            self.assertEqual(request['data'], b'excel')
            self.assertFalse(callable(request['data']))
            self.download.assert_called_once_with(self.files[0]['blob_name'])
            app.run()
            self.assertFalse(app.exception)
            self.listing.assert_called_once()

    def test_download_failure_is_visible_and_retry_succeeds(self):
        self.download.side_effect = [PermissionError('access denied'), b'excel']
        app = self.app()
        prepare_key = f"prepare_excel_{self.files[0]['blob_name']}"
        app.button(key=prepare_key).click().run()
        self.assertFalse(app.exception)
        self.assertIn('Download failed: access denied', [item.value for item in app.error])
        self.assertNotIn('_source_excel_download', app.session_state)
        app.button(key=prepare_key).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state['_source_excel_download']['data'], b'excel')
        self.assertEqual(self.download.call_count, 2)
        self.delete.assert_not_called()

    def test_missing_download_data_is_reported_and_not_registered(self):
        self.download.return_value = None
        with patch('streamlit.download_button') as button:
            app = self.app()
            app.button(key=f"prepare_excel_{self.files[0]['blob_name']}").click().run()
            self.assertFalse(app.exception)
            self.assertIn('Download failed:', app.error[0].value)
            self.assertNotIn('_source_excel_download', app.session_state)
            button.assert_not_called()

    def test_preparing_another_file_replaces_payload_and_paging_clears_it(self):
        app = self.app()
        for item in self.files[:2]:
            app.button(key=f"prepare_excel_{item['blob_name']}").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['_source_excel_download']['blob_name'], item['blob_name'])
        app.button(key='source_excels_next').click().run()
        self.assertNotIn('_source_excel_download', app.session_state)
        self.assertEqual(self.download.call_count, 2)

    def test_current_page_clamps_when_listing_shrinks(self):
        app = self.app()
        app.button(key='source_excels_next').click().run()
        app.button(key='source_excels_next').click().run()
        self.listing.return_value = self.files[:5]
        del app.session_state['_source_excel_listing']
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['_source_excels_page'], 0)
        self.assertEqual(self.names(app), [item['file_name'] for item in self.files[:5]])
        self.assertNotIn('source_excels_next', [button.key for button in app.button])
        self.delete.assert_not_called()

    def test_listing_failure_is_not_reported_as_empty_bucket(self):
        self.listing.side_effect = ValueError('BUCKET_NAME is not configured.')
        app = self.app()
        self.assertIn('BUCKET_NAME', app.error[0].value)
        self.assertFalse(app.info)
        self.download.assert_not_called()
        self.delete.assert_not_called()

    def test_empty_listing_has_no_navigation_or_downloads(self):
        self.listing.return_value = []
        app = self.app()
        self.assertEqual(app.info[0].value, 'No source Excel files uploaded yet.')
        self.assertFalse(app.button)
        self.download.assert_not_called()
        self.delete.assert_not_called()


class SourceExcelConfigurationTests(unittest.TestCase):
    def test_direct_visit_loads_bucket_from_dotenv_and_lists_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('BUCKET_NAME=test-source-bucket\nPROJECT_ID=test-project\n')
            older, newer = MagicMock(), MagicMock()
            for blob, day in ((older, 1), (newer, 2)):
                blob.name = f'sg-dashboard/source-excels/file-{day}.xlsx'
                blob.time_created = datetime(2026, 10, day, tzinfo=timezone.utc)
                blob.size = 10
                blob.content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            with patch.dict(os.environ, {}, clear=True), \
                    patch('dotenv.load_dotenv', side_effect=lambda: load_dotenv(env)), \
                    patch('google.cloud.storage.Client') as client:
                bucket = client.return_value.bucket.return_value
                bucket.list_blobs.return_value = [older, newer]
                result = source_storage.list_uploaded_excels()
                client.return_value.bucket.assert_called_once_with('test-source-bucket')
                self.assertEqual([item['file_name'] for item in result], ['file-2.xlsx', 'file-1.xlsx'])
                bucket.list_blobs.assert_called_once_with(prefix='sg-dashboard/source-excels/')
                bucket.blob.assert_not_called()

    def test_missing_bucket_raises_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(source_storage, '_load_gcp_access') as loader:
            with self.assertRaisesRegex(ValueError, 'BUCKET_NAME'):
                source_storage.list_uploaded_excels()
            loader.return_value.get_storage_client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
