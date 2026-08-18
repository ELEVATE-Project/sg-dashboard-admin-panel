from google.cloud import storage
from google.oauth2 import service_account
import os
import logging
import json
import re
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")

service_account_info = {
    "type": os.getenv("TYPE"),
    "project_id": os.getenv("PROJECT_ID"),
    "private_key_id": os.getenv("PRIVATE_KEY_ID"),
    "private_key": PRIVATE_KEY.replace('\\n', '\n') if PRIVATE_KEY else None,
    "client_email": os.getenv("CLIENT_EMAIL"),
    "auth_uri": os.getenv("AUTH_URI"),
    "token_uri": os.getenv("TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("AUTH_PROVIDER_X509_CERT_URL"),
    "client_x509_cert_url": os.getenv("CLIENT_X509_CERT_URL"),
    "universe_domain": os.getenv("UNIVERSE_DOMAIN"),
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

GCS_BUCKET_URL_RE = re.compile(r"https://storage\.googleapis\.com/[^/]+")


def get_storage_client():
    if PRIVATE_KEY:
        logger.info("Initializing GCS client with service account credentials from environment variables")
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return storage.Client(credentials=credentials, project=service_account_info["project_id"])

    logger.info("Initializing GCS client with application default credentials")
    return storage.Client(project=service_account_info["project_id"] or None)


def get_public_bucket_url(bucket_name):
    configured_url = os.getenv("GCS_PUBLIC_BASE_URL") or os.getenv("BUCKET_URL")
    if configured_url:
        return configured_url.strip().strip('"').rstrip("/")
    return f"https://storage.googleapis.com/{bucket_name}"


def normalize_icon_urls(data, bucket_name):
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if key == "icon" and isinstance(value, str):
                normalized[key] = GCS_BUCKET_URL_RE.sub(
                    get_public_bucket_url(bucket_name),
                    value,
                    count=1
                )
            else:
                normalized[key] = normalize_icon_urls(value, bucket_name)
        return normalized

    if isinstance(data, list):
        return [normalize_icon_urls(item, bucket_name) for item in data]

    return data


def get_json_upload_payload(source_file_path, bucket_name):
    with open(source_file_path, "r", encoding="utf-8") as source_file:
        data = json.load(source_file)

    normalized_data = normalize_icon_urls(data, bucket_name)
    if normalized_data != data:
        with open(source_file_path, "w", encoding="utf-8") as source_file:
            json.dump(normalized_data, source_file, indent=2, ensure_ascii=False)

    return json.dumps(normalized_data, indent=2, ensure_ascii=False)


def upload_file_to_gcs_and_get_directory(bucket_name, source_file_path, destination_blob_name):
    """
    Uploads a file to a Google Cloud Storage bucket and returns the public URL for the folder.
    """
    try:
        if not os.path.exists(source_file_path):
            logger.error(f"Source file not found: {source_file_path}")
            return None

        storage_client = get_storage_client()
        bucket = storage_client.bucket(bucket_name)

        logger.info(f"Uploading {source_file_path} to {bucket_name}/{destination_blob_name}")
        blob = bucket.blob(destination_blob_name)

        if str(source_file_path).lower().endswith(".json"):
            blob.upload_from_string(
                get_json_upload_payload(source_file_path, bucket_name),
                content_type="application/json"
            )
        else:
            blob.upload_from_filename(source_file_path)

        logger.info(f"Making file {destination_blob_name} publicly accessible")
        blob.make_public()

        folder_path = os.path.dirname(destination_blob_name)
        if not folder_path:
            folder_path = ""

        public_folder_url = f"{get_public_bucket_url(bucket_name)}/{folder_path}".rstrip("/")
        logger.info(f"Generated public folder URL: {public_folder_url}")

        if blob.public_url:
            logger.info(f"Public URL for file: {blob.public_url}")
            return public_folder_url
        else:
            logger.error("File is not publicly accessible")
            return None

    except Exception as e:
        logger.error(f"Failed to upload file or generate public URL: {str(e)}")
        return None


def delete_file_from_gcs(bucket_name, blob_name):
    storage_client = get_storage_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.delete()


def delete_all_community_pie_charts_json(bucket_name):
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(bucket_name)

        prefix = "sg-dashboard/districts/"
        deleted = 0

        for blob in storage_client.list_blobs(bucket, prefix=prefix):
            if blob.name.endswith("community-pie-chart.json"):
                blob.delete()
                logger.info(f"🗑️ Deleted {blob.name}")
                deleted += 1

        logger.info(f"✅ Deleted {deleted} district community-pie-chart.json files")
        return deleted

    except Exception as e:
        logger.error(f"❌ Bulk delete failed: {str(e)}")
        return 0
