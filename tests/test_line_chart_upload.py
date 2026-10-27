from datetime import date
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from constants import GCS_STORAGE_BASE_URL, LINE_CHART_QUARTERS, MICRO_IMPROVEMENTS_PROGRESS_SHEET
from processing_report import record_upload
from tabs_scripts import line_chart
from upload_page_module import upload_page


# Isolated fixture values, not defaults for production data or credentials.
SAMPLE_STATE = 'Example State'
SAMPLE_DISTRICT = 'Example District'
SAMPLE_STATE_ID = 'TEST_STATE'
SAMPLE_DISTRICT_ID = 'TEST_DISTRICT'
TEST_BUCKET = 'line-chart-test'
TEST_PUBLIC_BASE_URL = 'https://cdn.example.invalid/assets'
DASHBOARD_PREFIX = 'sg-dashboard'
STATE_QUARTER_VALUES = (1, 2, 3, 4)
DISTRICT_QUARTER_VALUES = (2, 3, 4, 5)


class LineChartUploadTests(unittest.TestCase):
    def test_micro_sheet_publishes_dashboard_state_and_district_once(self):
        reference_year = date.today().year
        years = (reference_year - 1, reference_year, reference_year + 1)
        dashboard_blob = f'{DASHBOARD_PREFIX}/dashboard.json'
        state_blob = f'{DASHBOARD_PREFIX}/states/{SAMPLE_STATE_ID}/line-chart.json'
        district_blob = f'{DASHBOARD_PREFIX}/districts/{SAMPLE_DISTRICT_ID}/line-chart.json'
        public_base_urls = (f'{GCS_STORAGE_BASE_URL}/{TEST_BUCKET}', TEST_PUBLIC_BASE_URL)

        for public_base_url in public_base_urls:
            for fail_dashboard in (False, True):
                with self.subTest(public_base_url=public_base_url, fail_dashboard=fail_dashboard), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / 'pages').mkdir()
                    (root / 'tabs_scripts').mkdir()
                    (root / 'pages' / 'dashboard.json').write_text(json.dumps([{'type': 'line-chart', 'data': []}]))
                    (root / 'pages' / 'state_code_details.json').write_text(json.dumps({
                        SAMPLE_STATE: {'id': SAMPLE_STATE_ID, SAMPLE_DISTRICT: SAMPLE_DISTRICT_ID},
                    }))
                    workbook = openpyxl.Workbook()
                    workbook.active.title = MICRO_IMPROVEMENTS_PROGRESS_SHEET
                    workbook.active.append(['State', 'District', 'Year', *LINE_CHART_QUARTERS])
                    # Reverse input order also verifies that output years are sorted.
                    for year in reversed(years):
                        workbook.active.append([SAMPLE_STATE, None, year, *STATE_QUARTER_VALUES])
                        workbook.active.append([SAMPLE_STATE, SAMPLE_DISTRICT, year, *DISTRICT_QUARTER_VALUES])
                    stream = BytesIO()
                    workbook.save(stream)
                    workbook.close()
                    data = stream.getvalue()
                    destinations = []
                    public_urls = []

                    def upload(bucket_name, source_file_path, destination_blob_name):
                        self.assertEqual(bucket_name, os.environ['BUCKET_NAME'])
                        destinations.append(destination_blob_name)
                        if fail_dashboard and destination_blob_name == dashboard_blob:
                            record_upload(source_file_path, bucket_name, destination_blob_name, error='Storage denied')
                            return None
                        payload = Path(source_file_path).read_text()
                        record_upload(source_file_path, bucket_name, destination_blob_name, payload=payload)
                        base_url = os.environ['GCS_PUBLIC_BASE_URL'].rstrip('/')
                        folder = destination_blob_name.rsplit('/', 1)[0]
                        public_url = f'{base_url}/{folder}'
                        public_urls.append(public_url)
                        return public_url

                    class Loader:
                        def create_module(self, spec):
                            return None

                        def exec_module(self, module):
                            module.upload_file_to_gcs_and_get_directory = upload

                    fake_spec = importlib.util.spec_from_loader('fake_gcp', Loader())
                    spec = next(item for item in upload_page.ACTIONS if item.processor == 'line_chart.extract_micro_improvements')
                    # Inject test configuration instead of reading a developer's .env.
                    with patch.object(line_chart, '__file__', str(root / 'tabs_scripts' / 'line_chart.py')), patch(
                        'tabs_scripts.line_chart.importlib.util.spec_from_file_location', return_value=fake_spec
                    ), patch.dict(os.environ, {'BUCKET_NAME': TEST_BUCKET, 'GCS_PUBLIC_BASE_URL': public_base_url}):
                        result = upload_page.run_action(spec, data, upload_page.inspect_workbook(data), root,
                                                        processor=line_chart.extract_micro_improvements)
                    self.assertEqual(destinations, [dashboard_blob, state_blob, district_blob])
                    self.assertTrue(all(url.startswith(public_base_url + '/') for url in public_urls))
                    self.assertEqual(result['status'], 'Partial' if fail_dashboard else 'Uploaded')
                    expected_state_data = [{'year': year, 'data': list(STATE_QUARTER_VALUES)} for year in years]
                    expected_district_data = [{'year': year, 'data': list(DISTRICT_QUARTER_VALUES)} for year in years]
                    self.assertEqual(json.loads((root / 'pages' / 'dashboard.json').read_text())[0]['data'], expected_state_data)
                    self.assertEqual(json.loads((root / 'states' / SAMPLE_STATE_ID / 'line-chart.json').read_text())['data'], expected_state_data)
                    self.assertEqual(json.loads((root / 'districts' / SAMPLE_DISTRICT_ID / 'line-chart.json').read_text())['data'], expected_district_data)
