"""Load helpers from the upload page without selecting a file or performing uploads."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'frontend-pages' / 'file-upload.py'
spec = importlib.util.spec_from_file_location('_upload_page_for_tests', path)
upload_page = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = upload_page
with patch('streamlit.file_uploader', return_value=None):
    spec.loader.exec_module(upload_page)
