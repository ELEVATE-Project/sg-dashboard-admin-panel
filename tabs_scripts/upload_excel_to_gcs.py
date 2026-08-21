import importlib.util
import os
import re
from datetime import datetime, timezone

from constants import GCS_EXCEL_UPLOAD_PREFIX


def _load_gcp_access():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    gcp_access_path = os.path.join(script_dir, "..", "cloud-scripts", "gcp_access.py")
    spec = importlib.util.spec_from_file_location("gcp_access", gcp_access_path)
    gcp_access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcp_access)
    return gcp_access


def _safe_filename(filename):
    name = os.path.basename(filename or "uploaded-file.xlsx")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "uploaded-file.xlsx"


def _get_bucket():
    bucket_name = os.environ.get("BUCKET_NAME")
    if not bucket_name:
        return None, None

    gcp_access = _load_gcp_access()
    storage_client = gcp_access.get_storage_client()
    return bucket_name, storage_client.bucket(bucket_name)


def _format_blob_details(bucket_name, blob):
    uploaded_at = blob.time_created
    if uploaded_at:
        uploaded_at = uploaded_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return {
        "file_name": os.path.basename(blob.name),
        "gcs_path": f"gs://{bucket_name}/{blob.name}",
        "blob_name": blob.name,
        "uploaded_at": uploaded_at or "",
        "size_bytes": blob.size or 0,
        "content_type": blob.content_type or "",
    }


def upload_excel_to_gcs(uploaded_file):
    try:
        bucket_name, bucket = _get_bucket()
        if not bucket:
            print("❌ BUCKET_NAME not set. Skipping Excel source upload.")
            return None

        if not uploaded_file or not uploaded_file.name.lower().endswith(".xlsx"):
            return None

        current_position = uploaded_file.tell()
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        uploaded_file.seek(current_position)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{timestamp}_{_safe_filename(uploaded_file.name)}"
        destination_blob_name = f"{GCS_EXCEL_UPLOAD_PREFIX}/{filename}"

        blob = bucket.blob(destination_blob_name)
        blob.upload_from_string(
            file_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        print(f"✅ Uploaded source Excel to gs://{bucket_name}/{destination_blob_name}")
        blob.reload()
        return _format_blob_details(bucket_name, blob)
    except Exception as e:
        print(f"❌ Failed to upload source Excel to GCS. Continuing without archive: {e}")
        return None


def list_uploaded_excels():
    bucket_name, bucket = _get_bucket()
    if not bucket:
        return []

    blobs = bucket.list_blobs(prefix=f"{GCS_EXCEL_UPLOAD_PREFIX}/")
    excel_files = [
        _format_blob_details(bucket_name, blob)
        for blob in blobs
        if blob.name.lower().endswith(".xlsx")
    ]
    return sorted(excel_files, key=lambda item: item["uploaded_at"], reverse=True)


def delete_uploaded_excel(blob_name):
    bucket_name, bucket = _get_bucket()
    if not bucket or not blob_name:
        return False

    if not blob_name.startswith(f"{GCS_EXCEL_UPLOAD_PREFIX}/"):
        raise ValueError("Only source Excel uploads can be deleted from this screen.")

    blob = bucket.blob(blob_name)
    blob.delete()
    print(f"✅ Deleted source Excel gs://{bucket_name}/{blob_name}")
    return True


def download_uploaded_excel(blob_name):
    _, bucket = _get_bucket()
    if not bucket or not blob_name:
        return None

    if not blob_name.startswith(f"{GCS_EXCEL_UPLOAD_PREFIX}/"):
        raise ValueError("Only source Excel uploads can be downloaded from this screen.")

    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()
