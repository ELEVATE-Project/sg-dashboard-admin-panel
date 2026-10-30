import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
from io import BytesIO
import json
from pathlib import Path
import re

import openpyxl
import pandas as pd
import streamlit as st

from constants import PAGE_METADATA, TABS_METADATA, LEADERS_ENGAGED_SHEET
from processing_report import _current_report, dashboard_write_lock, report_print
from admin_auth import persist_upload_state


ROOT = Path(__file__).resolve().parents[1]
_current_step = ContextVar('excel_upload_step', default=None)


@dataclass(frozen=True)
class Action:
    sheet: str
    processor: str
    headers: tuple = ()
    dependencies: tuple = ()


def action(sheet, processor, metadata=None, dependencies=()):
    return Action(sheet, processor, tuple(TABS_METADATA.get(metadata, ())), dependencies)


# Processor validation metadata. The upload page controls its execution order
# with explicit function calls; comment those calls to disable bulk steps.
ACTIONS = (
    action(PAGE_METADATA['HOME_PAGE'], 'key_progress_indicators.key_progress_indicators', 'HOME_PAGE'),
    action(PAGE_METADATA['PARTNERS'], 'partners.get_partners', 'PARTNERS'),
    action(PAGE_METADATA['NETWORK_MAP'], 'network_map_data.get_network_map_data', 'NETWORK_MAP'),
    action(PAGE_METADATA['STATE_DETAILS'], 'extract_state_details.update_district_view_indicators', 'STATE_DETAILS',
           (PAGE_METADATA['HOME_PAGE'], PAGE_METADATA['STATE_DISTRICT_DETAILS'])),
    action(PAGE_METADATA['DISTRICT_DETAILS'], 'extract_district_details.extract_district_details', 'DISTRICT_DETAILS'),
    action(PAGE_METADATA['DASHBOARD_FIRST_PAGE'], 'pie_chart.pie_chart', 'PIE_CHART'),
    action(PAGE_METADATA['TESTIMONIALS'], 'testimonials.testimonials', 'TESTIMONIALS'),
    action(PAGE_METADATA['NEW_COMMUNITY_LED_PROGRAMS'], 'community_led_details.community_led_programs_sum_with_codes',
           dependencies=(PAGE_METADATA['STATE_DISTRICT_DETAILS'],)),
    # Program descriptions, media and report links are optional in this parser.
    Action(PAGE_METADATA['PROGRAMS'], 'programs.generate_program_reports', tuple(TABS_METADATA['PROGRAMS'][:4])),
    action(PAGE_METADATA['NEW_COMMUNITY_LED_PROGRAMS'], 'extract_community_details.extract_community_details'),
    action('Micro improvements progress', 'line_chart.extract_micro_improvements'),
    action(PAGE_METADATA['UPLOAD_IMAGES'], 'upload_images_from_excel.upload_images_from_excel', 'UPLOAD_IMAGES'),
    Action(PAGE_METADATA['VOICE_TAB_BIG_NUMBERS'], 'voices_tab_big_numbers.voices_tab_big_numbers',
           tuple(TABS_METADATA['VOICE_TAB_BIG_NUMBERS'][2:])),
    action('Content Requirements', 'outcomes_model_from_excel.generate_outcomes_model_json'),
    action('Graph_VoiceTab_MI', 'line_chart.update_voices_json_line_chart', dependencies=('Micro improvements progress',)),
    action(LEADERS_ENGAGED_SHEET, 'line_chart.update_leaders_engaged_dual_axis_chart'),
    action(PAGE_METADATA['GOALS'], 'goals.goals', 'GOALS'),
    action(PAGE_METADATA['IMPACT_PROGRAMS'], 'impact_programs.update_impact_program_frameworks', 'IMPACT_PROGRAMS'),
)
SUPPORT_SHEETS = {PAGE_METADATA['STATE_DISTRICT_DETAILS'], 'Enrollments', 'Documentation'}


def normalize_name(name):
    return re.sub(r'[^a-z0-9]', '', name.strip().lower())


def inspect_workbook(file_bytes):
    workbook = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
    try:
        inventory = {}
        for sheet in workbook:
            rows = sheet.iter_rows(values_only=True)
            headers = [str(value).strip() if value is not None else '' for value in next(rows, ())]
            count = sum(any(value is not None for value in row) for row in rows)
            inventory[sheet.title] = {'rows': count, 'headers': headers}
        return inventory
    finally:
        workbook.close()


def snapshot_outputs(root):
    outputs = {}
    for directory in ('pages', 'states', 'districts'):
        for path in (root / directory).rglob('*.json'):
            outputs[str(path.relative_to(root))] = path.read_bytes()
    return outputs


def json_changes(before, after, path=''):
    """JSON pointers show exactly which values were added, removed, or replaced."""
    if type(before) is type(after) and isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if key not in before:
                yield {'path': pointer, 'change': 'added', 'after': after[key]}
            elif key not in after:
                yield {'path': pointer, 'change': 'removed', 'before': before[key]}
            else:
                yield from json_changes(before[key], after[key], pointer)
    elif type(before) is type(after) and isinstance(before, list):
        for index in range(max(len(before), len(after))):
            pointer = path + '/' + str(index)
            if index >= len(before):
                yield {'path': pointer, 'change': 'added', 'after': after[index]}
            elif index >= len(after):
                yield {'path': pointer, 'change': 'removed', 'before': before[index]}
            else:
                yield from json_changes(before[index], after[index], pointer)
    elif type(before) is not type(after) or before != after:
        yield {'path': path or '/', 'change': 'replaced', 'before': before, 'after': after}


def _preflight(spec, inventory):
    if spec.sheet not in inventory:
        similar = [name for name in inventory if normalize_name(name) == normalize_name(spec.sheet)]
        if similar:
            return 'Failed', f'Rename sheet {similar[0]!r} to the required name {spec.sheet!r}.'
        return 'Skipped', f'Sheet {spec.sheet!r} is not in this workbook.'
    if not inventory[spec.sheet]['rows']:
        return 'Skipped', 'Sheet has no data rows; existing dashboard data was left unchanged.'
    missing = [name for name in spec.dependencies if name not in inventory]
    if missing:
        return 'Failed', f'Missing supporting sheets: {", ".join(repr(name) for name in missing)}.'
    empty = [name for name in spec.dependencies if not inventory[name]['rows']]
    if empty:
        return 'Failed', f'Supporting sheets have no data rows: {", ".join(repr(name) for name in empty)}. Existing data was left unchanged.'
    headers = inventory[spec.sheet]['headers']
    missing = [header for header in spec.headers if header.strip() not in headers]
    if missing:
        return 'Failed', f'Missing required columns: {", ".join(missing)}.'
    return None


def run_action(spec, file_bytes, inventory, root=ROOT, *, processor=None):
    report = {'sheet': spec.sheet, 'processor': spec.processor,
              'step': spec.processor.split('.')[-1],
              'rows': inventory.get(spec.sheet, {}).get('rows', 0),
              'status': 'Running', 'summary': '', 'messages': [], 'uploads': [], 'outputs': []}
    issue = _preflight(spec, inventory)
    if issue:
        report['status'], report['summary'] = issue
        return report
    token = _current_report.set(report)
    try:
        before = snapshot_outputs(root)
        try:
            if processor is None:
                module_name, function_name = spec.processor.split('.')
                processor = getattr(import_module('tabs_scripts.' + module_name), function_name)
            # Each processor receives a fresh stream, independent of prior reads.
            result = processor(BytesIO(file_bytes))
            if result is False:
                report_print('Error: Processor returned failure.')
        except SystemExit as error:
            # Legacy scripts use exit() on invalid data. Report the failed step
            # without terminating the rest of the workbook processing.
            report_print(f'Error: Processor exited with status {error.code}.')
        except Exception as error:
            report_print(f'Error: {error}')
        after = snapshot_outputs(root)
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) == after.get(name):
                continue
            old = json.loads(before[name]) if name in before else None
            new = json.loads(after[name]) if name in after else None
            changes = list(json_changes(old, new))
            if changes:
                report['outputs'].append({'file': name, 'changes': changes, 'data': new})
        uploaded = sum(item['status'] == 'uploaded' for item in report['uploads'])
        errors = report.get('has_errors', False) or any(
            item['status'] == 'failed' for item in report['uploads'])
        warnings = report.get('has_warnings', False)
        if errors:
            report['status'] = 'Partial' if uploaded or report['outputs'] else 'Failed'
            report['summary'] = 'Errors occurred. Review diagnostics and confirmed uploads below.'
        elif uploaded:
            report['status'] = 'Warning' if warnings else 'Uploaded'
            report['summary'] = f'{uploaded} cloud upload(s) confirmed; {len(report["outputs"])} local JSON file(s) changed.'
        else:
            report['status'] = 'Warning'
            report['summary'] = ('Local data changed, but no cloud upload was confirmed.' if report['outputs']
                                 else 'No data changes or cloud uploads were confirmed. Review diagnostics.')
    except Exception as error:
        report['status'] = 'Failed'
        report['summary'] = f'Could not complete processing or verify its result: {error}'
    finally:
        _current_report.reset(token)
    return report


@contextmanager
def reporting_steps(run_step):
    """Route calls made by this upload page through its reporting callback."""
    token = _current_step.set(run_step)
    try:
        yield
    finally:
        _current_step.reset(token)


def bind_upload_reporting(namespace):
    """Wrap imported page aliases, leaving the original processor modules intact.

    This only attaches reporting; it never executes a processor. The explicit
    calls in the page remain the source of truth for which scripts run.
    """
    names = {spec.processor.split('.')[-1] for spec in ACTIONS}
    names.add('pie_chart_community_led')

    def wrap(function):
        @wraps(function)
        def reported(source):
            run_step = _current_step.get()
            if run_step is None:
                return function(source)
            return run_step(function, source)
        return reported

    for name in names:
        if name in namespace:
            namespace[name] = wrap(namespace[name])


@st.cache_resource
def _processing_lock():
    # The page is reloaded on reruns; keep one lock across browser sessions.
    return dashboard_write_lock


@contextmanager
def processing_session():
    """Serialize writes while the upload page runs its explicit processing calls."""
    with _processing_lock():
        yield


def sanitize_dataframe(df):
    display = df.copy()
    for column in display.columns:
        if display[column].dtype == 'object':
            display[column] = display[column].astype(str)
    return display


def result_rows(results):
    return [{
        'Sheet': result['sheet'],
        'Step': result['step'],
        'Status': result['status'],
        'Source rows': result['rows'],
        'Confirmed uploads': sum(item['status'] == 'uploaded' for item in result['uploads']),
        'Changed JSON files': len(result['outputs']),
        'Details': result['summary'],
    } for result in results]


def display_report(results):
    st.subheader('Processing results')
    problems = [item for item in results if item['status'] in ('Failed', 'Partial', 'Warning')]
    confirmed = sum(item['status'] == 'uploaded' for result in results for item in result['uploads'])
    skipped = sum(item['status'] == 'Skipped' for item in results)
    if problems:
        st.warning(f'{len(problems)} step(s) need attention. {confirmed} cloud upload(s) confirmed; {skipped} step(s) skipped. Review each result below.')
    elif confirmed:
        st.success(f'Processing finished: {confirmed} cloud upload(s) confirmed; {skipped} step(s) skipped.')
    else:
        st.warning('No cloud uploads were confirmed. Your dashboard may not have changed.')
    st.caption('Source rows are rows available in the sheet, not a count of records saved. Changes below compare local JSON before and after each step; cloud receipts show the payload accepted by storage, not a website refresh check.')
    st.dataframe(pd.DataFrame(result_rows(results)), hide_index=True, width='stretch')
    for result in results:
        with st.expander(f"{result['status']} · {result['sheet']} · {result['step']}",
                         expanded=result['status'] in ('Failed', 'Partial', 'Warning')):
            st.write(result['summary'])
            for item in result['messages']:
                if item['level'] == 'error':
                    st.error(item['message'])
                elif item['level'] == 'warning':
                    st.warning(item['message'])
            if result['uploads']:
                st.markdown('**Cloud upload results**')
                st.dataframe(pd.DataFrame([{key: item[key] for key in ('destination', 'status', 'error')}
                                           for item in result['uploads']]), hide_index=True, width='stretch')
                for item in result['uploads']:
                    if 'data' in item:
                        with st.expander(f"Uploaded JSON · {item['destination']}"):
                            st.json(item['data'], expanded=False)
            if result['outputs']:
                st.markdown('**Local JSON changes**')
                for output in result['outputs']:
                    with st.expander(f"{output['file']} · {len(output['changes'])} change(s)"):
                        st.json(output['changes'], expanded=False)
                        st.caption('Resulting JSON')
                        st.json(output['data'], expanded=False)
            with st.expander('Processing diagnostics'):
                st.code('\n'.join(item['message'] for item in result['messages']) or 'No processor diagnostics were emitted.')
                if result.get('omitted_messages'):
                    st.caption(f"{result['omitted_messages']} additional diagnostic messages omitted. Output data remains available above.")
    st.download_button('Download processing report', json.dumps(results, indent=2, ensure_ascii=False),
                       file_name='excel-processing-report.json', mime='application/json')


@contextmanager
def process(file_bytes, inventory, upload_key, status):
    # Archive storage and dashboard publication are separate operations.
    if st.session_state.get('_excel_archive_key') != upload_key:
        from tabs_scripts.upload_excel_to_gcs import upload_excel_to_gcs
        source = BytesIO(file_bytes)
        source.name = uploaded_file.name
        archive_report = {'messages': [], 'uploads': []}
        token = _current_report.set(archive_report)
        try:
            with st.spinner('Saving source Excel…'):
                details = upload_excel_to_gcs(source)
        finally:
            _current_report.reset(token)
            st.session_state['_excel_archive_messages'] = archive_report['messages']
        st.session_state['_excel_archive'] = details
        if details:
            st.session_state['_excel_archive_key'] = upload_key

    results = []
    st.session_state['_excel_results'] = results
    specs = {spec.processor.split('.')[-1]: spec for spec in ACTIONS}
    # This optional step remains disabled in the bulk upload below.
    specs['pie_chart_community_led'] = Action('Community Led Programs', 'community_led_details.pie_chart_community_led')
    with processing_session():
        live_results = st.empty()

        def run_step(upload_function, source):
            # Keep the calls in this page; the helper only validates and reports them.
            spec = specs[upload_function.__name__]
            status.update(label=f'Processing {spec.sheet}…')
            result = run_action(spec, source.getvalue(), inventory, processor=upload_function)
            results.append(result)
            st.session_state['_excel_results'] = results
            live_results.dataframe(pd.DataFrame(result_rows(results)), hide_index=True, width='stretch')

        with reporting_steps(run_step):
            yield
        needs_attention = any(result['status'] in ('Failed', 'Partial', 'Warning') for result in results)
        status.update(label='Processing finished — review results below' if needs_attention else 'Processing finished',
                      state='error' if needs_attention else 'complete', expanded=False)
        live_results.empty()
        persist_upload_state()


def clear_removed_upload():
    if st.session_state.get('_dashboard_workbook') is None:
        for key in ('_last_uploaded_excel', '_excel_current_key', '_excel_results',
                    '_excel_archive', '_excel_archive_key', '_excel_archive_messages'):
            st.session_state.pop(key, None)
        persist_upload_state()


uploaded_file = st.file_uploader('Choose a file', type=['csv', 'txt', 'xlsx'],
                                key='_dashboard_workbook', on_change=clear_removed_upload)
if uploaded_file is None and st.session_state.get('_last_uploaded_excel'):
    retained = st.session_state['_last_uploaded_excel']
    uploaded_file = BytesIO(retained['data'])
    uploaded_file.name = retained['name']
    st.caption(f"Current file: {uploaded_file.name}")
if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    st.session_state['_last_uploaded_excel'] = {'name': uploaded_file.name, 'data': file_bytes}
    file_type = uploaded_file.name.rsplit('.', 1)[-1].lower()
    upload_key = (uploaded_file.name, hashlib.sha256(file_bytes).hexdigest())
    if st.session_state.get('_excel_current_key') != upload_key:
        for key in ('_excel_results', '_excel_archive', '_excel_archive_key', '_excel_archive_messages'):
            st.session_state.pop(key, None)
        st.session_state['_excel_current_key'] = upload_key
    persist_upload_state()
    try:
        if file_type in ('csv', 'txt'):
            st.info('File received for preview. Dashboard processing requires an Excel (.xlsx) workbook.')
            frame = pd.read_csv(BytesIO(file_bytes), sep='\t' if file_type == 'txt' else ',')
            st.dataframe(sanitize_dataframe(frame), height=400)
        elif file_type == 'xlsx':
            inventory = inspect_workbook(file_bytes)
            # ✅ Import script functions
            from tabs_scripts.community_led_details import community_led_programs_sum_with_codes, pie_chart_community_led
            from tabs_scripts.goals import goals
            from tabs_scripts.key_progress_indicators import key_progress_indicators
            from tabs_scripts.line_chart import extract_micro_improvements, update_voices_json_line_chart, update_leaders_engaged_dual_axis_chart
            from tabs_scripts.network_map_data import get_network_map_data
            from tabs_scripts.partners import get_partners
            from tabs_scripts.extract_state_details import update_district_view_indicators
            from tabs_scripts.pie_chart import pie_chart
            from tabs_scripts.testimonials import testimonials
            # This module initializes Drive credentials on import. Keep an import
            # failure confined to its own reported step so other sheets can run.
            try:
                from tabs_scripts.programs import generate_program_reports
            except Exception as error:
                program_import_error = str(error)

                def generate_program_reports(source):
                    raise RuntimeError(f'Could not load program reports: {program_import_error}')

            from tabs_scripts.extract_district_details import extract_district_details
            from tabs_scripts.extract_community_details import extract_community_details
            from tabs_scripts.upload_images_from_excel import upload_images_from_excel
            from tabs_scripts.outcomes_model_from_excel import generate_outcomes_model_json
            from tabs_scripts.voices_tab_big_numbers import voices_tab_big_numbers
            from tabs_scripts.impact_programs import update_impact_program_frameworks

            # Attach reporting to this page's imported functions. Scripts run
            # only when explicitly called below; commented calls stay disabled.
            bind_upload_reporting(globals())

            # ✅ Each sheet maps to an ordered sequence, even when it has one step.
            # Comment a function in these tuples to disable it for individual uploads.
            # Bulk uploads remain controlled by the explicit calls at the bottom.
            upload_actions = {
                'Data on homepage': (
                    key_progress_indicators,
                    update_district_view_indicators,  # Also reads homepage indicators.
                ),
                'Dashboard first page': (pie_chart,),
                'Goals': (goals,),
                'States details': (update_district_view_indicators,),
                'District Details': (extract_district_details,),
                'Programs': (
                    generate_program_reports,
                    update_impact_program_frameworks,  # Reattach frameworks after regeneration.
                ),
                'Micro improvements progress': (
                    # This already calls state charts, which call district charts.
                    extract_micro_improvements,
                    update_voices_json_line_chart,
                ),
                'Graph_VoiceTab_MI': (update_voices_json_line_chart,),
                'Partners': (get_partners,),
                'Network Map': (get_network_map_data,),
                'Testimonials': (testimonials,),
                'Imagesicons': (upload_images_from_excel,),
                'Voices Tab Big Numbers': (voices_tab_big_numbers,),
                'Content Requirements': (generate_outcomes_model_json,),
                'Impact_Programs': (update_impact_program_frameworks,),
                'New_Community led Programs ': (community_led_programs_sum_with_codes, extract_community_details),
                'Leaders and community members_v': (update_leaders_engaged_dual_axis_chart,),
            }

            # ✅ Upload All Files Button (Above Sheet Previews)
            st.markdown("---")
            if st.button("🚀 Upload all files", type='primary'):
                try:
                    with st.status("🔄 Uploading and processing all allowed sheets...", expanded=True) as status:
                        with process(file_bytes, inventory, upload_key, status):
                            # Comment any script call below to disable that bulk step.
                            key_progress_indicators(uploaded_file)
                            get_partners(uploaded_file)
                            get_network_map_data(uploaded_file)
                            update_district_view_indicators(uploaded_file)
                            extract_district_details(uploaded_file)
                            pie_chart(uploaded_file)
                            testimonials(uploaded_file)
                            # pie_chart_community_led(uploaded_file)   #commenting this line as per phase enhancement 1
                            community_led_programs_sum_with_codes(uploaded_file)
                            generate_program_reports(uploaded_file)
                            extract_community_details(uploaded_file)
                            extract_micro_improvements(uploaded_file)
                            upload_images_from_excel(uploaded_file)
                            voices_tab_big_numbers(uploaded_file)
                            generate_outcomes_model_json(uploaded_file)
                            update_voices_json_line_chart(uploaded_file)
                            update_leaders_engaged_dual_axis_chart(uploaded_file)
                            goals(uploaded_file)
                            update_impact_program_frameworks(uploaded_file)
                except Exception as error:
                    st.error(f'Error during full upload: {error}')

            st.markdown("---")
            st.subheader('Sheet previews')
            excel_data = pd.read_excel(BytesIO(file_bytes), sheet_name=None)
            tabs = st.tabs(list(inventory))
            for index, (name, details) in enumerate(inventory.items()):
                with tabs[index]:
                    st.write(f"{details['rows']} source row(s)")
                    st.dataframe(sanitize_dataframe(excel_data[name]), height=320)
                    upload_functions = next((functions for sheet, functions in upload_actions.items()
                                             if normalize_name(sheet) == normalize_name(name)), ())
                    if upload_functions:
                        with st.expander(f'📤 Upload `{name}`', expanded=False):
                            st.caption('Runs in order: ' + ' → '.join(
                                function.__name__
                                for function in upload_functions))
                            if st.button(f'Upload {name}', key=f'upload_sheet_{index}'):
                                with st.status(f'Uploading {name}…', expanded=True) as status, process(file_bytes, inventory, upload_key, status):
                                    for function in upload_functions:
                                        function(uploaded_file)
                    elif name in SUPPORT_SHEETS:
                        st.info('Supporting sheet: read when its dashboard processing step runs.')
                    else:
                        st.warning('This sheet has no configured processor and will be skipped.')
            archive = st.session_state.get('_excel_archive')
            if archive:
                st.caption(f"Source Excel archive: {archive['gcs_path']}")
            elif '_excel_archive' in st.session_state:
                st.warning('Source Excel could not be archived. Dashboard processing results are shown separately below.')
                messages = st.session_state.get('_excel_archive_messages', [])
                if messages:
                    with st.expander('Source Excel archive diagnostics'):
                        st.code('\n'.join(item['message'] for item in messages))
            if '_excel_results' in st.session_state:
                display_report(st.session_state['_excel_results'])
        else:
            st.error('Unsupported file format.')
    except Exception as error:
        st.error(f'Could not read or process this file: {error}')
