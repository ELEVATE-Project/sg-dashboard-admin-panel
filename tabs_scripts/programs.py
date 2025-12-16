import openpyxl
import json
import os
import re
import io
from difflib import get_close_matches
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from constants import PAGE_METADATA, TABS_METADATA
import importlib.util
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

service_account_info = {
    "type": os.getenv("TYPE"),
    "project_id": os.getenv("PROJECT_ID"),
    "private_key_id": os.getenv("PRIVATE_KEY_ID"),
    "private_key": os.getenv("PRIVATE_KEY").replace('\\n', '\n'),
    "client_email": os.getenv("CLIENT_EMAIL"),
    "auth_uri": os.getenv("AUTH_URI"),
    "token_uri": os.getenv("TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("AUTH_PROVIDER_X509_CERT_URL"),
    "client_x509_cert_url": os.getenv("CLIENT_X509_CERT_URL"),
    "universe_domain": os.getenv("UNIVERSE_DOMAIN"),
}

credentials = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=credentials)


def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).strip().lower())


def snake_case(text):
    """Convert string to snake_case for JSON keys."""
    text = re.sub(r'\s+', '_', text.strip())
    text = re.sub(r'[^a-zA-Z0-9_]', '', text)
    return text.lower()


def extract_folder_id(drive_url):
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', drive_url) or re.search(r'id=([a-zA-Z0-9_-]+)', drive_url)
    return match.group(1) if match else None


def download_file(file_id, output_dir):
    try:
        file_metadata = drive_service.files().get(fileId=file_id).execute()
        filename = file_metadata.get('name')
        request = drive_service.files().get_media(fileId=file_id)
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, filename)
        with io.FileIO(file_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return file_path
    except Exception as e:
        print(f"❌ Failed to download file {file_id}: {e}")
        return None


def download_folder_images(folder_id, output_dir, program_type):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
    spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
    gcp_access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcp_access)

    bucket_name = os.environ.get("BUCKET_NAME")
    logo_urls = []
    page_token = None

    while True:
        response = drive_service.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image/'",
            spaces='drive',
            fields='nextPageToken, files(id, name)',
            pageToken=page_token
        ).execute()

        for file in response.get('files', []):
            local_file = download_file(file['id'], output_dir)
            if local_file:
                local_filename = os.path.basename(local_file)
                destination_blob = f"sg-dashboard/partners/{program_type}/{local_filename}"
                folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                    bucket_name=bucket_name,
                    source_file_path=local_file,
                    destination_blob_name=destination_blob
                )
                if folder_url:
                    os.remove(local_file)
                    final_url = f"{folder_url.rstrip('/')}/{local_filename}"
                    logo_urls.append(final_url)
                    print(f"✅ Uploaded {local_filename} → {final_url}")
                else:
                    print(f"❌ Failed to upload {local_filename} to GCS")

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    return logo_urls


def build_lookup(state_code_map):
    """Build lookup for district codes and state codes"""
    lookup = {}
    district_index = {}
    for state_name, state_info in state_code_map.items():
        state_code = str(state_info.get("id", "")).strip()
        for key, value in state_info.items():
            if key == "id":
                continue
            norm_key = (normalize(state_name), normalize(key))
            district_code = str(value).strip()
            lookup[norm_key] = (state_code, district_code)
            district_index.setdefault(normalize(state_name), {})[normalize(key)] = (state_code, district_code)
    return lookup, district_index


def resolve_codes(state, district, lookup, district_index):
    """Return (state_code, district_code) tuple"""
    norm_key = (normalize(state), normalize(district))
    if norm_key in lookup:
        return lookup[norm_key]

    state_key = normalize(state)
    if state_key in district_index:
        possible_districts = list(district_index[state_key].keys())
        close = get_close_matches(normalize(district), possible_districts, n=1, cutoff=0.8)
        if close:
            print(f"ℹ️ Using fuzzy match: {district} → {close[0]}")
            return district_index[state_key][close[0]]

    return None, None


def generate_program_reports(excel_file):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_images_dir = os.path.join(script_dir, 'tmp_images')
        os.makedirs(base_images_dir, exist_ok=True)

        # Load state code mapping
        state_code_path = os.path.join(script_dir, '..', "pages", 'state_code_details.json')
        with open(state_code_path, 'r', encoding='utf-8') as f:
            state_code_map = json.load(f)

        district_lookup, district_index = build_lookup(state_code_map)

        workbook = openpyxl.load_workbook(excel_file, data_only=True)
        sheet = workbook[PAGE_METADATA["PROGRAMS"]]

        headers = [str(cell.value).strip() if cell.value else '' for cell in sheet[1]]
        header_index_map = {str(col).strip(): i for i, col in enumerate(headers) if col}
        print("✅ Headers from Excel:", headers)

        district_data = {'SLC': {}, 'WLC': {}}
        state_data = {}
        state_wlc_data = {} 

        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)
        bucket_name = os.environ.get("BUCKET_NAME")

        for row in sheet.iter_rows(min_row=2, values_only=True):
            row_dict = {snake_case(col): row[header_index_map.get(col)] if header_index_map.get(col) is not None else '' 
                        for col in TABS_METADATA["PROGRAMS"]}

            state = str(row_dict.get('state_name', '')).strip()
            district = str(row_dict.get('district_name', '')).strip()
            program = str(row_dict.get('name_of_the_program', '')).strip()
            program_type = str(row_dict.get('program_type', '')).strip().upper()

            if not (state and program):
                continue

            is_state_level = not district or district.lower() in ['none', state.lower()]
            state_code, district_code = resolve_codes(state, district if not is_state_level else state, district_lookup, district_index)

            # Use state id from JSON if available
            state_code = state_code_map.get(state, {}).get("id", state_code or normalize(state))

            folder_url = row_dict.get('pictures_from_the_program', '')
            logo_urls = []
            if folder_url:
                folder_id = extract_folder_id(folder_url)
                if folder_id:
                    program_folder = os.path.join(base_images_dir, f"{program.replace(' ', '_').lower()}")
                    os.makedirs(program_folder, exist_ok=True)
                    logo_urls = download_folder_images(folder_id, program_folder, program_type)
            row_dict['logo_urls'] = logo_urls

            # Convert partner field to array
            partner_key = 'name_of_the_partner_leading_the_program'
            if partner_key in row_dict:
                partner_value = row_dict[partner_key]
                if partner_value:
                    if isinstance(partner_value, str):
                        row_dict[partner_key] = [x.strip() for x in partner_value.split(',')]
                    elif isinstance(partner_value, list):
                        row_dict[partner_key] = partner_value
                else:
                    row_dict[partner_key] = []

            # Add to state-level or district-level JSON
            if is_state_level or not district_code:
                state_data.setdefault(str(state_code), []).append(row_dict)
            else:
                district_data[program_type].setdefault(str(district_code), []).append(row_dict)
                if program_type == "WLC":
                    state_wlc_data.setdefault(str(state_code), []).append(row_dict)  # <-- Collect WLC per state

        # District-level JSONs
        districts_dir = os.path.join(script_dir, '..', 'districts')
        os.makedirs(districts_dir, exist_ok=True)

        for category_name, data_dict in district_data.items():
            for district_code, programs in data_dict.items():
                district_folder = os.path.join(districts_dir, str(district_code))
                os.makedirs(district_folder, exist_ok=True)
                out_file = os.path.join(district_folder, f"{category_name}.json")
                with open(out_file, 'w', encoding='utf-8') as f:
                    json.dump(programs, f, indent=2, ensure_ascii=False)
                print(f"✅ Saved {category_name}.json for district {district_code} at {out_file}")

                gcs_path = f"sg-dashboard/districts/{district_code}/{category_name}.json"
                folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                    bucket_name=bucket_name,
                    source_file_path=out_file,
                    destination_blob_name=gcs_path
                )
                if folder_url:
                    print(f"✅ Uploaded {category_name}.json for district {district_code} to {folder_url}")
                else:
                    print(f"❌ Failed to upload {category_name}.json for district {district_code}")

        # State-level JSONs
        states_dir = os.path.join(script_dir, '..', 'states')
        os.makedirs(states_dir, exist_ok=True)

        for state_code, programs in state_data.items():
            state_folder = os.path.join(states_dir, str(state_code))
            os.makedirs(state_folder, exist_ok=True)
            out_file = os.path.join(state_folder, "state-program.json")
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(programs, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved state-program.json for state {state_code} at {out_file}")

            gcs_path = f"sg-dashboard/states/{state_code}/state-program.json"
            folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                bucket_name=bucket_name,
                source_file_path=out_file,
                destination_blob_name=gcs_path
            )
            if folder_url:
                print(f"✅ Uploaded state-program.json for state {state_code} to {folder_url}")
            else:
                print(f"❌ Failed to upload state-program.json for state {state_code}")

        # NEW: State-level WLC.json
        for state_code, wlc_programs in state_wlc_data.items():
            state_folder = os.path.join(states_dir, str(state_code))
            os.makedirs(state_folder, exist_ok=True)
            out_file = os.path.join(state_folder, "WLC.json")
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(wlc_programs, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved WLC.json for state {state_code} at {out_file}")

            gcs_path = f"sg-dashboard/states/{state_code}/WLC.json"
            folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                bucket_name=bucket_name,
                source_file_path=out_file,
                destination_blob_name=gcs_path
            )
            if folder_url:
                print(f"✅ Uploaded WLC.json for state {state_code} to {folder_url}")
            else:
                print(f"❌ Failed to upload WLC.json for state {state_code}")

        print("✅ Program reports generated successfully.")

    except Exception as e:
        print(f"❌ Fatal Error: {e}")


if __name__ == "__main__":
    excel_file = "programs.xlsx"
    generate_program_reports(excel_file)
