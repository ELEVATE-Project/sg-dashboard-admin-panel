from altair.datasets import data
import openpyxl
import json
import os
import importlib.util
from constants import PAGE_METADATA, TABS_METADATA

from google.cloud import storage


def delete_district_community_pie_charts():
    bucket_name = os.environ.get("BUCKET_NAME")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    state_codes = load_state_codes()
    if not bucket_name:
        print("❌ BUCKET_NAME not set")
        return

    gcp_access_path = os.path.join(script_dir, '..', 'cloud-scripts', 'gcp_access.py')
    spec = importlib.util.spec_from_file_location('gcp_access', gcp_access_path)
    gcp_access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcp_access)

    gcp_access.delete_all_district_community_pie_charts(
        bucket_name=os.environ.get("BUCKET_NAME")
    )


if __name__ == "__main__":
    delete_district_community_pie_charts()


def load_state_codes():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    state_codes_file = os.path.join(script_dir, "..", "pages", "state_code_details.json")
    if not os.path.exists(state_codes_file):
        print("❌ state_code_details.json not found.")
        return None
    with open(state_codes_file, "r", encoding="utf-8") as f:
        return json.load(f)


# ✅ SAFE INT (fix for "Total")
def safe_int(val):
    try:
        return int(val)
    except:
        return 0


def load_enrollment_data(workbook):
    enrollment_data = {}
    enrollment_totals_by_state = {}
    enrollment_sum_by_state = {}

    try:
        sheet = workbook["Enrollments"]
    except KeyError:
        print("❌ 'Enrollments' sheet not found")
        return enrollment_data, enrollment_totals_by_state

    for row in sheet.iter_rows(min_row=3, values_only=True):
        state = str(row[1]).strip() if row[1] else None
        district = str(row[2]).strip() if row[2] else None

        if not state or not district:
            continue

        total_enrolled = safe_int(row[6])
        total_regularized = safe_int(row[10])

        if district.lower() == "total":
            enrollment_totals_by_state[state] = {
                "Children enrolled": total_enrolled,
                "At-risk of dropout children regularised in school": total_regularized
            }
            continue

        enrollment_data[(state, district)] = {
            "Children enrolled": total_enrolled,
            "At-risk of dropout children regularised in school": total_regularized
        }

        state_sum = enrollment_sum_by_state.setdefault(state, {
            "Children enrolled": 0,
            "At-risk of dropout children regularised in school": 0
        })
        state_sum["Children enrolled"] += total_enrolled
        state_sum["At-risk of dropout children regularised in school"] += total_regularized

    for state, totals in enrollment_sum_by_state.items():
        if state not in enrollment_totals_by_state:
            enrollment_totals_by_state[state] = totals

    return enrollment_data, enrollment_totals_by_state


# ✅ UPDATED (capture TOTAL row)
def load_documentation_data(workbook):
    documentation_data = {}
    documentation_totals_by_state = {}
    documentation_totals_global = {
        "Children who got Aadhaar": 0,
        "Children who got Birth Certificate": 0
    }

    try:
        sheet = workbook["Documentation"]
    except KeyError:
        print("❌ 'Documentation' sheet not found")
        return documentation_data, documentation_totals_by_state, documentation_totals_global

    for row in sheet.iter_rows(min_row=2, values_only=True):
        state = str(row[0]).strip() if row[0] else None
        district = str(row[1]).strip() if row[1] else None

        aadhaar = safe_int(row[2])
        birth = safe_int(row[3])

        if state and district and district.lower() == "total":
            state_totals = documentation_totals_by_state.setdefault(state, {
                "Children who got Aadhaar": 0,
                "Children who got Birth Certificate": 0
            })
            state_totals["Children who got Aadhaar"] = aadhaar
            state_totals["Children who got Birth Certificate"] = birth
            documentation_totals_global["Children who got Aadhaar"] += aadhaar
            documentation_totals_global["Children who got Birth Certificate"] += birth
            continue

        if not state or not district:
            continue

        documentation_data[(state, district)] = {
            "Children who got Aadhaar": aadhaar,
            "Children who got Birth Certificate": birth
        }

        state_totals = documentation_totals_by_state.setdefault(state, {
            "Children who got Aadhaar": 0,
            "Children who got Birth Certificate": 0
        })
        state_totals["Children who got Aadhaar"] += aadhaar
        state_totals["Children who got Birth Certificate"] += birth

        documentation_totals_global["Children who got Aadhaar"] += aadhaar
        documentation_totals_global["Children who got Birth Certificate"] += birth

    return documentation_data, documentation_totals_by_state, documentation_totals_global


def extract_community_details(excel_file):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        state_codes = load_state_codes()
        if not state_codes:
            return

        workbook = openpyxl.load_workbook(excel_file, data_only=True)

        enrollment_data, enrollment_totals_by_state = load_enrollment_data(workbook)
        documentation_data, documentation_totals_by_state, documentation_totals = load_documentation_data(workbook)

        try:
            sheet = workbook[PAGE_METADATA["NEW_COMMUNITY_LED_PROGRAMS"]]
        except KeyError:
            print(f"❌ Sheet not found: {PAGE_METADATA['NEW_COMMUNITY_LED_PROGRAMS']}")
            return

        expected_headers = ["Name of the State","Name of the District","Community members participating in dialogues","Local challenges identified","Community leaders driving improvements","Local solutions identified","Local Solutions implemented","Community Engagement","Infrastructure and resources","School structure and practices","Leadership","Pedagogy","Assessment and Evaluation","Districts initiated"]

        column_indices = {}
        for cell in sheet[1]:
            if cell.value and str(cell.value).strip() in expected_headers:
                column_indices[str(cell.value).strip()] = cell.column

        missing_columns = [col for col in expected_headers if col not in column_indices]
        if missing_columns:
            print(f"❌ Missing required columns: {missing_columns}")
            return

        map_keys = [
            "Community members participating in dialogues",
            "Local challenges identified",
            "Community leaders driving improvements",
            "Local solutions identified",
            "Local Solutions implemented"
        ]

        MAP_DISPLAY_NAMES = {
            "Community members participating in dialogues": "Community members participating in dialogues",
            "Local challenges identified": "Local challenges identified",
            "Community leaders driving improvements": "Community leaders driving improvements",
            "Local solutions identified": "Local solutions identified",
            "Local Solutions implemented":"Local Solutions implemented"
        }

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

            if not state_name or not district_name or district_name.lower() == "total":
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
                    "enrollment_totals": {
                        "Children enrolled": 0,
                        "At-risk of dropout children regularised in school": 0
                    },
                    "documentation_totals": {
                        "Children who got Aadhaar": 0,
                        "Children who got Birth Certificate": 0
                    },
                    "pie_totals": {k: 0 for k in pie_keys}
                }

            details = []

            for k in map_keys:
                val = safe_int(row[column_indices[k] - 1])
                state_data[state_id]["overview_totals"][k] += val
                details.append({"value": val, "code": k})

            enroll_info = enrollment_data.get((state_name, district_name), {
                "Children enrolled": 0,
                "At-risk of dropout children regularised in school": 0
            })

            doc_info = documentation_data.get((state_name, district_name), {
                "Children who got Aadhaar": 0,
                "Children who got Birth Certificate": 0
            })

            if enroll_info["Children enrolled"] != 0:
                details.append({"value": enroll_info["Children enrolled"], "code": "Children enrolled"})
            if enroll_info["At-risk of dropout children regularised in school"] != 0:
                details.append({"value": enroll_info["At-risk of dropout children regularised in school"], "code": "At-risk of dropout children regularised in school"})
            if doc_info["Children who got Aadhaar"] != 0:
                details.append({"value": doc_info["Children who got Aadhaar"], "code": "Children who got Aadhaar"})
            if doc_info["Children who got Birth Certificate"] != 0:
                details.append({"value": doc_info["Children who got Birth Certificate"], "code": "Children who got Birth Certificate"})

            state_data[state_id]["enrollment_totals"]["Children enrolled"] += enroll_info["Children enrolled"]
            state_data[state_id]["enrollment_totals"]["At-risk of dropout children regularised in school"] += enroll_info["At-risk of dropout children regularised in school"]

            state_data[state_id]["documentation_totals"]["Children who got Aadhaar"] += doc_info["Children who got Aadhaar"]
            state_data[state_id]["documentation_totals"]["Children who got Birth Certificate"] += doc_info["Children who got Birth Certificate"]

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

            metrics = [
                {
                    "label": MAP_DISPLAY_NAMES.get(k, k),
                    "value": safe_int(row[column_indices[k] - 1]),
                    "identifier": idx
                }
                for idx, k in enumerate(map_keys, start=1)
            ]

            for label, value, identifier in [
                ("Children enrolled", enroll_info["Children enrolled"], 6),
                ("At-risk of dropout children regularised in school", enroll_info["At-risk of dropout children regularised in school"], 7),
                ("Children who got Aadhaar", doc_info["Children who got Aadhaar"], 8),
                ("Children who got Birth Certificate", doc_info["Children who got Birth Certificate"], 9)
            ]:
                if value != 0:
                    metrics.append({"label": label, "value": value, "identifier": identifier})

            metrics_json = {"metrics": metrics}

            metrics_path = os.path.join(district_folder, "community-metrics.json")
            with open(metrics_path, "w", encoding="utf-8") as f:
                json.dump(metrics_json, f, indent=2, ensure_ascii=False)

            # pie_json = {
            #     "data": [
            #          {"name": DISPLAY_NAMES.get(k.strip(), k.strip()), "value": pie_totals[k]} 
            #          for k in pie_keys
            #     ]
            # }
            # pie_path = os.path.join(district_folder, "community-pie-chart.json")
            # with open(pie_path, "w", encoding="utf-8") as f:
            #     json.dump(pie_json, f, indent=2, ensure_ascii=False)

            for fname in ["community-metrics.json"]:
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

            state_doc_totals = documentation_totals_by_state.get(data["state_name"], {
                "Children who got Aadhaar": 0,
                "Children who got Birth Certificate": 0
            })

            state_enroll_totals = enrollment_totals_by_state.get(data["state_name"], data["enrollment_totals"])

            # map_json = {
            #     "result": {
            #         "districts": data["districts"],
            #         "overview": {
            #             "label": data["state_name"],
            #             "type": "category_2",
            #             "details":
            #                 [{"value": v, "code": MAP_DISPLAY_NAMES.get(k, k)} for k, v in data["overview_totals"].items()]
            #                 +
            #                 [
            #                     {"value": state_enroll_totals["Children enrolled"], "code": "Children enrolled"},
            #                     {"value": state_enroll_totals["At-risk of dropout children regularised in school"], "code": "At-risk of dropout children regularised in school"},
            #                     {"value": state_doc_totals["Children who got Aadhaar"], "code": "Children who got Aadhaar"},
            #                     {"value": state_doc_totals["Children who got Birth Certificate"], "code": "Children who got Birth Certificate"},
            #                     {"value": len(data["districts"]), "code": "Districts activated"}
            #                 ]
            #         }
            #     }
            # }

            # build filtered overview details
            details = []

            # map_keys totals
            for k, v in data["overview_totals"].items():
                if v != 0:
                    details.append({
                        "value": v,
                        "code": MAP_DISPLAY_NAMES.get(k, k)
                   })

            # enrollment + documentation
            extra_fields = [
                ("Children enrolled", state_enroll_totals["Children enrolled"]),
                ("At-risk of dropout children regularised in school", state_enroll_totals["At-risk of dropout children regularised in school"]),
                ("Children who got Aadhaar", state_doc_totals["Children who got Aadhaar"]),
                ("Children who got Birth Certificate", state_doc_totals["Children who got Birth Certificate"]),
                ("Districts activated", len(data["districts"]))
            ]

            for label, value in extra_fields:
                if value != 0:
                    details.append({
                        "value": value,
                        "code": label
                    })
 
            map_json = {
                "result": {
                    "districts": data["districts"],
                    "overview": {
                         "label": data["state_name"],
                         "type": "category_2",
                         "details": details
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
            # pie_json = {
            #     "data": [
            #         {"name": DISPLAY_NAMES.get(k.strip(), k.strip()), "value": v}
            #         for k, v in data["pie_totals"].items()
            #     ]
            # }
            # pie_path = os.path.join(state_folder, "community-pie-chart.json")
            # with open(pie_path, "w", encoding="utf-8") as f:
            #     json.dump(pie_json, f, indent=2, ensure_ascii=False)

            # for fname in ["community-map.json", "community-pie-chart.json"]:

            for fname in ["community-map.json"]:
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