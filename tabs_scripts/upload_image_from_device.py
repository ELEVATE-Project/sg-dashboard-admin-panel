import os
import re
import time
import importlib.util
from pathlib import Path

UNDO_TIMEOUT = 15
ALLOWED_EXTENSIONS = {".svg"}


def clean_name(name):
    return re.sub(r'[^a-z0-9_-]', '', name.lower().replace(" ", "_"))


def load_gcp_module():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    gcp_path = os.path.join(base_dir, '..', 'cloud-scripts', 'gcp_access.py')

    spec = importlib.util.spec_from_file_location("gcp_access", gcp_path)
    gcp_access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gcp_access)
    return gcp_access


def upload_svg_from_local(file_path: str, image_name: str):
    if not file_path or not os.path.exists(file_path):
        return False, "File not found"

    if Path(file_path).suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, "Only SVG images are allowed"

    if not image_name.strip():
        return False, "Image name is required"

    image_name = clean_name(image_name)
    filename = f"{image_name}.svg"

    gcp_access = load_gcp_module()
    destination_path = f"sg-dashboard/assets/icons/{filename}"

    folder_url = gcp_access.upload_file_to_gcs_and_get_directory(
        bucket_name=os.environ.get("BUCKET_NAME"),
        source_file_path=file_path,
        destination_blob_name=destination_path
    )

    if not folder_url:
        return False, "Upload failed"

    final_url = f"{folder_url.rstrip('/')}/{filename}"

    return True, {
        "url": final_url,
        "blob_path": destination_path,
        "uploaded_at": time.time()
    }


def undo_upload(blob_path: str):
    gcp_access = load_gcp_module()
    gcp_access.delete_file_from_gcs(
        bucket_name=os.environ.get("BUCKET_NAME"),
        blob_name=blob_path
    )
