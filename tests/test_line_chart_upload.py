from io import BytesIO
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from upload_page_module import upload_page

ACTIONS = upload_page.ACTIONS
inspect_workbook = upload_page.inspect_workbook
run_action = upload_page.run_action
from processing_report import record_upload
from tabs_scripts import line_chart


class LineChartUploadTests(unittest.TestCase):
    def test_micro_sheet_publishes_dashboard_state_and_district_once(self):
        for fail_dashboard in (False, True):
            with self.subTest(fail_dashboard=fail_dashboard), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'pages').mkdir()
                (root / 'tabs_scripts').mkdir()
                (root / 'pages' / 'dashboard.json').write_text('[{"type":"line-chart","data":[]}]')
                (root / 'pages' / 'state_code_details.json').write_text(
                    '{"Karnataka":{"id":"S1","Mysuru":"D1"}}')
                workbook = openpyxl.Workbook()
                workbook.active.title = 'Micro improvements progress'
                workbook.active.append(['State', 'District', 'Year', 'Q1', 'Q2', 'Q3', 'Q4'])
                workbook.active.append(['Karnataka', None, 2025, 1, 2, 3, 4])
                workbook.active.append(['Karnataka', 'Mysuru', 2025, 2, 3, 4, 5])
                stream = BytesIO()
                workbook.save(stream)
                data = stream.getvalue()
                destinations = []

                def upload(bucket_name, source_file_path, destination_blob_name):
                    destinations.append(destination_blob_name)
                    if fail_dashboard and destination_blob_name == 'sg-dashboard/dashboard.json':
                        record_upload(source_file_path, bucket_name, destination_blob_name, error='Storage denied')
                        return None
                    payload = Path(source_file_path).read_text()
                    record_upload(source_file_path, bucket_name, destination_blob_name, payload=payload)
                    return 'https://storage.googleapis.com/test/sg-dashboard'

                class Loader:
                    def create_module(self, spec):
                        return None
                    def exec_module(self, module):
                        module.upload_file_to_gcs_and_get_directory = upload

                fake_spec = importlib.util.spec_from_loader('fake_gcp', Loader())
                spec = next(item for item in ACTIONS if item.processor == 'line_chart.extract_micro_improvements')
                with patch.object(line_chart, '__file__', str(root / 'tabs_scripts' / 'line_chart.py')), patch(
                    'tabs_scripts.line_chart.importlib.util.spec_from_file_location', return_value=fake_spec
                ), patch.dict('os.environ', {'BUCKET_NAME': 'test'}):
                    result = run_action(spec, data, inspect_workbook(data), root, processor=line_chart.extract_micro_improvements)
                self.assertEqual(destinations, [
                    'sg-dashboard/dashboard.json', 'sg-dashboard/states/S1/line-chart.json',
                    'sg-dashboard/districts/D1/line-chart.json'])
                self.assertEqual(result['status'], 'Partial' if fail_dashboard else 'Uploaded')
                self.assertEqual(json.loads((root / 'pages' / 'dashboard.json').read_text())[0]['data'],
                                 [{'year': 2025, 'data': [1, 2, 3, 4]}])
                self.assertEqual(json.loads((root / 'districts' / 'D1' / 'line-chart.json').read_text())['data'],
                                 [{'year': 2025, 'data': [2, 3, 4, 5]}])
