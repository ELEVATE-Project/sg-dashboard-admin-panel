import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

import streamlit as st
from streamlit_ace import st_ace

from processing_report import dashboard_write_lock


ROOT = Path(__file__).resolve().parents[1]


def load_gcp_access():
    path = ROOT / 'cloud-scripts' / 'gcp_access.py'
    spec = importlib.util.spec_from_file_location('gcp_access', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_json_files():
    """Discover dashboard page JSON; generated state/district data stays excluded."""
    files = {}
    for path in sorted((ROOT / 'pages').glob('*.json')):
        if path.is_file() and not path.is_symlink():
            files[path.relative_to(ROOT).as_posix()] = path
    return files


def validate_path(path):
    path = Path(path).resolve()
    relative = path.relative_to(ROOT.resolve())
    if len(relative.parts) != 2 or relative.parts[0] != 'pages' or path.suffix.lower() != '.json':
        raise ValueError('Select a JSON file from the dashboard pages folder.')
    return path, relative


def destination_blob_name(path):
    _, relative = validate_path(path)
    return f'sg-dashboard/{relative.name}'


def parse_json(text):
    """Reject ambiguous keys and nonstandard numbers before changing any file."""
    def reject_constant(value):
        raise ValueError(f'Invalid JSON: {value} is not a valid JSON number. No changes were saved.')

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Invalid JSON: duplicate key {key!r}. No changes were saved.')
            result[key] = value
        return result

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def file_version(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def load_document(path):
    path, _ = validate_path(path)
    with dashboard_write_lock:
        raw = path.read_bytes()
    return {'text': raw.decode('utf-8-sig'), 'version': hashlib.sha256(raw).hexdigest(), 'revision': 0}


def ensure_unchanged(path, expected_version):
    if file_version(path) != expected_version:
        raise ValueError('This file changed after you opened it. Download your draft if needed, then reload the latest version before saving.')


def write_document(path, text):
    """Replace complete JSON atomically so readers cannot see a partial write."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def upload_json_to_gcs(path):
    gcp_access = load_gcp_access()  # Loads server .env configuration as well.
    bucket_name = os.environ.get('BUCKET_NAME')
    if not bucket_name:
        raise ValueError('BUCKET_NAME is not configured on the server.')
    return gcp_access.upload_file_to_gcs_and_get_directory(
        bucket_name=bucket_name, source_file_path=str(path),
        destination_blob_name=destination_blob_name(path),
    )


def save_document(path, edited_json, expected_version, publish=False):
    path, _ = validate_path(path)
    parsed = parse_json(edited_json)
    gcp_access = load_gcp_access()
    bucket_name = os.environ.get('BUCKET_NAME')
    if bucket_name:
        parsed = gcp_access.normalize_icon_urls(parsed, bucket_name)
    text = json.dumps(parsed, indent=2, ensure_ascii=False, allow_nan=False)
    with dashboard_write_lock:
        ensure_unchanged(path, expected_version)
        previous_text = path.read_text(encoding='utf-8-sig')
        write_document(path, text)
        result = {'text': text, 'previous_text': previous_text, 'version': file_version(path),
                  'upload_error': '', 'folder_url': None}
        if publish:
            try:
                result['folder_url'] = upload_json_to_gcs(path)
                if not result['folder_url']:
                    result['upload_error'] = 'Cloud storage did not confirm the upload.'
            except Exception as error:
                result['upload_error'] = str(error)
        return result


def reload_from_cloud(path, expected_version):
    path, _ = validate_path(path)
    gcp_access = load_gcp_access()
    bucket_name = os.environ.get('BUCKET_NAME')
    if not bucket_name:
        raise ValueError('BUCKET_NAME is not configured on the server.')
    client = gcp_access.get_storage_client()
    raw = client.bucket(bucket_name).blob(destination_blob_name(path)).download_as_bytes()
    text = json.dumps(parse_json(raw.decode('utf-8-sig')), indent=2, ensure_ascii=False, allow_nan=False)
    with dashboard_write_lock:
        ensure_unchanged(path, expected_version)
        write_document(path, text)
        return {'text': text, 'version': file_version(path)}


def main():
    st.title('JSON Editor & GCS Uploader')
    files = discover_json_files()
    if not files:
        st.info('No dashboard JSON files are available. Upload and process an Excel workbook first.')
        return

    name = st.selectbox('JSON file', list(files), key='_json_selected_pages')
    path = files[name]
    st.caption(f'Editing {name}')
    if path.name == 'outcomes-model-config.json':
        st.caption('Edit outcomes-model content and configuration here.')

    documents = st.session_state.setdefault('_json_documents', {})
    try:
        if name not in documents:
            documents[name] = load_document(path)
        document = documents[name]

        local_column, cloud_column = st.columns(2)
        with local_column:
            reload_local = st.button('Reload local file', key=f'_json_reload_local_{name}',
                                     help='Discard your draft and open the latest local file.')
        with cloud_column:
            reload_cloud = st.button('Reload from cloud', key=f'_json_reload_cloud_{name}',
                                     help='Discard your draft and replace the local file with the cloud version.')
        if reload_local or reload_cloud:
            revision = document['revision'] + 1
            document = (reload_from_cloud(path, document['version']) if reload_cloud else load_document(path))
            document['revision'] = revision
            documents[name] = document

        if file_version(path) != document['version']:
            st.warning('This file has newer changes. Save your draft using Download JSON, then reload the local file before saving.')
        try:
            parse_json(document['text'])
        except ValueError:
            st.warning('This file contains invalid JSON. You can correct it in the editor below.')

        edited_json = st_ace(value=document.get('draft', document['text']), language='json', theme='monokai',
                             key=f'_json_editor_{name}_{document["revision"]}', height=450,
                             font_size=14, auto_update=True)
        if edited_json is not None:
            document['draft'] = edited_json
        if document.get('draft', document['text']) != document['text']:
            st.caption('Unsaved changes in this draft.')
        save_column, upload_column, download_column = st.columns(3)
        with save_column:
            save = st.button('Save locally', key=f'_json_save_{name}', disabled=edited_json is None)
        with upload_column:
            publish = st.button('Save & upload', key=f'_json_publish_{name}', type='primary', disabled=edited_json is None)
        with download_column:
            st.download_button('Download JSON', document['text'] if edited_json is None else edited_json,
                               file_name=path.name, mime='application/json', key=f'_json_download_{name}')
        if save or publish:
            result = save_document(path, edited_json, document['version'], publish=publish)
            if result['upload_error']:
                notice = ('error', f'Saved locally, but cloud upload failed: {result["upload_error"]}')
            elif publish:
                notice = ('success', f'Saved and uploaded {name}.')
            else:
                notice = ('success', f'Saved {name} locally. Use Save & upload to publish it.')
            documents[name] = {**document, 'text': result['text'], 'draft': result['text'],
                               'version': result['version'], 'revision': document['revision'] + 1,
                               'notice': notice,
                               'comparison': {'old': result['previous_text'], 'new': result['text']}}
            st.rerun()
        notice = documents[name].get('notice')
        if notice:
            level, message = notice
            getattr(st, level)(f'Last save: {message}')
        comparison = documents[name].get('comparison')
        if comparison:
            with st.expander('Old and new JSON — last save', expanded=True):
                st.caption('Compares the local file before and after saving. Cloud upload status is shown separately above.')
                old_column, new_column = st.columns(2)
                with old_column:
                    st.markdown('**Old JSON**')
                    st.code(comparison['old'], language='json')
                with new_column:
                    st.markdown('**New JSON**')
                    st.code(comparison['new'], language='json')
    except json.JSONDecodeError as error:
        st.error(f'Invalid JSON: {error}. No changes were saved.')
    except Exception as error:
        st.error(str(error))


if __name__ == '__main__':
    main()
