import re
import importlib.util
import json
import os

import openpyxl

from constants import (
    ATRISK_CHILDREN_REGULARISED,
    CHILDREN_ENROLLED,
    CHILDREN_GOT_AADHAAR,
    CHILDREN_GOT_BIRTH_CERTIFICATE,
    COMMUNITY_COUNTRY_VIEW_CODE_ORDER,
    COMMUNITY_DISTRICT_COLUMN,
    COMMUNITY_MAP_COLUMNS,
    COMMUNITY_PIE_COLUMNS,
    COMMUNITY_PIE_DISPLAY_NAMES,
    COMMUNITY_STATE_COLUMN,
    DISTRICTS_ACTIVATED,
    DOCUMENTATION_COLUMNS,
    ENROLLMENT_COLUMNS,
    TOTAL_ROW_LABEL,
)


def normalize(text):
    if text is None:
        return ""
    text = str(text)

    # remove extra spaces + hidden whitespace
    text = re.sub(r'\s+', ' ', text)

    # trim + lowercase
    return text.strip().lower()


def safe_int(value):
    try:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)

        value = str(value).strip()

        if value.lower() in ["", "total", "-", "na", "n/a"]:
            return 0

        return int(float(value))
    except:
        return 0


def load_enrollment_data(workbook):
    enrollment_data = {}
    enrollment_by_state = {}

    try:
        sheet = workbook["Enrollments"]
    except KeyError:
        return enrollment_data, enrollment_by_state

    # Start from row 4 (skip title row 1, main headers row 2, detailed headers row 3)
    for row in sheet.iter_rows(min_row=4, values_only=True):
        state = normalize(row[ENROLLMENT_COLUMNS["state"]])
        district = normalize(row[ENROLLMENT_COLUMNS["district"]])

        # Skip empty rows or total rows (where district name might be empty)
        if not state or not district or district == TOTAL_ROW_LABEL:
            continue

        key = (state, district)
        enrolled = safe_int(row[ENROLLMENT_COLUMNS["children_total"]])
        atrisk = safe_int(row[ENROLLMENT_COLUMNS["atrisk_total"]])

        enrollment_data[key] = {
            CHILDREN_ENROLLED: enrolled,
            ATRISK_CHILDREN_REGULARISED: atrisk
        }

        state_totals = enrollment_by_state.setdefault(state, {
            CHILDREN_ENROLLED: 0,
            ATRISK_CHILDREN_REGULARISED: 0
        })
        state_totals[CHILDREN_ENROLLED] += enrolled
        state_totals[ATRISK_CHILDREN_REGULARISED] += atrisk

    return enrollment_data, enrollment_by_state


def load_documentation_data(workbook):
    documentation_data = {}
    documentation_by_state = {}

    try:
        sheet = workbook["Documentation"]
    except KeyError:
        return documentation_data, documentation_by_state

    for row in sheet.iter_rows(min_row=2, values_only=True):
        state_value = row[DOCUMENTATION_COLUMNS["state"]]
        district_value = row[DOCUMENTATION_COLUMNS["district"]]
        state = str(state_value).strip() if state_value else None
        district = str(district_value).strip() if district_value else None

        if not state or not district:
            continue

        if district.lower() == TOTAL_ROW_LABEL:
            continue

        key = (normalize(state), normalize(district))
        aadhaar = safe_int(row[DOCUMENTATION_COLUMNS["aadhaar"]])
        birth = safe_int(row[DOCUMENTATION_COLUMNS["birth_certificate"]])

        documentation_data[key] = {
            CHILDREN_GOT_AADHAAR: aadhaar,
            CHILDREN_GOT_BIRTH_CERTIFICATE: birth
        }

        state_totals = documentation_by_state.setdefault(normalize(state), {
            CHILDREN_GOT_AADHAAR: 0,
            CHILDREN_GOT_BIRTH_CERTIFICATE: 0
        })
        state_totals[CHILDREN_GOT_AADHAAR] += aadhaar
        state_totals[CHILDREN_GOT_BIRTH_CERTIFICATE] += birth

    return documentation_data, documentation_by_state


def pie_chart_community_led(excel_file):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        print(script_dir)

        json_path = os.path.join(
            script_dir, "..", "pages", "community-led-improvements-page.json"
        )

        workbook = openpyxl.load_workbook(excel_file, data_only=True)

        try:
            sheet = workbook["Community Led Programs"]
        except KeyError:
            print("Error: Sheet 'Community Led Programs' not found in the Excel file.")
            print(f"Available sheets: {workbook.sheetnames}")
            return

        headers = [cell.value for cell in sheet[1]]
        cleaned_headers = [
            str(cell).strip() if cell is not None else '' for cell in headers
        ]

        expected_columns = COMMUNITY_PIE_COLUMNS
        display_names = COMMUNITY_PIE_DISPLAY_NAMES

        if not all(col in cleaned_headers for col in expected_columns):
            print(f"Error: Excel file must contain columns: {expected_columns}")
            print(f"Found: {cleaned_headers}")
            return

        data = []

        for col_name in expected_columns:
            col_index = cleaned_headers.index(col_name)
            col_sum = 0

            for row in sheet.iter_rows(
                min_row=2, max_col=len(headers), values_only=True
            ):
                try:
                    value = row[col_index]
                    if isinstance(value, (int, float)) and value is not None:
                        col_sum += value
                except Exception as e:
                    print(f"Error processing value in column {col_name}: {str(e)}")
                    continue

            if isinstance(col_sum, float) and col_sum.is_integer():
                col_sum = int(col_sum)

            data.append({
                'name': display_names.get(col_name.strip(), col_name.strip()),
                'value': col_sum
            })

        with open(json_path, 'r', encoding='utf-8') as json_file:
            raw_content = json_file.read()
            json_file.seek(0)

            try:
                json_data = json.load(json_file)
            except json.JSONDecodeError:
                try:
                    json_data = json.loads(raw_content)
                except json.JSONDecodeError:
                    json_data = []

        if not isinstance(json_data, list):
            json_data = [json_data] if json_data else []

        found = False

        for obj in json_data:
            if isinstance(obj, dict) and obj.get('type', '').strip().lower() == 'pie-chart':
                print(data)
                obj['data'] = data
                found = True
                break

        if not found:
            json_data.append({
                'type': 'pie-chart-community-led',
                'data': data
            })

        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(json_data, json_file, indent=2, ensure_ascii=False)

        gcp_access_path = os.path.join(
            script_dir, '..', 'cloud-scripts', 'gcp_access.py'
        )

        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)

        folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
            bucket_name=os.environ.get("BUCKET_NAME"),
            source_file_path=json_path,
            destination_blob_name="sg-dashboard/community-led-improvements-page.json"
        )

        if folder_url:
            print(f"Successfully uploaded and got public folder URL: {folder_url}")
        else:
            print("Failed to upload file to GCS. Check logs for details.")

    except Exception as e:
        print(f"Error: {str(e)}")

def community_led_programs_sum_with_codes(excel_file):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        print(script_dir)

        json_path = os.path.join(
            script_dir, "..", "pages", "community-country-view.json"
        )

        workbook = openpyxl.load_workbook(excel_file, data_only=True)

        enrollment_data, enrollment_by_state = load_enrollment_data(workbook)
        documentation_data, documentation_by_state = load_documentation_data(workbook)

        try:
            community_sheet = workbook["New_Community led Programs "]
        except KeyError:
            print("Error: Sheet 'new Community Led Programs' not found in the Excel file.")
            print(f"Available sheets: {workbook.sheetnames}")
            return

        try:
            state_district_sheet = workbook["State_district details"]
        except KeyError:
            print("Error: Sheet 'State_district details' not found in the Excel file.")
            print(f"Available sheets: {workbook.sheetnames}")
            return

        community_headers = [cell.value for cell in community_sheet[1]]
        community_cleaned_headers = [
            str(cell).strip() if cell is not None else '' for cell in community_headers
        ]

        expected_community_columns = [
            COMMUNITY_STATE_COLUMN,
            COMMUNITY_DISTRICT_COLUMN,
            *COMMUNITY_MAP_COLUMNS,
        ]

        if not all(col in community_cleaned_headers for col in expected_community_columns):
            print("Error: Excel file must contain columns in Community Led Programs")
            return

        state_district_headers = [cell.value for cell in state_district_sheet[1]]
        state_district_cleaned_headers = [
            str(cell).strip() if cell is not None else '' for cell in state_district_headers
        ]

        state_codes = {}

        for row in state_district_sheet.iter_rows(min_row=2, values_only=True):
            try:
                state_name = row[state_district_cleaned_headers.index("state name")] or ''
                state_code = row[state_district_cleaned_headers.index("state code")]

                if state_name and state_code:
                    state_codes[state_name] = str(state_code)
            except Exception:
                continue

        state_sums = {}

        for row in community_sheet.iter_rows(min_row=2, values_only=True):
            try:
                state_name = row[community_cleaned_headers.index(COMMUNITY_STATE_COLUMN)] or ''
                district_name = row[community_cleaned_headers.index(COMMUNITY_DISTRICT_COLUMN)] or ''

                if not state_name or not district_name:
                    continue

                if state_name not in state_sums:
                    state_sums[state_name] = {
                        **{column: 0 for column in COMMUNITY_MAP_COLUMNS},
                        DISTRICTS_ACTIVATED: set(),
                        CHILDREN_ENROLLED: 0,
                        ATRISK_CHILDREN_REGULARISED: 0,
                        CHILDREN_GOT_AADHAAR: 0,
                        CHILDREN_GOT_BIRTH_CERTIFICATE: 0,
                    }

                state_sums[state_name][DISTRICTS_ACTIVATED].add(district_name)

                # if (state_name, district_name) in enrollment_data:
                #     enroll_info = enrollment_data[(state_name, district_name)]
                #     state_sums[state_name]["Children enrolled"] += enroll_info["Children enrolled"]
                #     state_sums[state_name]["At-risk of dropout children regularised in school"] += enroll_info["At-risk of dropout children regularised in school"]

                # if (state_name, district_name) in documentation_data:
                #     doc_info = documentation_data[(state_name, district_name)]
                #     state_sums[state_name]["Children who got Aadhaar"] += doc_info["Children who got Aadhaar"]
                #     state_sums[state_name]["Children who got Birth Certificate"] += doc_info["Children who got Birth Certificate"]

                # We keep community sheet details by row, but enrollment/documentation totals should use full state totals.
                for col_name in expected_community_columns[2:]:
                    col_index = community_cleaned_headers.index(col_name)
                    value = row[col_index]

                    if isinstance(value, (int, float)) and value is not None:
                        state_sums[state_name][col_name] += value

            except Exception:
                continue

        # Apply full state totals for enrollment and documentation data.
        for state_name, sums in state_sums.items():
            state_key = normalize(state_name)
            state_enroll = enrollment_by_state.get(state_key, {})
            state_doc = documentation_by_state.get(state_key, {})

            sums[CHILDREN_ENROLLED] = state_enroll.get(CHILDREN_ENROLLED, 0)
            sums[ATRISK_CHILDREN_REGULARISED] = state_enroll.get(
                ATRISK_CHILDREN_REGULARISED, 0
            )
            sums[CHILDREN_GOT_AADHAAR] = state_doc.get(CHILDREN_GOT_AADHAAR, 0)
            sums[CHILDREN_GOT_BIRTH_CERTIFICATE] = state_doc.get(
                CHILDREN_GOT_BIRTH_CERTIFICATE,
                0
            )

        unmapped_states = sorted(state for state in state_sums if state not in state_codes)
        if unmapped_states:
            print(f"Error: Missing state codes for: {', '.join(unmapped_states)}")
            return

        states_data = {
            state_codes[state]: {
                "id": state_codes[state],
                "label": state,
                "type": "category_1",
                "details": []
            }
            for state, sums in state_sums.items()
        }

        for state, sums in state_sums.items():
            state_id = state_codes[state]
            details = []
            for code in COMMUNITY_COUNTRY_VIEW_CODE_ORDER:
                if code == DISTRICTS_ACTIVATED:
                    value = len(sums[DISTRICTS_ACTIVATED])
                else:
                    value = sums.get(code, 0)
                
                if value == 0:
                    continue
                
                details.append({
                    "code": code,
                    "value": int(value) if isinstance(value, float) and value.is_integer() else value
                })
            
            states_data[state_id]["details"] = details

        data = {"result": {"states": states_data}}

        try:
            with open(json_path, 'r', encoding='utf-8') as json_file:
                raw_content = json_file.read()
                json_file.seek(0)

                try:
                    json_data = json.load(json_file)
                except json.JSONDecodeError:
                    json_data = json.loads(raw_content)

        except:
            json_data = {}

        if "result" not in json_data:
            json_data["result"] = {}

        if "states" not in json_data["result"]:
            json_data["result"]["states"] = {}

        json_data["result"]["states"].update(states_data)

        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(json_data, json_file, indent=2)

        print(states_data)
        # Dynamically import gcp_access module and upload file
        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)

        folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
            bucket_name=os.environ.get("BUCKET_NAME"),
            source_file_path=json_path,
            destination_blob_name="sg-dashboard/community-country-view.json"
        )

        if folder_url:
            print(f"Successfully uploaded and got public folder URL: {folder_url}")
            updateOverviewValues()
        else:
            print("Failed to upload file to GCS. Check logs for details.")

    except Exception as e:
        print(f"Error: {str(e)}")


def updateOverviewValues():
    # Get the directory of the script
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Construct the JSON file path
    json_path = os.path.join(script_dir, "..", "pages", "community-country-view.json")

    # Read JSON data from file with detailed error handling
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
            if not file_content.strip():
                print(f"Error: The file {json_path} is empty.")
                exit(1)
            data = json.loads(file_content)
    except FileNotFoundError:
        print(f"Error: File not found at {json_path}")
        exit(1)
    except UnicodeDecodeError as e:
        print(f"Error: Encoding issue in {json_path}. Ensure the file is UTF-8 encoded.")
        print(f"Details: {str(e)}")
        exit(1)
    except JSONDecodeError as e:
        print(f"Error: Invalid JSON format in {json_path}")
        print(f"Details: {str(e)}")
        print(f"Line: {e.lineno}, Column: {e.colno}")
        print(f"Content near error: {e.doc[max(0, e.pos-20):e.pos+20]}")
        exit(1)
    except Exception as e:
        print(f"Unexpected error while reading {json_path}: {str(e)}")
        exit(1)

    # Initialize a dictionary to store the sums for each code dynamically
    code_sums = {}

    # Navigate each state and sum the values for each code in details
    try:
        for state_id, state_data in data['result']['states'].items():
            for detail in state_data['details']:
                code = detail['code']
                value = detail['value']
                # Initialize the code in code_sums if not present
                if code not in code_sums:
                    code_sums[code] = 0
                code_sums[code] += value
    except KeyError as e:
        print(f"Error: Missing expected key in JSON structure: {str(e)}")
        exit(1)

    # Ensure all codes from code_sums exist in overview details
    try:
        overview_details = data['result']['overview']['details']
        existing_codes = {detail['code'] for detail in overview_details}
        
        # Add missing codes to overview details
        for code in code_sums:
            if code not in existing_codes:
                print(f"Adding missing code '{code}' to overview details")
                overview_details.append({"code": code, "value": 0})
        
        # Update the overview details with the summed values
        for detail in overview_details:
            code = detail['code']
            if code in code_sums:
                detail['value'] = code_sums[code]
            else:
                print(f"Warning: Code '{code}' in overview not found in states")

        # Sort overview details to match the order
        overview_details.sort(
            key=lambda d: (
                COMMUNITY_COUNTRY_VIEW_CODE_ORDER.index(d['code'])
                if d['code'] in COMMUNITY_COUNTRY_VIEW_CODE_ORDER
                else len(COMMUNITY_COUNTRY_VIEW_CODE_ORDER)
            )
        )

    except KeyError as e:
        print(f"Error: Missing expected key in overview structure: {str(e)}")
        exit(1)

    # Convert the updated data to a JSON string with indentation
    updated_json = json.dumps(data, indent=2)

    # Print the updated JSON
    print(updated_json)

    # Save the updated JSON to a new file in the same directory
    output_path = os.path.join(script_dir, "..", "pages", "community-country-view.json")
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)
        folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
            bucket_name=os.environ.get("BUCKET_NAME"),
            source_file_path=json_path,
            destination_blob_name="sg-dashboard/community-country-view.json"
        )
        print(f"Updated JSON saved to {output_path}")
    except Exception as e:
        print(f"Error: Failed to write to {output_path}: {str(e)}")
        exit(1)



                
