import openpyxl
import json
import os
import importlib.util
from constants import PAGE_METADATA, TABS_METADATA

def load_state_codes():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    state_codes_file = os.path.join(script_dir, "..", "pages", "state_code_details.json")
    if not os.path.exists(state_codes_file):
        print("❌ state_code_details.json not found.")
        return None
    with open(state_codes_file, "r", encoding="utf-8") as f:
        return json.load(f)

import os
import json
import openpyxl
import importlib.util

def extract_community_details(excel_file):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        state_codes = load_state_codes()
        if not state_codes:
            return

        workbook = openpyxl.load_workbook(excel_file, data_only=True)

        try:
            sheet = workbook[PAGE_METADATA["COMMUNITY_LED_PROGRAMS"]]
        except KeyError:
            print(f"❌ Sheet not found: {PAGE_METADATA['COMMUNITY_LED_PROGRAMS']}")
            return

        expected_headers = ["Name of the State","Name of the District","No. of community leaders engaged","Community led improvements","Challenges shared","Solutions shared","Infrastructure and resources","School structure and practices","Leadership","Pedagogy","Assessment and Evaluation","Community Engagement","Districts initiated"]
        column_indices = {}
        for cell in sheet[1]:
            if cell.value and str(cell.value).strip() in expected_headers:
                column_indices[str(cell.value).strip()] = cell.column

        missing_columns = [col for col in expected_headers if col not in column_indices]
        if missing_columns:
            print(f"❌ Missing required columns: {missing_columns}")
            return

        map_keys = [
            "No. of community leaders engaged",
            "Community led improvements",
            "Challenges shared",
            "Solutions shared"
        ]

        MAP_DISPLAY_NAMES = {
            "No. of community leaders engaged": "Community Leaders Engaged",
            "Community led improvements": "Community led improvements",
            "Challenges shared": "Challenges shared",
            "Solutions shared": "Solutions shared"
        }

        # These 6 go into community-pie-chart.json
        pie_keys = [
            "Infrastructure and resources",
            "School structure and practices",
            "Leadership",
            "Pedagogy",
            "Assessment and Evaluation",
            "Community Engagement"
        ]

        DISPLAY_NAMES = {
            "Infrastructure and resources": "Infrastructure and Resources",
            "School structure and practices": "School Structure and Practices",
            "Leadership": "Leadership",
            "Pedagogy": "Pedagogy",
            "Assessment and Evaluation": "Assessment and Evaluation",
            "Community Engagement": "Community Engagement"
        }

        state_data = {}

        gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
        spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
        gcp_access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gcp_access)

        for row in sheet.iter_rows(min_row=2, values_only=True):
            state_name = str(row[column_indices["Name of the State"] - 1]).strip()
            district_name = str(row[column_indices["Name of the District"] - 1]).strip()

            if not state_name or not district_name:
                continue

            if state_name not in state_codes:
                continue

            state_id = state_codes[state_name]["id"]
            district_id = state_codes[state_name].get(district_name)
            if not district_id:
                print(f"⚠️ Skipping unknown district {district_name} under state {state_name}")
                continue

            if state_id not in state_data:
                state_data[state_id] = {
                    "state_name": state_name,
                    "districts": {},
                    "overview_totals": {k: 0 for k in map_keys},
                    "pie_totals": {k: 0 for k in pie_keys}
                }

            details = []
            for k in map_keys:
                val = row[column_indices[k] - 1] or 0
                state_data[state_id]["overview_totals"][k] += val
                details.append({"value": val, "code": k})

            state_data[state_id]["districts"][district_id] = {
                "label": district_name,
                "type": "category_1",
                "details": details
            }

            pie_totals = {}
            for k in pie_keys:
                val = row[column_indices[k] - 1] or 0
                state_data[state_id]["pie_totals"][k] += val
                pie_totals[k] = val

            district_folder = os.path.join(script_dir, "..", "districts", district_id)
            os.makedirs(district_folder, exist_ok=True)

            metrics_json = {
                "metrics": [
                    {
                        "label": MAP_DISPLAY_NAMES.get(k, k),
                        "value": row[column_indices[k] - 1] or 0,
                        "identifier": idx
                    }
                    for idx, k in enumerate(map_keys, start=1)
                ]
            }
            metrics_path = os.path.join(district_folder, "community-metrics.json")
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(metrics_json, f, indent=2, ensure_ascii=False)

            pie_json = {
                "data": [
                     {"name": DISPLAY_NAMES.get(k.strip(), k.strip()), "value": pie_totals[k]} 
                     for k in pie_keys
                ]
            }
            pie_path = os.path.join(district_folder, "community-pie-chart.json")
            with open(pie_path, "w", encoding="utf-8") as f:
                json.dump(pie_json, f, indent=2, ensure_ascii=False)

            for fname in ["community-metrics.json", "community-pie-chart.json"]:
                local_path = os.path.join(district_folder, fname)
                blob_path = f"sg-dashboard/districts/{district_id}/{fname}"
                folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                    bucket_name=os.environ.get("BUCKET_NAME"),
                    source_file_path=local_path,
                    destination_blob_name=blob_path
                )
                if folder_url:
                    print(f"✅ Uploaded {fname} for district {district_name} ({district_id}) to {folder_url}")
                else:
                    print(f"❌ Failed to upload {fname} for district {district_name} ({district_id})")

        for state_id, data in state_data.items():
            state_folder = os.path.join(script_dir, "..", "states", state_id)
            os.makedirs(state_folder, exist_ok=True)

            map_json = {
                "result": {
                    "districts": data["districts"],
                    "overview": {
                        "label": data["state_name"],
                        "type": "category_2",
                        "details": [{"value": v, "code": MAP_DISPLAY_NAMES.get(k, k)} for k, v in data["overview_totals"].items()] + [{"value": len(data["districts"]), "code": "Districts activated"}]
                    }
                }
            }

            map_path = os.path.join(state_folder, "community-map.json")
            with open(map_path, "w", encoding="utf-8") as f:
                json.dump(map_json, f, indent=2, ensure_ascii=False)

            # Build community-pie-chart.json
            # pie_json = {
            #     "data": [{"name": k.strip(), "value": v} for k, v in data["pie_totals"].items()]
            # }
            pie_json = {
                "data": [
                    {"name": DISPLAY_NAMES.get(k.strip(), k.strip()), "value": v}
                    for k, v in data["pie_totals"].items()
                ]
            }
            pie_path = os.path.join(state_folder, "community-pie-chart.json")
            with open(pie_path, "w", encoding="utf-8") as f:
                json.dump(pie_json, f, indent=2, ensure_ascii=False)

            for fname in ["community-map.json", "community-pie-chart.json"]:
                local_path = os.path.join(state_folder, fname)
                blob_path = f"sg-dashboard/states/{state_id}/{fname}"
                folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
                    bucket_name=os.environ.get("BUCKET_NAME"),
                    source_file_path=local_path,
                    destination_blob_name=blob_path
                )
                if folder_url:
                    print(f"✅ Uploaded {fname} for state {data['state_name']} to {folder_url}")
                else:
                    print(f"❌ Failed to upload {fname} for state {data['state_name']}")

        state_details_path = os.path.join(script_dir, "..", "pages", "community-details-page.json")
        folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
            bucket_name=os.environ.get("BUCKET_NAME"),
            source_file_path=state_details_path,
            destination_blob_name="sg-dashboard/community-details-page.json"
        )

        if folder_url:
            print(f"✅ Uploaded community-details-page.json to {folder_url}")
        else:
            print("❌ Failed to upload community-details-page.json")

    except Exception as e:
        print(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python extract_community_details.py <excel_file>")
    else:
        extract_community_details(sys.argv[1])
