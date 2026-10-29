import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from processing_report import dashboard_write_lock
from upload_page_module import upload_page

PAGE = Path(__file__).resolve().parents[1] / 'frontend-pages' / 'json-editor.py'
spec = importlib.util.spec_from_file_location('json_editor_for_tests', PAGE)
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)


def preview_editor(page_path, root_path, fail_upload=False):
    import importlib.util
    from pathlib import Path
    import types
    import streamlit as st
    spec = importlib.util.spec_from_file_location('json_editor_preview', page_path)
    editor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(editor)
    editor.ROOT = Path(root_path)
    editor.st_ace = lambda value, key, **kwargs: st.text_area('JSON content', value=value, key=key)
    editor.load_gcp_access = lambda: types.SimpleNamespace(
        normalize_icon_urls=lambda data, bucket: data,
        upload_file_to_gcs_and_get_directory=lambda **kwargs: None if fail_upload else 'https://example.invalid/uploaded',
    )
    editor.main()


class JsonEditorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = {}
        fixtures = {
            'pages/landing-page.json': [{'type': 'indicators', 'value': 1}],
            'pages/outcomes-model-config.json': {'layers': [{'key': 'system', 'body': 'Original outcome'}]},
            'pages/network-data.json': {'partners': []},
            'states/TEST_STATE/state-program.json': [{'name_of_the_program': 'Example', 'framework': []}],
            'districts/TEST_DISTRICT/SLC.json': [{'name_of_the_program': 'Example', 'framework': []}],
            'districts/TEST_DISTRICT/WLC.json': [],
        }
        for name, data in fixtures.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
            self.paths[name] = path
        (self.root / 'private-key.json').write_text('{"private":true}')
        root_patch = patch.object(editor, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.gcp = MagicMock()
        self.gcp.normalize_icon_urls.side_effect = lambda data, bucket: data
        self.gcp.upload_file_to_gcs_and_get_directory.return_value = 'https://example.invalid/sg-dashboard'
        gcp_patch = patch.object(editor, 'load_gcp_access', return_value=self.gcp)
        gcp_patch.start()
        self.addCleanup(gcp_patch.stop)
        env_patch = patch.dict(os.environ, {'BUCKET_NAME': 'json-editor-test', 'GOOGLE_APPLICATION_CREDENTIALS': '/keep/credentials.json'})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def app(self, fail_upload=False):
        app = AppTest.from_function(preview_editor, args=(str(PAGE), str(self.root), fail_upload)).run()
        self.assertFalse(app.exception)
        return app

    def test_catalog_includes_only_dashboard_pages(self):
        self.assertEqual(set(editor.discover_json_files()),
                         {name for name in self.paths if name.startswith('pages/')})
        added = self.root / 'pages' / 'new-upload-output.json'
        added.write_text('{}')
        self.assertIn('pages/new-upload-output.json', editor.discover_json_files())

    def test_cloud_paths_include_only_page_filenames(self):
        for name, path in self.paths.items():
            if name.startswith('pages/'):
                self.assertEqual(editor.destination_blob_name(path), f'sg-dashboard/{path.name}')
            else:
                with self.assertRaises(ValueError):
                    editor.destination_blob_name(path)
                with self.assertRaises(ValueError):
                    editor.save_document(path, '{}', editor.file_version(path), publish=True)
                with self.assertRaises(ValueError):
                    editor.reload_from_cloud(path, editor.file_version(path))

    def test_save_page_edits_and_publish_to_their_own_destinations(self):
        for name in ('pages/outcomes-model-config.json', 'pages/landing-page.json'):
            with self.subTest(name=name):
                path = self.paths[name]
                original = editor.load_document(path)
                data = json.loads(original['text'])
                if isinstance(data, dict):
                    data['layers'][0]['body'] = 'Edited outcome'
                else:
                    data[0]['value'] = 2
                result = editor.save_document(path, json.dumps(data), original['version'], publish=True)
                self.assertEqual(result['previous_text'], original['text'])
                self.assertEqual(json.loads(path.read_text()), data)
                self.assertFalse(result['upload_error'])
                self.assertEqual(self.gcp.upload_file_to_gcs_and_get_directory.call_args.kwargs['destination_blob_name'],
                                 editor.destination_blob_name(path))
        self.assertEqual(os.environ['GOOGLE_APPLICATION_CREDENTIALS'], '/keep/credentials.json')

    def test_invalid_json_and_stale_edits_do_not_overwrite_files(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        with self.assertRaises(json.JSONDecodeError):
            editor.save_document(path, '{invalid', document['version'], publish=True)
        self.assertEqual(path.read_text(), document['text'])
        path.write_text('{"newer":true}')
        with self.assertRaisesRegex(ValueError, 'changed after you opened'):
            editor.save_document(path, '{"older":true}', document['version'], publish=True)
        self.assertEqual(json.loads(path.read_text()), {'newer': True})
        self.gcp.upload_file_to_gcs_and_get_directory.assert_not_called()

    def test_failed_upload_retains_local_save_and_reports_failure(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        self.gcp.upload_file_to_gcs_and_get_directory.return_value = None
        result = editor.save_document(path, '{"changed":true}', document['version'], publish=True)
        self.assertEqual(json.loads(path.read_text()), {'changed': True})
        self.assertTrue(result['upload_error'])
        self.assertEqual(result['version'], editor.file_version(path))

    def test_ambiguous_keys_and_nonstandard_numbers_cannot_be_saved_or_reloaded(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        blob = self.gcp.get_storage_client.return_value.bucket.return_value.blob.return_value
        for text in ('{"key":1,"key":2}', '{"nested":{"key":1,"key":2}}',
                     '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}', '{"value":1e999}'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    editor.save_document(path, text, document['version'], publish=True)
                blob.download_as_bytes.return_value = text.encode()
                with self.assertRaises(ValueError):
                    editor.reload_from_cloud(path, document['version'])
                self.assertEqual(path.read_text(), document['text'])
        self.gcp.upload_file_to_gcs_and_get_directory.assert_not_called()

    def test_cloud_configuration_is_loaded_before_bucket_validation(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        def configured_helper():
            os.environ['BUCKET_NAME'] = 'loaded-from-server-env'
            return self.gcp
        with patch.dict(os.environ), patch.object(editor, 'load_gcp_access', side_effect=configured_helper):
            os.environ.pop('BUCKET_NAME', None)
            result = editor.save_document(path, '{"changed":true}', document['version'], publish=True)
        self.assertFalse(result['upload_error'])
        self.assertEqual(self.gcp.upload_file_to_gcs_and_get_directory.call_args.kwargs['bucket_name'], 'loaded-from-server-env')

    def test_reload_cloud_uses_correct_blob_and_does_not_overwrite_newer_local_changes(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        blob = self.gcp.get_storage_client.return_value.bucket.return_value.blob.return_value
        blob.download_as_bytes.return_value = b'{"layers":[{"key":"system"}]}'
        result = editor.reload_from_cloud(path, document['version'])
        self.gcp.get_storage_client.return_value.bucket.return_value.blob.assert_called_with(
            'sg-dashboard/outcomes-model-config.json')
        self.assertEqual(json.loads(result['text']), {'layers': [{'key': 'system'}]})
        with self.assertRaisesRegex(ValueError, 'changed after you opened'):
            editor.reload_from_cloud(path, document['version'])

    def test_paths_outside_dashboard_data_and_symlinks_are_excluded(self):
        with self.assertRaises(ValueError):
            editor.save_document(self.root / 'private-key.json', '{}', None)
        link = self.root / 'pages' / 'credentials-link.json'
        link.symlink_to(self.root / 'private-key.json')
        self.assertNotIn('pages/credentials-link.json', editor.discover_json_files())
        with self.assertRaises(ValueError):
            editor.destination_blob_name(link)

    def test_failed_atomic_replace_leaves_original_intact(self):
        path = self.paths['pages/outcomes-model-config.json']
        document = editor.load_document(path)
        with patch.object(editor.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                editor.save_document(path, '{"changed":true}', document['version'])
        self.assertEqual(path.read_text(), document['text'])
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_json_editor_and_excel_processing_share_the_same_write_lock(self):
        self.assertIs(upload_page._processing_lock(), dashboard_write_lock)
        self.assertIs(editor.dashboard_write_lock, dashboard_write_lock)

    def test_ui_lists_only_pages_and_outcome_content_is_editable(self):
        app = self.app()
        self.assertEqual(len(app.selectbox), 1)
        self.assertEqual(set(app.selectbox[0].options),
                         {name for name in self.paths if name.startswith('pages/')})
        app.selectbox(key='_json_selected_pages').set_value('pages/outcomes-model-config.json').run()
        app.text_area[0].set_value('{"layers":[{"body":"Edited in UI"}]}').run()
        next(button for button in app.button if button.label == 'Save & upload').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.success)
        self.assertEqual(json.loads(self.paths['pages/outcomes-model-config.json'].read_text()),
                         {'layers': [{'body': 'Edited in UI'}]})
        self.assertEqual(json.loads(app.code[0].value),
                         {'layers': [{'key': 'system', 'body': 'Original outcome'}]})
        self.assertEqual(json.loads(app.code[1].value), {'layers': [{'body': 'Edited in UI'}]})
        app.run()
        self.assertEqual(len(app.code), 2)
        self.assertTrue(any('Saved and uploaded' in item.value for item in app.success))
        self.assertEqual(app.text_area[0].value, self.paths['pages/outcomes-model-config.json'].read_text())
        app.text_area[0].set_value('{"layers":[]}').run()
        next(button for button in app.button if button.label == 'Save locally').click().run()
        self.assertEqual(json.loads(app.code[0].value), {'layers': [{'body': 'Edited in UI'}]})
        self.assertEqual(json.loads(app.code[1].value), {'layers': []})

    def test_ui_keeps_failed_upload_status_and_saved_comparison(self):
        app = self.app(fail_upload=True)
        name = app.selectbox[0].value
        original = self.paths[name].read_text()
        app.text_area[0].set_value('{"updated":true}').run()
        self.assertTrue(any('Unsaved changes' in item.value for item in app.caption))
        next(button for button in app.button if button.label == 'Save & upload').click().run()
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any('cloud upload failed' in item.value for item in app.error))
        self.assertFalse(app.success)
        self.assertEqual(app.code[0].value, original)
        self.assertEqual(json.loads(app.code[1].value), {'updated': True})
        self.assertEqual(app.text_area[0].value, self.paths[name].read_text())
        self.assertFalse(any('Unsaved changes' in item.value for item in app.caption))

    def test_ui_reports_invalid_json_and_newer_file_conflicts(self):
        app = self.app()
        name = app.selectbox(key='_json_selected_pages').value
        original = self.paths[name].read_text()
        app.text_area[0].set_value('{broken').run()
        next(button for button in app.button if button.label == 'Save locally').click().run()
        self.assertTrue(any('Invalid JSON' in error.value for error in app.error))
        self.assertEqual(self.paths[name].read_text(), original)
        app.text_area[0].set_value('{"draft":true}').run()
        self.paths[name].write_text('{"newer":true}')
        next(button for button in app.button if button.label == 'Save locally').click().run()
        self.assertTrue(any('changed after you opened' in error.value for error in app.error))
        self.assertEqual(json.loads(self.paths[name].read_text()), {'newer': True})

    def test_unsaved_draft_survives_switching_files(self):
        app = self.app()
        original_name = app.selectbox(key='_json_selected_pages').value
        original_file = self.paths[original_name].read_text()
        draft = '{"unsaved":"keep this draft"}'
        app.text_area[0].set_value(draft).run()
        app.selectbox(key='_json_selected_pages').set_value('pages/outcomes-model-config.json').run()
        app.selectbox(key='_json_selected_pages').set_value(original_name).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_area[0].value, draft)
        self.assertEqual(self.paths[original_name].read_text(), original_file)

    def test_reload_local_fetches_excel_changes_and_empty_json_can_be_saved(self):
        app = self.app()
        name = app.selectbox(key='_json_selected_pages').value
        self.paths[name].write_text('{"from_excel":true}')
        next(button for button in app.button if button.label == 'Reload local file').click().run()
        self.assertEqual(json.loads(app.text_area[0].value), {'from_excel': True})
        app.text_area[0].set_value('[]').run()
        next(button for button in app.button if button.label == 'Save locally').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(json.loads(self.paths[name].read_text()), [])
