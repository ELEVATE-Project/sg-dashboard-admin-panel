import pandas as pd
import json
from datetime import datetime
import logging
import os
import configparser
import re
import subprocess
from dotenv import load_dotenv
from google.cloud import storage
from google.oauth2 import service_account

# Load environment variables from .env file
load_dotenv()

# Load configuration from config.ini
current_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(current_dir, '..', 'config.ini')

config = configparser.RawConfigParser()
config.read(config_path)
LOG_DIR = config.get("LOGS", "log_dir")
LOG_FILENAME_PREFIX = config.get("LOGS", "feeds_file")

# Required columns
REQUIRED_COLUMNS = ['story_id', 'action_steps', 'impact', 'add_to_frontend', 
                   'pii_flag', 'role', 'district', 'state', 'justification', 'confidence_score']
MANDATORY_OUTPUT_COLUMNS = ['action_steps', 'impact', 'pii_flag', 'role', 'district', 'state']

# Setup Logger
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filepath = os.path.join(LOG_DIR, f"{LOG_FILENAME_PREFIX}_{timestamp}.log")
    
    logger = logging.getLogger('CSVProcessor')
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

logger = setup_logger()

def get_config(section, key, default=None):
    try:
        return config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default

def get_gcp_credentials():
    """Create GCP credentials from environment variables"""
    logger.info("Loading GCP credentials from environment variables...")
    
    try:
        credentials_dict = {
            "type": get_config("GCP", "TYPE"),
            "project_id": get_config("GCP", "PROJECT_ID"),
            "private_key_id": get_config("GCP", "PRIVATE_KEY_ID"),
            "private_key": get_config("GCP", "PRIVATE_KEY", "").replace('\\n', '\n'),
            "client_email": get_config("GCP", "CLIENT_EMAIL"),
            "client_id": get_config("GCP", "CLIENT_ID"),
            "auth_uri": get_config("GCP", "AUTH_URl") or get_config("GCP", "auth_uri"),
            "token_uri": get_config("GCP", "TOKEN_URI"),
            "auth_provider_x509_cert_url": get_config("GCP", "AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": get_config("GCP", "CLIENT_X509_CERT_URL"),
            "universe_domain": get_config("GCP", "UNIVERSE_DOMAIN")
        }
        
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        logger.info("GCP credentials loaded successfully")
        return credentials
        
    except Exception as e:
        logger.error(f"Failed to load GCP credentials: {str(e)}")
        raise

def validate_columns(df, required_cols):
    logger.info("Validating DataFrame columns...")
    missing = set(required_cols) - set(df.columns)
    if missing:
        logger.error(f"Missing columns: {missing}")
        raise ValueError(f"Missing required columns: {missing}")
    logger.info("All required columns present")
    return True

def remove_null_empty_rows(df, columns_to_check):
    logger.info("Removing rows with null/empty mandatory columns...")
    initial_count = len(df)
    
    mask = pd.Series([True] * len(df))
    for col in columns_to_check:
        if col in df.columns:
            mask &= df[col].notna()
            mask &= df[col].astype(str).str.strip() != ''
            mask &= df[col].astype(str).str.strip() != 'nan'
    
    filtered_df = df[mask].copy()
    logger.info(f"Removed {initial_count - len(filtered_df)} rows. Remaining: {len(filtered_df)}")
    return filtered_df

def remove_pii_rows(df):
    logger.info("Filtering out rows with pii_flag = True...")
    initial_count = len(df)
    
    filtered_df = df[
        ~(
            (df['pii_flag'] == True) | 
            (df['pii_flag'] == 'TRUE') | 
            (df['pii_flag'] == 'true') | 
            (df['pii_flag'] == 1) |
            (df['pii_flag'] == '1')
        )
    ].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"Removed {removed_count} rows with PII flag. Remaining: {len(filtered_df)}")
    return filtered_df

def filter_data(df):
    """
    CORRECTED LOGIC:
    - Check if ANY row has add_to_frontend == True
    - If YES: Return ONLY those rows (ignore rows with null add_to_frontend)
    - If NO: Process 20 rows with confidence_score >= 0.9 and length > 30
    """
    logger.info("Applying business logic filters...")
    logger.debug(f"Total rows: {len(df)}")
    
    # Check for rows with add_to_frontend == True
    true_rows = df[
        (df['add_to_frontend'] == True) | 
        (df['add_to_frontend'] == 'TRUE') |
        (df['add_to_frontend'] == 'true') |
        (df['add_to_frontend'] == 1)
    ]
    
    if len(true_rows) > 0:
        logger.info(f"Found {len(true_rows)} rows with add_to_frontend=True")
        logger.info("Returning ONLY rows where add_to_frontend=True (ignoring null rows)")
        return true_rows.copy()
    
    else:
        logger.info("No rows with add_to_frontend=True found")
        logger.info("Processing rows with null add_to_frontend...")
        
        df_null = df[df['add_to_frontend'].isna()]
        logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
        if len(df_null) > 0:
            filtered_null = df_null[
                (df_null['confidence_score'] >= 0.9) &
                (df_null['action_steps'].str.len() > 30) &
                (df_null['impact'].str.len() > 30)
            ]
            logger.debug(f"Eligible rows (confidence>=0.9, length>30): {len(filtered_null)}")
            
            if len(filtered_null) > 0:
                selected_rows = filtered_null.sample(n=min(20, len(filtered_null)), random_state=None)
                logger.info(f"Selected {len(selected_rows)} random rows from eligible null rows")
                return selected_rows
            else:
                logger.warning("No rows meet the criteria (confidence>=0.9, length>30)")
                return pd.DataFrame()
        else:
            logger.warning("No rows with null add_to_frontend to process")
            return pd.DataFrame()

def clean_action_steps(text):
    if pd.isna(text) or text == '':
        return text
    return re.sub(r'\s+', ' ', str(text).replace('[', '').replace(']', '').replace('"', '').replace("'", '')).strip()

def construct_json(df):
    logger.info("Constructing JSON...")
    output_df = df[['action_steps', 'impact', 'role', 'district', 'state']].copy()
    
    logger.info("Cleaning action_steps column...")
    output_df['action_steps'] = output_df['action_steps'].apply(clean_action_steps)
    
    data_list = [
        {
            "action_step": row['action_steps'],
            "impact": row['impact'],
            "role": row['role'],
            "district": row['district'],
            "state": row['state']
        }
        for idx, row in output_df.iterrows()
    ]
    
    json_output = {"identification": "microimprovement_feed", "data": data_list}
    logger.info(f"JSON constructed with {len(data_list)} records")
    return json_output

def save_json_locally(json_data, output_dir, filename):
    logger.info("Saving JSON locally...")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    file_size = os.path.getsize(filepath)
    logger.info(f"Saved to: {filepath} ({file_size/1024:.2f} KB)")
    return filepath

def upload_to_gcp(json_data, bucket_name, blob_name, credentials):
    """Upload JSON to GCP bucket using credentials from .env"""
    logger.info(f"Uploading to GCP bucket: {bucket_name}/{blob_name}")
    
    try:
        # Initialize GCS client with credentials
        storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # Convert to JSON string and upload
        json_string = json.dumps(json_data, indent=2)
        blob.upload_from_string(json_string, content_type='application/json')
        
        logger.info(f"Successfully uploaded to gs://{bucket_name}/{blob_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to upload to GCP: {str(e)}", exc_info=True)
        logger.info("Falling back to gsutil method...")
        return False

def fetch_the_csv_file_from_gcp(bucket_name, blob_path, credentials):
    """
    Fetch CSV file from GCP bucket inside a given blob path whose
    filename starts with 'microimprovement_feed'
    """
    file_prefix = "microimprovement_feed"
    logger.info(
        f"Searching for '{file_prefix}' inside "
        f"gs://{bucket_name}/{blob_path}/"
    )

    try:
        storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = storage_client.bucket(bucket_name)

        if blob_path.startswith(f"{bucket_name}/"):
            blob_path = blob_path[len(bucket_name)+1:]

        blob_path = blob_path.rstrip("/") + "/"

        blobs = bucket.list_blobs(prefix=blob_path)

        matching_blobs = [
            blob for blob in blobs
            if os.path.basename(blob.name).startswith(file_prefix)
        ]

        if not matching_blobs:
            logger.warning(
                f"File not found: No file starting with "
                f"'{file_prefix}' in {blob_path}"
            )
            return "file not found"

        latest_blob = max(matching_blobs, key=lambda b: b.updated)

        local_file_path = f"/tmp/{os.path.basename(latest_blob.name)}"

        latest_blob.download_to_filename(local_file_path)

        logger.info(f"✓ Successfully fetched: {latest_blob.name}")
        return local_file_path

    except Exception as e:
        logger.error(f"Failed to fetch from GCP: {str(e)}", exc_info=True)
        return None

def upload_to_gcp_gsutil(local_filepath, bucket_name, blob_name):
    """Fallback: Upload using gsutil"""
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
            logger.info(f"Successfully uploaded to {gs_path}")
            return True
        else:
            logger.error(f"gsutil upload failed: {result.stderr}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to upload: {str(e)}")
        return False

def main(bucket_name, output_blob_path, input_blob_path, output_filename):

    try:
        logger.info("="*60)
        logger.info("Starting CSV processing pipeline")
        logger.info("="*60)
        
        credentials = get_gcp_credentials()
        
        csv_path = fetch_the_csv_file_from_gcp(bucket_name,input_blob_path, credentials)
        
        df = pd.read_csv(csv_path)
        
        validate_columns(df, REQUIRED_COLUMNS)
        
        df = remove_null_empty_rows(df, MANDATORY_OUTPUT_COLUMNS)
        
        if len(df) == 0:
            logger.warning("No rows after removing null/empty values")
            return None
        
        df = remove_pii_rows(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing PII flagged data")
            return None
        
        filtered_df = filter_data(df)
        
        if len(filtered_df) == 0:
            logger.warning("No rows matched business logic criteria")
            return None
        
        json_output = construct_json(filtered_df)
        
        # Step 7: Save locally
        # local_filename = output_filename.split('/')[-1]
        # local_filepath = save_json_locally(json_output, local_output_dir, local_filename)
        
        
        final_output_path = output_blob_path
        if final_output_path.startswith(f"{bucket_name}/"):
            final_output_path = final_output_path[len(bucket_name)+1:]
        
        final_output_path = final_output_path.rstrip("/") + "/" + output_filename

        upload_success = upload_to_gcp(json_output, bucket_name, final_output_path, credentials)
        
        if not upload_success:
            raise Exception("Failed to upload JSON to GCP")
        
        logger.info("="*60)
        logger.info("✓ PIPELINE COMPLETED SUCCESSFULLY!")
        logger.info("="*60)
        return json_output
        
    except Exception as e:
        logger.error("="*60)
        logger.error("✗ PIPELINE FAILED")
        logger.error("="*60)
        logger.error(f"Error: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    # Read configuration from .env file
    INPUT_BLOB_PATH = config.get("GCP", "INPUT_BLOB_PATH")
    BUCKET_NAME = config.get("GCP", "BUCKET_NAME")  
    OUTPUT_BLOB_PATH = config.get("GCP", "OUTPUT_BLOB_PATH")
    OUTPUT_FILENAME = "microimprovement_feed.json"
    
    # CSV_PATH = "/Users/user/Documents/shikshalokam/elevate-analytics/dev/data-pipeline/Documentation/sg-batch-scripts/json_constructor/data-files/micro_improvement_feed.csv"
    # BUCKET_NAME = "dev-sg-dashboard"
    # OUTPUT_FILENAME = "sg-dashboard/micro_improvement_feed.json"
    # LOCAL_OUTPUT_DIR = "/Users/user/Documents/shikshalokam/elevate-analytics/dev/data-pipeline/Documentation/sg-batch-scripts/json_constructor/data-files"

    
    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        main(BUCKET_NAME,OUTPUT_BLOB_PATH,INPUT_BLOB_PATH,OUTPUT_FILENAME)
        
        logger.info("Application finished successfully")
    except Exception as e:
        logger.error(f"Application terminated: {str(e)}")
        raise