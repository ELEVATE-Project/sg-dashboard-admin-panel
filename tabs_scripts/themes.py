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
LOG_FILENAME_PREFIX = config.get("LOGS", "themes_file")

# Required columns
REQUIRED_COLUMNS = ['discussion_id', 'challenge', 'theme_name', 'add_to_frontend', 
                   'theme_id', 'pii_flag', 'role', 'district', 'state', 'confidence_score', 
                   'justification']
MANDATORY_OUTPUT_COLUMNS = ['challenge', 'theme_name', 'theme_id', 'pii_flag', 'role', 'district', 'state']

# Setup Logger
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filepath = os.path.join(LOG_DIR, f"{LOG_FILENAME_PREFIX}_{timestamp}.log")
    
    logger = logging.getLogger('ThemesProcessor')
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
        logger.info("✓ GCP credentials loaded successfully")
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
    logger.info("✓ All required columns present")
    return True

def calculate_theme_counts(df):
    """
    Calculate total challenge count per theme from original data
    This should be called after column validation but before any filtering
    Returns: dict with (theme_id, theme_name) as key and count as value
    """
    logger.info("Calculating total challenge counts per theme...")
    
    # Group by theme_id and theme_name to count challenges
    theme_counts = df.groupby(['theme_id', 'theme_name']).size().to_dict()
    
    logger.info(f"✓ Calculated counts for {len(theme_counts)} themes")
    for (theme_id, theme_name), count in theme_counts.items():
        logger.debug(f"  Theme '{theme_name}' (ID: {theme_id}): {count} total challenges")
    
    return theme_counts

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
    logger.info(f"✓ Removed {initial_count - len(filtered_df)} rows. Remaining: {len(filtered_df)}")
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
    logger.info(f"✓ Removed {removed_count} rows with PII flag. Remaining: {len(filtered_df)}")
    return filtered_df

def remove_unknown_themes(df):
    logger.info("Removing rows with theme_name = Unknown/Unclear...")
    initial_count = len(df)
    
    filtered_df = df[
        ~(
            (df['theme_name'].str.strip().str.lower() == 'unknown') |
            (df['theme_name'].str.strip().str.lower() == 'unclear') |
            (df['theme_name'].str.strip().str.lower() == 'unknown/unclear')
        )
    ].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} rows with Unknown/Unclear themes. Remaining: {len(filtered_df)}")
    return filtered_df

def filter_data_by_theme(df):
    """
    LOGIC:
    - Check if ANY row has add_to_frontend == True
    - If YES: Return ONLY those rows
    - If NO: For EACH theme, select up to 20 rows with filters
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
        # Return ONLY True rows
        logger.info(f"✓ Found {len(true_rows)} rows with add_to_frontend=True")
        logger.info("Returning ONLY rows where add_to_frontend=True")
        return true_rows.copy()
    
    else:
        # Process null rows per theme with filters
        logger.info("No rows with add_to_frontend=True found")
        logger.info("Processing rows with null add_to_frontend (20 per theme)...")
        
        df_null = df[df['add_to_frontend'].isna()]
        logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
        if len(df_null) == 0:
            logger.warning("No rows with null add_to_frontend to process")
            return pd.DataFrame()
        
        # Apply filters: confidence >= 0.9 and challenge length > 30
        filtered_null = df_null[
            (df_null['confidence_score'] >= 0.9) &
            (df_null['challenge'].str.len() > 30)
        ]
        logger.debug(f"Eligible rows (confidence>=0.9, challenge length>30): {len(filtered_null)}")
        
        if len(filtered_null) == 0:
            logger.warning("No rows meet the criteria (confidence>=0.9, challenge length>30)")
            return pd.DataFrame()
        
        # For EACH theme, select up to 20 random rows
        selected_rows_list = []
        grouped = filtered_null.groupby('theme_name')
        
        for theme_name, group in grouped:
            theme_sample_size = min(20, len(group))
            theme_sample = group.sample(n=theme_sample_size, random_state=None)
            selected_rows_list.append(theme_sample)
            logger.info(f"  Theme '{theme_name}': Selected {len(theme_sample)}/{len(group)} rows")
        
        result_df = pd.concat(selected_rows_list, ignore_index=True)
        logger.info(f"✓ Selected total of {len(result_df)} rows across all themes")
        return result_df

def clean_text(text):
    """Clean text by removing extra spaces and special characters"""
    if pd.isna(text) or text == '':
        return text
    return re.sub(r'\s+', ' ', str(text).strip())

def construct_json(df, theme_counts):
    logger.info("Constructing JSON...")
    
    # Group by theme_id and theme_name
    grouped = df.groupby(['theme_id', 'theme_name'])
    
    data_list = []
    
    for (theme_id, theme_name), group in grouped:
        # Get the ORIGINAL total count from pre-calculated theme_counts
        original_count = theme_counts.get((theme_id, theme_name), len(group))
        
        # Build list of challenges (only filtered ones)
        challenge_list = []
        for idx, row in group.iterrows():
            challenge_obj = {
                "description": clean_text(row['challenge']),
                "voice_by": clean_text(row['role']),
                "district": clean_text(row['district']),
                "state": clean_text(row['state'])
            }
            challenge_list.append(challenge_obj)
        
        # Build theme object
        theme_obj = {
            "id": str(theme_id),
            "label": clean_text(theme_name),
            "value": str(original_count),  # Using original count, not filtered count
            "list": challenge_list
        }
        
        data_list.append(theme_obj)
        logger.debug(f"  Theme '{theme_name}' (ID: {theme_id}): {original_count} total challenges, {len(group)} in filtered list")
    
    json_output = {
        "identification": "themes_emerged",
        "data": data_list
    }
    
    logger.info(f"✓ JSON constructed with {len(data_list)} themes")
    return json_output

def save_json_locally(json_data, output_dir, filename):
    logger.info("Saving JSON locally...")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    file_size = os.path.getsize(filepath)
    logger.info(f"✓ Saved to: {filepath} ({file_size/1024:.2f} KB)")
    return filepath

def upload_to_gcp(json_data, bucket_name, blob_name, credentials):
    """Upload JSON to GCP bucket using credentials from .env"""
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

def fetch_the_csv_file_from_gcp(bucket_name, blob_path, credentials):
    """
    Fetch CSV file from GCP bucket inside a given blob path whose
    filename starts with 'themes_emerged'
    """
    file_prefix = "themes_emerged"
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
        logger.info(f"Actual blob path prefix: {blob_path}")

        blobs = list(bucket.list_blobs(prefix=blob_path)) # Convert to list to reuse/debug
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
            logger.info(f"✓ Successfully uploaded to {gs_path}")
            return True
        else:
            logger.error(f"✗ gsutil upload failed: {result.stderr}")
            return False
        
    except Exception as e:
        logger.error(f"✗ Failed to upload: {str(e)}")
        return False

def main(bucket_name,output_blob_path,input_blob_path,output_filename):
    try:
        logger.info("="*60)
        logger.info("Starting Themes Emerged CSV processing pipeline")
        logger.info("="*60)
        
        credentials = get_gcp_credentials()
        
        csv_path = fetch_the_csv_file_from_gcp(bucket_name,input_blob_path, credentials)
        
        if not csv_path:
            logger.error("Failed to fetch CSV file. Aborting.")
            return None

        df = pd.read_csv(csv_path)
        validate_columns(df, REQUIRED_COLUMNS)
        theme_counts = calculate_theme_counts(df)
        
        df = remove_null_empty_rows(df, MANDATORY_OUTPUT_COLUMNS)
        
        if len(df) == 0:
            logger.warning("No rows after removing null/empty values")
            return None
        
        df = remove_pii_rows(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing PII flagged data")
            return None
    
        df = remove_unknown_themes(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing Unknown/Unclear themes")
            return None
        
        filtered_df = filter_data_by_theme(df)
        
        if len(filtered_df) == 0:
            logger.warning("No rows matched business logic criteria")
            return None
        
        json_output = construct_json(filtered_df, theme_counts)
        
        final_output_path = output_blob_path
        if final_output_path.startswith(f"{bucket_name}/"):
            final_output_path = final_output_path[len(bucket_name)+1:]
        
        # Prepare output path
        final_output_path = output_blob_path
        if final_output_path.startswith(f"{bucket_name}/"):
            final_output_path = final_output_path[len(bucket_name)+1:]
        
        final_output_path = final_output_path.rstrip("/") + "/" + output_filename
        
        upload_success = upload_to_gcp(json_output, bucket_name, final_output_path, credentials)
        
        if not upload_success:
            raise Exception("Failed to upload JSON to GCP")
        else:
            logger.info("Successfully uploaded to gs://{}")
        
        logger.info("="*60)
        logger.info("PIPELINE COMPLETED SUCCESSFULLY!")
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
    # CSV_PATH = os.getenv("THEMES_CSV_PATH")
    # BUCKET_NAME = os.getenv("BUCKET_NAME")
    # OUTPUT_FILENAME = os.getenv("THEMES_OUTPUT_BLOB_PATH")
    # LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR")

    # Read configuration from .env file
    INPUT_BLOB_PATH = config.get("GCP", "INPUT_BLOB_PATH")
    BUCKET_NAME = config.get("GCP", "BUCKET_NAME")  
    OUTPUT_BLOB_PATH = config.get("GCP", "OUTPUT_BLOB_PATH")
    OUTPUT_FILENAME = "themes_emerged.json"
    
    
    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        main(BUCKET_NAME,OUTPUT_BLOB_PATH,INPUT_BLOB_PATH,OUTPUT_FILENAME)
        
        logger.info("="*60)
        logger.info("Application finished successfully")
        logger.info("="*60)
        
    except Exception as e:
        logger.error("="*60)
        logger.error("✗ PIPELINE FAILED")
        logger.error("="*60)
        logger.error(f"Error: {str(e)}", exc_info=True)
        raise