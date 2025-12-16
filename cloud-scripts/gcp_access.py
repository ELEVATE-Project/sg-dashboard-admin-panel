from google.cloud import storage
from google.oauth2 import service_account
import os
import logging
from dotenv import load_dotenv

load_dotenv()

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def upload_file_to_gcs_and_get_directory(bucket_name, source_file_path, destination_blob_name):
    """
    Uploads a file to a Google Cloud Storage bucket and returns the public URL for the folder.
    """
    try:
        if not os.path.exists(source_file_path):
            logger.error(f"Source file not found: {source_file_path}")
            return None

        # Initialize GCS client with service account credentials from environment variables
        logger.info("Initializing GCS client with service account credentials from environment variables")
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )

        storage_client = storage.Client(credentials=credentials, project=service_account_info["project_id"])
        bucket = storage_client.bucket(bucket_name)

        logger.info(f"Uploading {source_file_path} to {bucket_name}/{destination_blob_name}")
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_path)

        logger.info(f"Making file {destination_blob_name} publicly accessible")
        blob.make_public()

        folder_path = os.path.dirname(destination_blob_name)
        if not folder_path:
            folder_path = ""

        public_folder_url = f"https://storage.googleapis.com/{bucket_name}/{folder_path}"
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