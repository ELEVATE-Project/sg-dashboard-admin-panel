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
config = configparser.ConfigParser()
config.read('config.ini')
LOG_DIR = config.get("LOGS", "log_dir")
LOG_FILENAME_PREFIX = config.get("LOGS", "animations_log_filename_prefix")

# Required columns
REQUIRED_COLUMNS = [
    'challenge_id', 'challenge_user_role', 'challenge_district', 'challenge_state', 'challenge',
    'add_to_frontend', 'solution_id', 'solutions', 'solution_user_role', 
    'solution_district', 'solution_state'
]

MANDATORY_OUTPUT_COLUMNS = [
    'challenge_id', 'challenge_user_role', 'challenge_district', 'challenge_state', 'challenge',
    'solution_id', 'solutions', 'solution_user_role', 'solution_district', 'solution_state'
]

# Setup Logger
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filepath = os.path.join(LOG_DIR, f"{LOG_FILENAME_PREFIX}.log")
    
    logger = logging.getLogger('ChallengesSolutionsProcessor')
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

def get_gcp_credentials():
    """Create GCP credentials from environment variables"""
    logger.info("Loading GCP credentials from environment variables...")
    
    try:
        credentials_dict = {
            "type": os.getenv("TYPE"),
            "project_id": os.getenv("PROJECT_ID"),
            "private_key_id": os.getenv("PRIVATE_KEY_ID"),
            "private_key": os.getenv("PRIVATE_KEY").replace('\\n', '\n'),
            "client_email": os.getenv("CLIENT_EMAIL"),
            "client_id": os.getenv("CLIENT_ID"),
            "auth_uri": os.getenv("AUTH_URI"),
            "token_uri": os.getenv("TOKEN_URI"),
            "auth_provider_x509_cert_url": os.getenv("AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": os.getenv("CLIENT_X509_CERT_URL"),
            "universe_domain": os.getenv("UNIVERSE_DOMAIN")
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

def remove_spelling_mistakes(df):
    """
    Remove rows with gibberish/invalid text in challenge and solutions columns
    """
    logger.info("Filtering out rows with gibberish/invalid text...")
    initial_count = len(df)
    
    def is_low_quality_text(text):
        if pd.isna(text) or text == '':
            return False
        
        text = str(text).lower()
        
        # 1. Check for keyboard spam
        if re.search(r'(asdf|qwer|zxcv|hjkl|lkjh){2,}', text):
            return True
        
        # 2. Check for excessive special characters (>30% of text)
        special_chars = len(re.findall(r'[^a-zA-Z0-9\s]', text))
        if len(text) > 0 and (special_chars / len(text)) > 0.3:
            return True
        
        # 3. Check for repeating characters (>4 times)
        if re.search(r'(.)\1{4,}', text):
            return True
        
        # 4. Check for words with no vowels (excluding common abbreviations)
        words = text.split()
        no_vowel_words = []
        
        for word in words:
            clean_word = re.sub(r'[^a-z]', '', word.lower())
            
            # Skip short words and common abbreviations
            if len(clean_word) <= 3:
                continue
            
            # Check if word has no vowels
            if not re.search(r'[aeiou]', clean_word):
                no_vowel_words.append(word)
        
        # Flag if >30% of words have no vowels
        if len(words) > 3 and len(no_vowel_words) / len(words) > 0.3:
            return True
        
        # 5. Check for excessive consonant clusters (5+ consonants in a row)
        excessive_consonants = len(re.findall(r'[^aeiou\s]{5,}', text))
        if excessive_consonants > 2:
            return True
        
        # 6. Check for very low vowel ratio (<15%)
        letters = re.sub(r'[^a-z]', '', text)
        if len(letters) > 0:
            vowel_count = len(re.findall(r'[aeiou]', letters))
            vowel_ratio = vowel_count / len(letters)
            if vowel_ratio < 0.15:
                return True
        
        return False
    
    # Check both challenge and solutions columns
    filtered_df = df[
        ~(df['challenge'].apply(is_low_quality_text) | 
          df['solutions'].apply(is_low_quality_text))
    ].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} rows with low-quality text. Remaining: {len(filtered_df)}")
    return filtered_df

def filter_data_by_state(df):
    """
    LOGIC:
    - Check if ANY row has add_to_frontend == True
    - If YES: Return ONLY those rows
    - If NO: Select 25 rows per state (distributed proportionally, mixed districts)
    """
    logger.info("Applying business logic filters...")
    logger.debug(f"Total rows: {len(df)}")
    
    # Check for rows with add_to_frontend == True
    true_rows = df[
        (df['add_to_frontend'] == True) | 
        (df['add_to_frontend'] == 'TRUE') |
        (df['add_to_frontend'] == 'true') |
        (df['add_to_frontend'] == 1) |
        (df['add_to_frontend'] == '1')
    ]
    
    if len(true_rows) > 0:
        # Return ONLY True rows
        logger.info(f"✓ Found {len(true_rows)} rows with add_to_frontend=True")
        logger.info("Returning ONLY rows where add_to_frontend=True")
        return true_rows.copy()
    
    else:
        # Process null rows - 25 per state
        logger.info("No rows with add_to_frontend=True found")
        logger.info("Processing rows with null add_to_frontend (25 per state)...")
        
        df_null = df[df['add_to_frontend'].isna()]
        logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
        if len(df_null) == 0:
            logger.warning("No rows with null add_to_frontend to process")
            return pd.DataFrame()
        
        # Get unique states from challenge_state column
        unique_states = df_null['challenge_state'].str.strip().unique()
        num_states = len(unique_states)
        
        if num_states == 0:
            logger.warning("No states found in data")
            return pd.DataFrame()
        
        logger.info(f"Found {num_states} unique states: {', '.join(unique_states)}")
        
        selected_rows_list = []
        
        # For each state, select up to 25 rows
        for state in unique_states:
            state_data = df_null[df_null['challenge_state'].str.strip() == state]
            
            # Get unique challenge_ids for this state
            unique_challenges = state_data['challenge_id'].unique()
            
            # Sample up to 25 unique challenges
            sample_size = min(25, len(unique_challenges))
            sampled_challenge_ids = pd.Series(unique_challenges).sample(n=sample_size, random_state=None).tolist()
            
            # Get all rows for the sampled challenges (includes their solutions)
            state_sample = state_data[state_data['challenge_id'].isin(sampled_challenge_ids)]
            
            selected_rows_list.append(state_sample)
            
            num_districts = state_sample['challenge_district'].nunique()
            logger.info(f"  State '{state}': Selected {sample_size} challenges ({len(state_sample)} total rows) from {num_districts} districts")
        
        if not selected_rows_list:
            logger.warning("No rows selected")
            return pd.DataFrame()
        
        result_df = pd.concat(selected_rows_list, ignore_index=True)
        logger.info(f"✓ Selected total of {len(result_df)} rows")
        return result_df

def clean_text(text):
    """Clean text by removing extra spaces"""
    if pd.isna(text) or text == '':
        return text
    return re.sub(r'\s+', ' ', str(text).strip())

def construct_json(df):
    logger.info("Constructing JSON...")
    
    # Group by challenge_id to get challenges with their solutions
    grouped = df.groupby('challenge_id')
    
    data_list = []
    
    for challenge_id, group in grouped:
        # Get challenge info from first row (all rows with same challenge_id have same challenge info)
        first_row = group.iloc[0]
        
        # Build solutions list
        solutions_list = []
        for idx, row in group.iterrows():
            solution_obj = {
                "id": str(row['solution_id']),
                "role": clean_text(row['solution_user_role']),
                "district": clean_text(row['solution_district']),
                "state": clean_text(row['solution_state']),
                "solution": clean_text(row['solutions'])
            }
            solutions_list.append(solution_obj)
        
        # Build challenge object
        challenge_obj = {
            "id": str(challenge_id),
            "role": clean_text(first_row['challenge_user_role']),
            "district": clean_text(first_row['challenge_district']),
            "state": clean_text(first_row['challenge_state']),
            "challenge": clean_text(first_row['challenge']),
            "solutions": solutions_list
        }
        
        data_list.append(challenge_obj)
        logger.debug(f"  Challenge {challenge_id}: {len(solutions_list)} solutions")
    
    json_output = {
        "identification": "challenges_solutions",
        "data": data_list
    }
    
    logger.info(f"✓ JSON constructed with {len(data_list)} challenges")
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

def save_csv_locally(df, output_dir, filename):
    logger.info("Saving CSV locally...")
    os.makedirs(output_dir, exist_ok=True)
    csv_filename = "filtered_" + filename.replace('.json', '.csv')
    filepath = os.path.join(output_dir, csv_filename)
    
    df.to_csv(filepath, index=False, encoding='utf-8')
    
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

def fetch_the_csv_file_from_gcp(local_csv_file_path, bucket_name, blob_path, credentials):
    """
    Fetch CSV file from GCP bucket inside a given blob path whose
    filename starts with 'cracks_and_flowers'
    """
    file_prefix = "cracks_and_flowers.csv"
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

        latest_blob.download_to_filename(local_csv_file_path)

        logger.info(f"✓ Successfully fetched: {latest_blob.name}")

        return local_csv_file_path

    except Exception as e:
        logger.error(f"Failed to fetch from GCP: {str(e)}", exc_info=True)
        return None

def process_csv(csv_path, bucket_name, output_filename, local_output_dir):
    try:
        logger.info("="*60)
        logger.info("Starting Challenges-Solutions CSV processing pipeline")
        logger.info("="*60)
        logger.info(f"Input CSV: {csv_path}")
        logger.info(f"Output bucket: {bucket_name}")
        logger.info(f"Output filename: {output_filename}")
        logger.info(f"Local output: {local_output_dir}")
        
        # Get GCP credentials
        credentials = get_gcp_credentials()
        
        # Step 1: Read CSV
        logger.info("-" * 60)
        logger.info("STEP 1: Reading CSV")
        df = pd.read_csv(csv_path)
        logger.info(f"✓ Loaded {len(df)} rows")
        
        # Step 2: Validate columns
        logger.info("-" * 60)
        logger.info("STEP 2: Validating columns")
        validate_columns(df, REQUIRED_COLUMNS)
        
        # Step 3: Remove null/empty rows
        logger.info("-" * 60)
        logger.info("STEP 3: Data validation - Removing null/empty rows")
        df = remove_null_empty_rows(df, MANDATORY_OUTPUT_COLUMNS)
        
        if len(df) == 0:
            logger.warning("No rows after removing null/empty values")
            return None
        
        # Step 4: Remove spelling mistakes
        logger.info("-" * 60)
        logger.info("STEP 4: Data validation - Removing spelling mistakes")
        df = remove_spelling_mistakes(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing spelling mistakes")
            return None
        
        # Step 5: Apply business logic filters
        logger.info("-" * 60)
        logger.info("STEP 5: Applying business logic filters")
        filtered_df = filter_data_by_state(df)
        
        if len(filtered_df) == 0:
            logger.warning("No rows matched business logic criteria")
            return None
        
        # Step 6: Construct JSON
        logger.info("-" * 60)
        logger.info("STEP 6: Constructing JSON")
        json_output = construct_json(filtered_df)
        
        # Step 7: Save JSON locally
        logger.info("-" * 60)
        logger.info("STEP 7: Saving JSON locally")
        local_filename = output_filename.split('/')[-1]
        local_filepath = save_json_locally(json_output, local_output_dir, local_filename)
        
        # Step 8: Save CSV locally
        logger.info("-" * 60)
        logger.info("STEP 8: Saving CSV locally")
        csv_filepath = save_csv_locally(filtered_df, local_output_dir, local_filename)
        
        # Step 9: Upload to GCP
        logger.info("-" * 60)
        logger.info("STEP 9: Uploading to GCP")
        
        upload_success = upload_to_gcp(json_output, bucket_name, output_filename, credentials)
        
        if not upload_success:
            logger.info("Trying gsutil fallback...")
            upload_success = upload_to_gcp_gsutil(local_filepath, bucket_name, output_filename)
        
        if not upload_success:
            logger.warning("⚠️  Auto-upload failed. Please upload manually:")
            logger.warning(f"gsutil cp {local_filepath} gs://{bucket_name}/{output_filename}")
        
        logger.info("="*60)
        logger.info("✓ PIPELINE COMPLETED SUCCESSFULLY!")
        logger.info("="*60)
        logger.info(f"Local JSON: {local_filepath}")
        logger.info(f"Local CSV: {csv_filepath}")
        logger.info(f"GCP: gs://{bucket_name}/{output_filename}")
        
        return json_output
        
    except Exception as e:
        logger.error("="*60)
        logger.error("✗ PIPELINE FAILED")
        logger.error("="*60)
        logger.error(f"Error: {str(e)}", exc_info=True)
        raise

def main():
    #local configurations 
    LOCAL_CSV_PATH = config.get("GCP", "ANIMATIONS_LOCAL_CSV_PATH")
    LOCAL_OUTPUT_DIR = config.get("GCP", "LOCAL_OUTPUT_DIR")

    #cloud configurations
    INPUT_BLOB_PATH = config.get("GCP", "INPUT_BLOB_PATH")
    BUCKET_NAME = config.get("GCP", "BUCKET_NAME")  
    OUTPUT_BLOB_NAME = config.get("GCP", "OUTPUT_BLOB_NAME")
    OUTPUT_FILENAME = "challenges_solutions.json"

    credentials = get_gcp_credentials()
    
    CSV_PATH = fetch_the_csv_file_from_gcp(LOCAL_CSV_PATH, BUCKET_NAME, INPUT_BLOB_PATH, credentials)
      
    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        result = process_csv(CSV_PATH, BUCKET_NAME, OUTPUT_FILENAME, LOCAL_OUTPUT_DIR)
        
        if result:
            print('Total animations in output JSON:', len(result["data"]))
        
        full_blob_name = f"{OUTPUT_BLOB_NAME}/{OUTPUT_FILENAME}"
        
        local_json_path = os.path.join(LOCAL_OUTPUT_DIR, OUTPUT_FILENAME)

        upload_success = upload_to_gcp(result, BUCKET_NAME, full_blob_name, credentials)

        if not upload_success:
            logger.info("Trying gsutil fallback...")
            upload_success = upload_to_gcp_gsutil(local_json_path, BUCKET_NAME, full_blob_name)

        logger.info("Application finished successfully")
        
    except Exception as e:
        logger.critical(f"Application terminated: {str(e)}")
        raise

if __name__ == "__main__":
    main()