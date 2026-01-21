"""
Shared utility functions for backend data processing
"""
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from google.cloud import storage
from google.oauth2 import service_account

# Load environment variables from .env file
load_dotenv()


def get_env_var(name: str, default=None) -> str:
    """Get environment variable with optional default value"""
    value = os.getenv(name, default)
    if value is None:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def get_gcp_credentials():
    """Create GCP credentials from environment variables"""
    try:
        credentials_dict = {
            "type": get_env_var("TYPE"),
            "project_id": get_env_var("PROJECT_ID"),
            "private_key_id": get_env_var("PRIVATE_KEY_ID"),
            "private_key": get_env_var("PRIVATE_KEY").replace('\\n', '\n'),
            "client_email": get_env_var("CLIENT_EMAIL"),
            "client_id": get_env_var("CLIENT_ID"),
            "auth_uri": get_env_var("AUTH_URI"),
            "token_uri": get_env_var("TOKEN_URI"),
            "auth_provider_x509_cert_url": get_env_var("AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": get_env_var("CLIENT_X509_CERT_URL"),
            "universe_domain": get_env_var("UNIVERSE_DOMAIN")
        }
        
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return credentials
        
    except Exception as e:
        raise Exception(f"Failed to load GCP credentials: {str(e)}")


def setup_logger(log_dir, log_filename, logger_name='Processor'):
    """Setup logger with file and console handlers"""
    # Handle None log_dir with a default
    if log_dir is None:
        log_dir = "./logs"
        
    os.makedirs(log_dir, exist_ok=True)
    log_filepath = os.path.join(log_dir, log_filename)
    
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_filepath)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.info(f"Logger initialized. Log file: {log_filepath}")
    return logger


def fetch_csv_from_gcp(local_csv_file_path, bucket_name, blob_path, credentials, file_prefix, logger):
    """
    Fetch CSV file from GCP bucket inside a given blob path whose
    filename starts with the given prefix
    """
    logger.info(
        f"Searching for '{file_prefix}' inside "
        f"gs://{bucket_name}/{blob_path}/"
    )

    try:
        # CREATE THE DIRECTORY IF IT DOESN'T EXIST
        if not local_csv_file_path:
            logger.error("local_csv_file_path is None or empty")
            return None

        dir_path = os.path.dirname(local_csv_file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        
        storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = storage_client.bucket(bucket_name)

        if blob_path.startswith(f"{bucket_name}/"):
            blob_path = blob_path[len(bucket_name)+1:]

        blob_path = blob_path.rstrip("/") + "/"
        logger.info(f"Actual blob path prefix: {blob_path}")

        blobs = list(bucket.list_blobs(prefix=blob_path))
        
        if not blobs:
            logger.warning(f"No blobs found at all in path: {blob_path}")
            return None

        matching_blobs = [
            blob for blob in blobs
            if os.path.basename(blob.name).startswith(file_prefix)
        ]

        if not matching_blobs:
            logger.warning(
                f"File not found: No file starting with "
                f"'{file_prefix}' in {blob_path}"
            )
            logger.info("Available files in this path:")
            for b in blobs:
                logger.info(f" - {b.name}")
            return None

        latest_blob = max(matching_blobs, key=lambda b: b.updated)

        latest_blob.download_to_filename(local_csv_file_path)

        logger.info(f"✓ Successfully fetched: {latest_blob.name}")
        return local_csv_file_path

    except Exception as e:
        logger.error(f"Failed to fetch from GCP: {str(e)}", exc_info=True)
        return None


def upload_to_gcp(json_data, bucket_name, blob_name, credentials, logger):
    """Upload JSON to GCP bucket using credentials from .env"""
    import json
    
    logger.info(f"Uploading to GCP bucket: {bucket_name}/{blob_name}")
    
    try:
        storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        json_string = json.dumps(json_data, indent=2)
        blob.upload_from_string(json_string, content_type='application/json')
        
        logger.info(f"✓ Successfully uploaded to gs://{bucket_name}/{blob_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to upload to GCP: {str(e)}", exc_info=True)
        logger.info("Falling back to gsutil method...")
        return False


def upload_to_gcp_gsutil(local_filepath, bucket_name, blob_name, logger):
    """Fallback: Upload using gsutil"""
    import subprocess
    
    logger.info(f"Uploading to GCP using gsutil...")
    
    try:
        gs_path = f"gs://{bucket_name}/{blob_name}"
        result = subprocess.run(
            ['gsutil', 'cp', local_filepath, gs_path],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            logger.info(f"✓ Successfully uploaded to {gs_path}")
            return True
        else:
            logger.error(f"✗ gsutil upload failed: {result.stderr}")
            return False
        
    except Exception as e:
        logger.error(f"✗ Failed to upload: {str(e)}")
        return False


def save_json_locally(json_data, output_dir, filename, logger):
    """Save JSON to local directory"""
    import json
    
    logger.info("Saving JSON locally...")
    
    # Handle None output_dir with a default
    if output_dir is None:
        output_dir = "./json_output"
        logger.warning(f"output_dir was None, using default: {output_dir}")
    
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    file_size = os.path.getsize(filepath)
    logger.info(f"✓ Saved to: {filepath} ({file_size/1024:.2f} KB)")
    return filepath


def save_csv_locally(df, output_dir, filename, logger):
    """Save CSV to local directory"""
    logger.info("Saving CSV locally...")
    
    # Handle None output_dir with a default
    if output_dir is None:
        output_dir = "./csv_output"
        logger.warning(f"output_dir was None, using default: {output_dir}")
    
    os.makedirs(output_dir, exist_ok=True)
    csv_filename = "filtered_" + filename.replace('.json', '.csv')
    filepath = os.path.join(output_dir, csv_filename)
    
    df.to_csv(filepath, index=False, encoding='utf-8')
    
    file_size = os.path.getsize(filepath)
    logger.info(f"✓ Saved to: {filepath} ({file_size/1024:.2f} KB)")
    return filepath