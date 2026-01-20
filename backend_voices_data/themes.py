import pandas as pd
import os
import re
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from .utils import (
    get_env_var, get_gcp_credentials, setup_logger, 
    fetch_csv_from_gcp, upload_to_gcp, upload_to_gcp_gsutil,
    save_json_locally, save_csv_locally
)

# Load environment variables
load_dotenv()

# Get configuration from environment
LOG_DIR = os.getenv("LOG_DIR")

# Required columns
REQUIRED_COLUMNS = ['discussion_id', 'challenge', 'theme_name', 'add_to_frontend', 
                   'theme_id', 'pii_flag', 'role', 'district', 'state', 'confidence_score', 
                   'justification']
MANDATORY_OUTPUT_COLUMNS = ['challenge', 'theme_name', 'theme_id', 'pii_flag', 'role', 'district', 'state']

# Setup Logger
logger = setup_logger(LOG_DIR, 'themes.log', 'ThemesProcessor')

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
          - Distributed proportionally across all available states
          - If a state doesn't have enough, redistribute to other states
          - Mixed districts within each state
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
        
        # Get unique states from data
        unique_states = filtered_null['state'].str.strip().unique()
        num_states = len(unique_states)
        
        if num_states == 0:
            logger.warning("No states found in data")
            return pd.DataFrame()
        
        # Calculate samples per state (distribute 20 samples)
        samples_per_state = 20 // num_states
        remaining_samples = 20 % num_states
        
        logger.info(f"Found {num_states} unique states: {', '.join(unique_states)}")
        logger.info(f"Sampling strategy: {samples_per_state} per state (+ {remaining_samples} additional)")
        
        # For EACH theme, select up to 20 rows distributed across states
        selected_rows_list = []
        grouped = filtered_null.groupby('theme_name')
        
        for theme_name, group in grouped:
            theme_selected = []
            total_needed = 20
            total_collected = 0
            
            # First pass: Collect samples from each state (equal distribution)
            state_samples = {}
            for idx, state in enumerate(unique_states):
                state_data = group[group['state'].str.strip() == state]
                
                # Calculate sample size for this state
                base_samples = samples_per_state
                if idx < remaining_samples:
                    base_samples += 1
                
                state_sample_size = min(base_samples, len(state_data))
                
                if state_sample_size > 0:
                    state_sample = state_data.sample(n=state_sample_size, random_state=None)
                    state_samples[state] = {
                        'sampled': state_sample,
                        'remaining': state_data.drop(state_sample.index),
                        'requested': base_samples,
                        'got': state_sample_size
                    }
                    total_collected += state_sample_size
                    logger.info(f"  Theme '{theme_name}' - {state}: Collected {state_sample_size}/{len(state_data)} rows (requested {base_samples})")
                else:
                    logger.warning(f"  Theme '{theme_name}' - {state}: No data available")
            
            # Second pass: Redistribute unfilled quota to states with remaining data
            shortfall = total_needed - total_collected
            
            if shortfall > 0:
                logger.info(f"  Theme '{theme_name}': Shortfall of {shortfall} samples, redistributing...")
                
                # Find states that have remaining data
                states_with_extra = [(state, info) for state, info in state_samples.items() 
                                     if len(info['remaining']) > 0]
                
                if states_with_extra:
                    # Distribute shortfall proportionally
                    extra_per_state = shortfall // len(states_with_extra)
                    extra_remainder = shortfall % len(states_with_extra)
                    
                    for idx, (state, info) in enumerate(states_with_extra):
                        extra_needed = extra_per_state
                        if idx < extra_remainder:
                            extra_needed += 1
                        
                        extra_available = min(extra_needed, len(info['remaining']))
                        
                        if extra_available > 0:
                            extra_sample = info['remaining'].sample(n=extra_available, random_state=None)
                            # Append to existing sample
                            state_samples[state]['sampled'] = pd.concat([state_samples[state]['sampled'], extra_sample])
                            state_samples[state]['remaining'] = state_samples[state]['remaining'].drop(extra_sample.index)
                            total_collected += extra_available
                            logger.info(f"  Theme '{theme_name}' - {state}: Added {extra_available} extra samples (total now: {len(state_samples[state]['sampled'])})")
            
            # Collect all samples for this theme
            for state, info in state_samples.items():
                theme_selected.append(info['sampled'])
                logger.info(f"  Theme '{theme_name}' - {state}: Final count {len(info['sampled'])} rows from {info['sampled']['district'].nunique()} districts")
            
            # Combine all states for this theme
            if theme_selected:
                theme_combined = pd.concat(theme_selected, ignore_index=True)
                selected_rows_list.append(theme_combined)
                logger.info(f"  Theme '{theme_name}': Total selected {len(theme_combined)} rows")
        
        if not selected_rows_list:
            logger.warning("No rows selected across all themes")
            return pd.DataFrame()
        
        result_df = pd.concat(selected_rows_list, ignore_index=True)
        logger.info(f"✓ Selected total of {len(result_df)} rows across all themes")
        return result_df

def clean_text(text):
    """Clean text by removing extra spaces and special characters"""
    if pd.isna(text) or text == '':
        return text
    return re.sub(r'\s+', ' ', str(text).strip())

def construct_json(df, theme_counts=None):
    logger.info("Constructing JSON...")
    
    # Group by theme_id and theme_name
    grouped = df.groupby(['theme_id', 'theme_name'])
    
    data_list = []
    
    for (theme_id, theme_name), group in grouped:
        # Check if voices_raised column exists (pre-processed data)
        if 'voices_raised' in group.columns:
            # Use voices_raised value from the dataframe
            original_count = group['voices_raised'].iloc[0]
            logger.debug(f"  Using voices_raised from CSV for theme '{theme_name}'")
        else:
            # Use theme_counts dict (newly processed data)
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
            "value": str(original_count),  # Using original count
            "list": challenge_list
        }
        
        data_list.append(theme_obj)
        # Sort themes by value (count) in descending order
        data_list = sorted(data_list, key=lambda x: int(x["value"]), reverse=True)
        logger.debug(f"  Theme '{theme_name}' (ID: {theme_id}): {original_count} total challenges, {len(group)} in filtered list")
    
    json_output = {
        "identification": "themes_emerged",
        "data": data_list
    }
    
    logger.info(f"✓ JSON constructed with {len(data_list)} themes")
    return json_output

def remove_spelling_mistakes(df):
    """
    Remove rows with gibberish/invalid text patterns
    Fast pattern-based detection (~0.001s per row)
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
    
    filtered_df = df[~df['challenge'].apply(is_low_quality_text)].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} rows with low-quality text. Remaining: {len(filtered_df)}")
    return filtered_df

def remove_semantic_duplicates(df, similarity_threshold=0.85):
    """
    Remove semantically similar challenges within each theme
    similarity_threshold: 0-1, higher means more strict (0.85 means 85% similar)
    """
    logger.info(f"Removing semantically similar challenges (threshold: {similarity_threshold})...")
    initial_count = len(df)
    
    # Load sentence transformer model
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    filtered_rows = []
    
    # Process each theme separately
    grouped = df.groupby('theme_name')
    
    for theme_name, group in grouped:
        challenges = group['challenge'].tolist()
        
        if len(challenges) <= 1:
            filtered_rows.append(group)
            continue
        
        # Get embeddings for all challenges in this theme
        embeddings = model.encode(challenges)
        
        # Calculate pairwise cosine similarity
        similarities = cosine_similarity(embeddings)
        
        # Keep track of which rows to keep
        keep_indices = []
        
        for i in range(len(challenges)):
            # Check if current challenge is similar to any already kept challenge
            is_duplicate = False
            for kept_idx in keep_indices:
                if similarities[i][kept_idx] > similarity_threshold:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                keep_indices.append(i)
        
        # Keep only non-duplicate rows
        filtered_group = group.iloc[keep_indices]
        filtered_rows.append(filtered_group)
        
        removed = len(group) - len(filtered_group)
        if removed > 0:
            logger.info(f"  Theme '{theme_name}': Removed {removed} semantic duplicates, kept {len(filtered_group)}")
    
    result_df = pd.concat(filtered_rows, ignore_index=True)
    removed_count = initial_count - len(result_df)
    logger.info(f"✓ Removed {removed_count} semantically similar challenges. Remaining: {len(result_df)}")
    return result_df

def process_csv(csv_path, bucket_name, output_filename, local_output_dir):
    try:
        logger.info("="*60)
        logger.info("Starting Themes Emerged CSV processing pipeline")
        logger.info("="*60)
        logger.info(f"Input CSV: {csv_path}")
        logger.info(f"Output bucket: {bucket_name}")
        logger.info(f"Output filename: {output_filename}")
        logger.info(f"Local output: {local_output_dir}")
        
        # Get GCP credentials
        logger.info("Loading GCP credentials from environment variables...")
        credentials = get_gcp_credentials()
        logger.info("✓ GCP credentials loaded successfully")
        
        # Step 1: Read CSV
        logger.info("-" * 60)
        logger.info("STEP 1: Reading CSV")
        df = pd.read_csv(csv_path)
        logger.info(f"✓ Loaded {len(df)} rows")

        # Step 2: Validate columns
        logger.info("-" * 60)
        logger.info("STEP 2: Validating columns")
        validate_columns(df, REQUIRED_COLUMNS)
        
        # Check if data is already pre-processed
        is_preprocessed = 'voices_raised' in df.columns
        
        if is_preprocessed:
            logger.info("="*60)
            logger.info("⚡ PRE-PROCESSED DATA DETECTED")
            logger.info("Skipping validation steps (3-5.5) - Data already validated")
            logger.info("="*60)
            theme_counts = None  # Not needed for pre-processed data
        else:
            # Step 2.5: Calculate original theme counts (BEFORE any filtering)
            logger.info("-" * 60)
            logger.info("STEP 2.5: Calculating original theme counts")
            theme_counts = calculate_theme_counts(df)
            
            # Step 3: Remove null/empty rows
            logger.info("-" * 60)
            logger.info("STEP 3: Data validation - Removing null/empty rows")
            df = remove_null_empty_rows(df, MANDATORY_OUTPUT_COLUMNS)
            
            if len(df) == 0:
                logger.warning("No rows after removing null/empty values")
                return None
            
            # Step 4: Remove PII flagged rows
            logger.info("-" * 60)
            logger.info("STEP 4: Data validation - Removing PII flagged rows")
            df = remove_pii_rows(df)
            
            if len(df) == 0:
                logger.warning("No rows after removing PII flagged data")
                return None
            
            # Step 5: Remove Unknown/Unclear themes
            logger.info("-" * 60)
            logger.info("STEP 5: Data validation - Removing Unknown/Unclear themes")
            df = remove_unknown_themes(df)
            
            if len(df) == 0:
                logger.warning("No rows after removing Unknown/Unclear themes")
                return None
            
            # Step 5.5: Remove rows with spelling mistakes
            logger.info("-" * 60)
            logger.info("STEP 5.5: Data validation - Removing spelling mistakes")
            df = remove_spelling_mistakes(df)
            
            if len(df) == 0:
                logger.warning("No rows after removing spelling mistakes")
                return None
            
            # Step 5.6: Remove semantic duplicates
            logger.info("-" * 60)
            logger.info("STEP 5.6: Data validation - Removing semantic duplicates")
            df = remove_semantic_duplicates(df, similarity_threshold=0.85)
            
            if len(df) == 0:
                logger.warning("No rows after removing semantic duplicates")
                return None
        
        # Step 6: Apply business logic filters
        logger.info("-" * 60)
        logger.info("STEP 6: Applying business logic filters")
        filtered_df = filter_data_by_theme(df)
        
        if len(filtered_df) == 0:
            logger.warning("No rows matched business logic criteria")
            return None
        
        # Add voices_raised column only if not already present
        if 'voices_raised' not in filtered_df.columns and theme_counts is not None:
            filtered_df['voices_raised'] = filtered_df.apply(
                lambda row: theme_counts.get((row['theme_id'], row['theme_name']), 0), 
                axis=1
            )
            
            # Reorder columns to place voices_raised after theme_name
            cols = list(filtered_df.columns)
            theme_name_index = cols.index('theme_name')
            cols.insert(theme_name_index + 1, cols.pop(cols.index('voices_raised')))
            filtered_df = filtered_df[cols]
        
        # Step 7: Construct JSON
        logger.info("-" * 60)
        logger.info("STEP 7: Constructing JSON")
        json_output = construct_json(filtered_df, theme_counts)
        
        # Step 8: Save locally
        logger.info("-" * 60)
        logger.info("STEP 8: Saving locally")
        local_filename = output_filename.split('/')[-1]
        local_filepath = save_json_locally(json_output, local_output_dir, local_filename, logger)

        # Step 8.5: Save CSV
        logger.info("-" * 60)
        logger.info("STEP 8.5: Saving CSV")
        csv_filepath = save_csv_locally(filtered_df, local_output_dir, local_filename, logger)
        
        # Step 9: Upload to GCP
        logger.info("-" * 60)
        logger.info("STEP 9: Uploading to GCP")
        
        upload_success = upload_to_gcp(json_output, bucket_name, output_filename, credentials, logger)
        
        if not upload_success:
            logger.info("Trying gsutil fallback...")
            upload_success = upload_to_gcp_gsutil(local_filepath, bucket_name, output_filename, logger)
        
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
    # Get configuration from environment
    LOCAL_CSV_PATH = os.getenv("THEMES_LOCAL_CSV_PATH")
    LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR")
    INPUT_BLOB_PATH = os.getenv("INPUT_BLOB_PATH")
    BUCKET_NAME = os.getenv("BUCKET_NAME")  
    OUTPUT_FILENAME = "sg-dashboard/themes_emerged.json"

    credentials = get_gcp_credentials()
    
    CSV_PATH = fetch_csv_from_gcp(
        LOCAL_CSV_PATH, 
        BUCKET_NAME, 
        INPUT_BLOB_PATH, 
        credentials, 
        "themes_emerged.csv",
        logger
    )
      
    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        result = process_csv(CSV_PATH, BUCKET_NAME, OUTPUT_FILENAME, LOCAL_OUTPUT_DIR)
        
        if result:
            print('Total themes in output JSON:', len(result["data"]))
        
        logger.info("Application finished successfully")
        
    except Exception as e:
        logger.critical(f"Application terminated: {str(e)}")
        raise

if __name__ == "__main__":
    main()




# import pandas as pd
# import json
# from datetime import datetime
# import logging
# import os
# import configparser
# import re
# import subprocess
# from dotenv import load_dotenv
# from google.cloud import storage
# from google.oauth2 import service_account
# from sentence_transformers import SentenceTransformer
# from sklearn.metrics.pairwise import cosine_similarity
# import numpy as np

# # Load environment variables from .env file
# load_dotenv()

# # Load configuration from config.ini
# current_dir = os.path.dirname(os.path.abspath(__file__))
# config_path = os.path.join(current_dir, '..', 'config.ini')

# # Load configuration from config.ini
# config = configparser.RawConfigParser()
# config.read(config_path)
# LOG_DIR = config.get("LOGS", "log_dir")
# LOG_FILENAME_PREFIX = config.get("LOGS", "themes_file")

# # Required columns
# REQUIRED_COLUMNS = ['discussion_id', 'challenge', 'theme_name', 'add_to_frontend', 
#                    'theme_id', 'pii_flag', 'role', 'district', 'state', 'confidence_score', 
#                    'justification']
# MANDATORY_OUTPUT_COLUMNS = ['challenge', 'theme_name', 'theme_id', 'pii_flag', 'role', 'district', 'state']

# # Setup Logger
# def setup_logger():
#     os.makedirs(LOG_DIR, exist_ok=True)
#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     log_filepath = os.path.join(LOG_DIR, f"{LOG_FILENAME_PREFIX}_{timestamp}.log")
    
#     logger = logging.getLogger('ThemesProcessor')
#     logger.setLevel(logging.DEBUG)
#     logger.handlers = []
    
#     formatter = logging.Formatter(
#         '%(asctime)s - %(levelname)s - %(message)s',
#         datefmt='%Y-%m-%d %H:%M:%S'
#     )
    
#     file_handler = logging.FileHandler(log_filepath)
#     file_handler.setLevel(logging.DEBUG)
#     file_handler.setFormatter(formatter)
#     logger.addHandler(file_handler)
    
#     console_handler = logging.StreamHandler()
#     console_handler.setLevel(logging.INFO)
#     console_handler.setFormatter(formatter)
#     logger.addHandler(console_handler)
    
#     logger.info(f"Logger initialized. Log file: {log_filepath}")
#     return logger

# logger = setup_logger()

# def get_env_var(name: str) -> str:
#     value = os.getenv(name)
#     if value is None:
#         raise EnvironmentError(f"Missing required environment variable: {name}")
#     return value

# def get_gcp_credentials():
#     """Create GCP credentials from environment variables"""
#     logger.info("Loading GCP credentials from environment variables...")
    
#     try:
#         credentials_dict = {
#             "type": get_env_var("TYPE"),
#             "project_id": get_env_var("PROJECT_ID"),
#             "private_key_id": get_env_var("PRIVATE_KEY_ID"),
#             "private_key": get_env_var("PRIVATE_KEY").replace('\\n', '\n'),
#             "client_email": get_env_var("CLIENT_EMAIL"),
#             "client_id": get_env_var("CLIENT_ID"),
#             "auth_uri": get_env_var("AUTH_URI"),
#             "token_uri": get_env_var("TOKEN_URI"),
#             "auth_provider_x509_cert_url": get_env_var("AUTH_PROVIDER_X509_CERT_URL"),
#             "client_x509_cert_url": get_env_var("CLIENT_X509_CERT_URL"),
#             "universe_domain": get_env_var("UNIVERSE_DOMAIN")
#         }
        
#         credentials = service_account.Credentials.from_service_account_info(credentials_dict)
#         logger.info("✓ GCP credentials loaded successfully")
#         return credentials
        
#     except Exception as e:
#         logger.error(f"Failed to load GCP credentials: {str(e)}")
#         raise

# def validate_columns(df, required_cols):
#     logger.info("Validating DataFrame columns...")
#     missing = set(required_cols) - set(df.columns)
#     if missing:
#         logger.error(f"Missing columns: {missing}")
#         raise ValueError(f"Missing required columns: {missing}")
#     logger.info("✓ All required columns present")
#     return True

# def calculate_theme_counts(df):
#     """
#     Calculate total challenge count per theme from original data
#     This should be called after column validation but before any filtering
#     Returns: dict with (theme_id, theme_name) as key and count as value
#     """
#     logger.info("Calculating total challenge counts per theme...")
    
#     # Group by theme_id and theme_name to count challenges
#     theme_counts = df.groupby(['theme_id', 'theme_name']).size().to_dict()
    
#     logger.info(f"✓ Calculated counts for {len(theme_counts)} themes")
#     for (theme_id, theme_name), count in theme_counts.items():
#         logger.debug(f"  Theme '{theme_name}' (ID: {theme_id}): {count} total challenges")
    
#     return theme_counts

# def remove_null_empty_rows(df, columns_to_check):
#     logger.info("Removing rows with null/empty mandatory columns...")
#     initial_count = len(df)
    
#     mask = pd.Series([True] * len(df))
#     for col in columns_to_check:
#         if col in df.columns:
#             mask &= df[col].notna()
#             mask &= df[col].astype(str).str.strip() != ''
#             mask &= df[col].astype(str).str.strip() != 'nan'
    
#     filtered_df = df[mask].copy()
#     logger.info(f"✓ Removed {initial_count - len(filtered_df)} rows. Remaining: {len(filtered_df)}")
#     return filtered_df

# def remove_pii_rows(df):
#     logger.info("Filtering out rows with pii_flag = True...")
#     initial_count = len(df)
    
#     filtered_df = df[
#         ~(
#             (df['pii_flag'] == True) | 
#             (df['pii_flag'] == 'TRUE') | 
#             (df['pii_flag'] == 'true') | 
#             (df['pii_flag'] == 1) |
#             (df['pii_flag'] == '1')
#         )
#     ].copy()
    
#     removed_count = initial_count - len(filtered_df)
#     logger.info(f"✓ Removed {removed_count} rows with PII flag. Remaining: {len(filtered_df)}")
#     return filtered_df

# def remove_unknown_themes(df):
#     logger.info("Removing rows with theme_name = Unknown/Unclear...")
#     initial_count = len(df)
    
#     filtered_df = df[
#         ~(
#             (df['theme_name'].str.strip().str.lower() == 'unknown') |
#             (df['theme_name'].str.strip().str.lower() == 'unclear') |
#             (df['theme_name'].str.strip().str.lower() == 'unknown/unclear')
#         )
#     ].copy()
    
#     removed_count = initial_count - len(filtered_df)
#     logger.info(f"✓ Removed {removed_count} rows with Unknown/Unclear themes. Remaining: {len(filtered_df)}")
#     return filtered_df

# def filter_data_by_theme(df):
#     """
#     LOGIC:
#     - Check if ANY row has add_to_frontend == True
#     - If YES: Return ONLY those rows
#     - If NO: For EACH theme, select up to 20 rows with filters
#           - Distributed proportionally across all available states
#           - If a state doesn't have enough, redistribute to other states
#           - Mixed districts within each state
#     """
#     logger.info("Applying business logic filters...")
#     logger.debug(f"Total rows: {len(df)}")
    
#     # Check for rows with add_to_frontend == True
#     true_rows = df[
#         (df['add_to_frontend'] == True) | 
#         (df['add_to_frontend'] == 'TRUE') |
#         (df['add_to_frontend'] == 'true') |
#         (df['add_to_frontend'] == 1)
#     ]
    
#     if len(true_rows) > 0:
#         # Return ONLY True rows
#         logger.info(f"✓ Found {len(true_rows)} rows with add_to_frontend=True")
#         logger.info("Returning ONLY rows where add_to_frontend=True")
#         return true_rows.copy()
    
#     else:
#         # Process null rows per theme with filters
#         logger.info("No rows with add_to_frontend=True found")
        
#         df_null = df[df['add_to_frontend'].isna()]
#         logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
#         if len(df_null) == 0:
#             logger.warning("No rows with null add_to_frontend to process")
#             return pd.DataFrame()
        
#         # Apply filters: confidence >= 0.9 and challenge length > 30
#         filtered_null = df_null[
#             (df_null['confidence_score'] >= 0.9) &
#             (df_null['challenge'].str.len() > 30)
#         ]
#         logger.debug(f"Eligible rows (confidence>=0.9, challenge length>30): {len(filtered_null)}")
        
#         if len(filtered_null) == 0:
#             logger.warning("No rows meet the criteria (confidence>=0.9, challenge length>30)")
#             return pd.DataFrame()
        
#         # Get unique states from data
#         unique_states = filtered_null['state'].str.strip().unique()
#         num_states = len(unique_states)
        
#         if num_states == 0:
#             logger.warning("No states found in data")
#             return pd.DataFrame()
        
#         # Calculate samples per state (distribute 20 samples)
#         samples_per_state = 20 // num_states
#         remaining_samples = 20 % num_states
        
#         logger.info(f"Found {num_states} unique states: {', '.join(unique_states)}")
#         logger.info(f"Sampling strategy: {samples_per_state} per state (+ {remaining_samples} additional)")
        
#         # For EACH theme, select up to 20 rows distributed across states
#         selected_rows_list = []
#         grouped = filtered_null.groupby('theme_name')
        
#         for theme_name, group in grouped:
#             theme_selected = []
#             total_needed = 20
#             total_collected = 0
            
#             # First pass: Collect samples from each state (equal distribution)
#             state_samples = {}
#             for idx, state in enumerate(unique_states):
#                 state_data = group[group['state'].str.strip() == state]
                
#                 # Calculate sample size for this state
#                 base_samples = samples_per_state
#                 if idx < remaining_samples:
#                     base_samples += 1
                
#                 state_sample_size = min(base_samples, len(state_data))
                
#                 if state_sample_size > 0:
#                     state_sample = state_data.sample(n=state_sample_size, random_state=None)
#                     state_samples[state] = {
#                         'sampled': state_sample,
#                         'remaining': state_data.drop(state_sample.index),
#                         'requested': base_samples,
#                         'got': state_sample_size
#                     }
#                     total_collected += state_sample_size
#                     logger.info(f"  Theme '{theme_name}' - {state}: Collected {state_sample_size}/{len(state_data)} rows (requested {base_samples})")
#                 else:
#                     logger.warning(f"  Theme '{theme_name}' - {state}: No data available")
            
#             # Second pass: Redistribute unfilled quota to states with remaining data
#             shortfall = total_needed - total_collected
            
#             if shortfall > 0:
#                 logger.info(f"  Theme '{theme_name}': Shortfall of {shortfall} samples, redistributing...")
                
#                 # Find states that have remaining data
#                 states_with_extra = [(state, info) for state, info in state_samples.items() 
#                                      if len(info['remaining']) > 0]
                
#                 if states_with_extra:
#                     # Distribute shortfall proportionally
#                     extra_per_state = shortfall // len(states_with_extra)
#                     extra_remainder = shortfall % len(states_with_extra)
                    
#                     for idx, (state, info) in enumerate(states_with_extra):
#                         extra_needed = extra_per_state
#                         if idx < extra_remainder:
#                             extra_needed += 1
                        
#                         extra_available = min(extra_needed, len(info['remaining']))
                        
#                         if extra_available > 0:
#                             extra_sample = info['remaining'].sample(n=extra_available, random_state=None)
#                             # Append to existing sample
#                             state_samples[state]['sampled'] = pd.concat([state_samples[state]['sampled'], extra_sample])
#                             state_samples[state]['remaining'] = state_samples[state]['remaining'].drop(extra_sample.index)
#                             total_collected += extra_available
#                             logger.info(f"  Theme '{theme_name}' - {state}: Added {extra_available} extra samples (total now: {len(state_samples[state]['sampled'])})")
            
#             # Collect all samples for this theme
#             for state, info in state_samples.items():
#                 theme_selected.append(info['sampled'])
#                 logger.info(f"  Theme '{theme_name}' - {state}: Final count {len(info['sampled'])} rows from {info['sampled']['district'].nunique()} districts")
            
#             # Combine all states for this theme
#             if theme_selected:
#                 theme_combined = pd.concat(theme_selected, ignore_index=True)
#                 selected_rows_list.append(theme_combined)
#                 logger.info(f"  Theme '{theme_name}': Total selected {len(theme_combined)} rows")
        
#         if not selected_rows_list:
#             logger.warning("No rows selected across all themes")
#             return pd.DataFrame()
        
#         result_df = pd.concat(selected_rows_list, ignore_index=True)
#         logger.info(f"✓ Selected total of {len(result_df)} rows across all themes")
#         return result_df

# def clean_text(text):
#     """Clean text by removing extra spaces and special characters"""
#     if pd.isna(text) or text == '':
#         return text
#     return re.sub(r'\s+', ' ', str(text).strip())

# def construct_json(df, theme_counts=None):
#     logger.info("Constructing JSON...")
    
#     # Group by theme_id and theme_name
#     grouped = df.groupby(['theme_id', 'theme_name'])
    
#     data_list = []
    
#     for (theme_id, theme_name), group in grouped:
#         # Check if voices_raised column exists (pre-processed data)
#         if 'voices_raised' in group.columns:
#             # Use voices_raised value from the dataframe
#             original_count = group['voices_raised'].iloc[0]
#             logger.debug(f"  Using voices_raised from CSV for theme '{theme_name}'")
#         else:
#             # Use theme_counts dict (newly processed data)
#             original_count = theme_counts.get((theme_id, theme_name), len(group))
        
#         # Build list of challenges (only filtered ones)
#         challenge_list = []
#         for idx, row in group.iterrows():
#             challenge_obj = {
#                 "description": clean_text(row['challenge']),
#                 "voice_by": clean_text(row['role']),
#                 "district": clean_text(row['district']),
#                 "state": clean_text(row['state'])
#             }
#             challenge_list.append(challenge_obj)
        
#         # Build theme object
#         theme_obj = {
#             "id": str(theme_id),
#             "label": clean_text(theme_name),
#             "value": str(original_count),  # Using original count
#             "list": challenge_list
#         }
        
#         data_list.append(theme_obj)
#         logger.debug(f"  Theme '{theme_name}' (ID: {theme_id}): {original_count} total challenges, {len(group)} in filtered list")
    
#     json_output = {
#         "identification": "themes_emerged",
#         "data": data_list
#     }
    
#     logger.info(f"✓ JSON constructed with {len(data_list)} themes")
#     return json_output

# def save_json_locally(json_data, output_dir, filename):
#     logger.info("Saving JSON locally...")
#     os.makedirs(output_dir, exist_ok=True)
#     filepath = os.path.join(output_dir, filename)
    
#     with open(filepath, 'w', encoding='utf-8') as f:
#         json.dump(json_data, f, indent=2, ensure_ascii=False)
    
#     file_size = os.path.getsize(filepath)
#     logger.info(f"✓ Saved to: {filepath} ({file_size/1024:.2f} KB)")
#     return filepath

# def upload_to_gcp(json_data, bucket_name, blob_name, credentials):
#     """Upload JSON to GCP bucket using credentials from .env"""
#     logger.info(f"Uploading to GCP bucket: {bucket_name}/{blob_name}")
    
#     try:
#         storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
#         bucket = storage_client.bucket(bucket_name)
#         blob = bucket.blob(blob_name)
        
#         json_string = json.dumps(json_data, indent=2)
#         blob.upload_from_string(json_string, content_type='application/json')
        
#         logger.info(f"✓ Successfully uploaded to gs://{bucket_name}/{blob_name}")
#         return True
        
#     except Exception as e:
#         logger.error(f"Failed to upload to GCP: {str(e)}", exc_info=True)
#         logger.info("Falling back to gsutil method...")
#         return False

# def upload_to_gcp_gsutil(local_filepath, bucket_name, blob_name):
#     """Fallback: Upload using gsutil"""
#     logger.info(f"Uploading to GCP using gsutil...")
    
#     try:
#         gs_path = f"gs://{bucket_name}/{blob_name}"
#         result = subprocess.run(
#             ['gsutil', 'cp', local_filepath, gs_path],
#             capture_output=True,
#             text=True,
#             check=False
#         )
        
#         if result.returncode == 0:
#             logger.info(f"✓ Successfully uploaded to {gs_path}")
#             return True
#         else:
#             logger.error(f"✗ gsutil upload failed: {result.stderr}")
#             return False
        
#     except Exception as e:
#         logger.error(f"✗ Failed to upload: {str(e)}")
#         return False
    
# def remove_spelling_mistakes(df):
#     """
#     Remove rows with gibberish/invalid text patterns
#     Fast pattern-based detection (~0.001s per row)
#     """
#     logger.info("Filtering out rows with gibberish/invalid text...")
#     initial_count = len(df)
    
#     def is_low_quality_text(text):
#         if pd.isna(text) or text == '':
#             return False
        
#         text = str(text).lower()
        
#         # 1. Check for keyboard spam
#         if re.search(r'(asdf|qwer|zxcv|hjkl|lkjh){2,}', text):
#             return True
        
#         # 2. Check for excessive special characters (>30% of text)
#         special_chars = len(re.findall(r'[^a-zA-Z0-9\s]', text))
#         if len(text) > 0 and (special_chars / len(text)) > 0.3:
#             return True
        
#         # 3. Check for repeating characters (>4 times)
#         if re.search(r'(.)\1{4,}', text):
#             return True
        
#         # 4. Check for words with no vowels (excluding common abbreviations)
#         words = text.split()
#         no_vowel_words = []
        
#         for word in words:
#             clean_word = re.sub(r'[^a-z]', '', word.lower())
            
#             # Skip short words and common abbreviations
#             if len(clean_word) <= 3:
#                 continue
            
#             # Check if word has no vowels
#             if not re.search(r'[aeiou]', clean_word):
#                 no_vowel_words.append(word)
        
#         # Flag if >30% of words have no vowels
#         if len(words) > 3 and len(no_vowel_words) / len(words) > 0.3:
#             return True
        
#         # 5. Check for excessive consonant clusters (5+ consonants in a row)
#         excessive_consonants = len(re.findall(r'[^aeiou\s]{5,}', text))
#         if excessive_consonants > 2:
#             return True
        
#         # 6. Check for very low vowel ratio (<15%)
#         letters = re.sub(r'[^a-z]', '', text)
#         if len(letters) > 0:
#             vowel_count = len(re.findall(r'[aeiou]', letters))
#             vowel_ratio = vowel_count / len(letters)
#             if vowel_ratio < 0.15:
#                 return True
        
#         return False
    
#     filtered_df = df[~df['challenge'].apply(is_low_quality_text)].copy()
    
#     removed_count = initial_count - len(filtered_df)
#     logger.info(f"✓ Removed {removed_count} rows with low-quality text. Remaining: {len(filtered_df)}")
#     return filtered_df

# def save_csv_locally(df, output_dir, filename):
#     logger.info("Saving CSV locally...")
#     os.makedirs(output_dir, exist_ok=True)
#     csv_filename = "filtered_" + filename.replace('.json', '.csv')
#     filepath = os.path.join(output_dir, csv_filename)
    
#     df.to_csv(filepath, index=False, encoding='utf-8')
    
#     file_size = os.path.getsize(filepath)
#     logger.info(f"✓ Saved to: {filepath} ({file_size/1024:.2f} KB)")
#     return filepath

# def remove_semantic_duplicates(df, similarity_threshold=0.85):
#     """
#     Remove semantically similar challenges within each theme
#     similarity_threshold: 0-1, higher means more strict (0.85 means 85% similar)
#     """
#     logger.info(f"Removing semantically similar challenges (threshold: {similarity_threshold})...")
#     initial_count = len(df)
    
#     # Load sentence transformer model
#     model = SentenceTransformer('all-MiniLM-L6-v2')
    
#     filtered_rows = []
    
#     # Process each theme separately
#     grouped = df.groupby('theme_name')
    
#     for theme_name, group in grouped:
#         challenges = group['challenge'].tolist()
        
#         if len(challenges) <= 1:
#             filtered_rows.append(group)
#             continue
        
#         # Get embeddings for all challenges in this theme
#         embeddings = model.encode(challenges)
        
#         # Calculate pairwise cosine similarity
#         similarities = cosine_similarity(embeddings)
        
#         # Keep track of which rows to keep
#         keep_indices = []
        
#         for i in range(len(challenges)):
#             # Check if current challenge is similar to any already kept challenge
#             is_duplicate = False
#             for kept_idx in keep_indices:
#                 if similarities[i][kept_idx] > similarity_threshold:
#                     is_duplicate = True
#                     # logger.debug(f"  Removing duplicate in '{theme_name}': '{challenges[i][:50]}...' (similar to '{challenges[kept_idx][:50]}...')")
#                     break
            
#             if not is_duplicate:
#                 keep_indices.append(i)
        
#         # Keep only non-duplicate rows
#         filtered_group = group.iloc[keep_indices]
#         filtered_rows.append(filtered_group)
        
#         removed = len(group) - len(filtered_group)
#         if removed > 0:
#             logger.info(f"  Theme '{theme_name}': Removed {removed} semantic duplicates, kept {len(filtered_group)}")
    
#     result_df = pd.concat(filtered_rows, ignore_index=True)
#     removed_count = initial_count - len(result_df)
#     logger.info(f"✓ Removed {removed_count} semantically similar challenges. Remaining: {len(result_df)}")
#     return result_df

# def fetch_the_csv_file_from_gcp(local_csv_file_path, bucket_name, blob_path, credentials):
#     """
#     Fetch CSV file from GCP bucket inside a given blob path whose
#     filename starts with 'themes_emerged'
#     """
#     file_prefix = "themes_emerged.csv"
#     logger.info(
#         f"Searching for '{file_prefix}' inside "
#         f"gs://{bucket_name}/{blob_path}/"
#     )

#     try:
#         storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
#         bucket = storage_client.bucket(bucket_name)

#         if blob_path.startswith(f"{bucket_name}/"):
#             blob_path = blob_path[len(bucket_name)+1:]

#         blob_path = blob_path.rstrip("/") + "/"
#         logger.info(f"Actual blob path prefix: {blob_path}")

#         blobs = list(bucket.list_blobs(prefix=blob_path)) # Convert to list to reuse/debug
#         if not blobs:
#             logger.warning(f"No blobs found at all in path: {blob_path}")
#             return None

#         matching_blobs = [
#             blob for blob in blobs
#             if os.path.basename(blob.name).startswith(file_prefix)
#         ]

#         if not matching_blobs:
#             logger.warning(
#                 f"File not found: No file starting with "
#                 f"'{file_prefix}' in {blob_path}"
#             )
#             logger.info("Available files in this path:")
#             for b in blobs:
#                 logger.info(f" - {b.name}")
#             return None

#         latest_blob = max(matching_blobs, key=lambda b: b.updated)

#         latest_blob.download_to_filename(local_csv_file_path)

#         logger.info(f"✓ Successfully fetched: {latest_blob.name}")

#         return local_csv_file_path

#     except Exception as e:
#         logger.error(f"Failed to fetch from GCP: {str(e)}", exc_info=True)
#         return None



# def process_csv(csv_path, bucket_name, output_filename, local_output_dir):
#     try:
#         logger.info("="*60)
#         logger.info("Starting Themes Emerged CSV processing pipeline")
#         logger.info("="*60)
#         logger.info(f"Input CSV: {csv_path}")
#         logger.info(f"Output bucket: {bucket_name}")
#         logger.info(f"Output filename: {output_filename}")
#         logger.info(f"Local output: {local_output_dir}")
        
#         # Get GCP credentials
#         credentials = get_gcp_credentials()
        
#         # Step 1: Read CSV
#         logger.info("-" * 60)
#         logger.info("STEP 1: Reading CSV")
#         df = pd.read_csv(csv_path)
#         logger.info(f"✓ Loaded {len(df)} rows")

#         # Step 2: Validate columns
#         logger.info("-" * 60)
#         logger.info("STEP 2: Validating columns")
#         validate_columns(df, REQUIRED_COLUMNS)
        
#         # Check if data is already pre-processed
#         is_preprocessed = 'voices_raised' in df.columns
        
#         if is_preprocessed:
#             logger.info("="*60)
#             logger.info("⚡ PRE-PROCESSED DATA DETECTED")
#             logger.info("Skipping validation steps (3-5.5) - Data already validated")
#             logger.info("="*60)
#             theme_counts = None  # Not needed for pre-processed data
#         else:
#             # Step 2.5: Calculate original theme counts (BEFORE any filtering)
#             logger.info("-" * 60)
#             logger.info("STEP 2.5: Calculating original theme counts")
#             theme_counts = calculate_theme_counts(df)
            
#             # Step 3: Remove null/empty rows
#             logger.info("-" * 60)
#             logger.info("STEP 3: Data validation - Removing null/empty rows")
#             df = remove_null_empty_rows(df, MANDATORY_OUTPUT_COLUMNS)
            
#             if len(df) == 0:
#                 logger.warning("No rows after removing null/empty values")
#                 return None
            
#             # Step 4: Remove PII flagged rows
#             logger.info("-" * 60)
#             logger.info("STEP 4: Data validation - Removing PII flagged rows")
#             df = remove_pii_rows(df)
            
#             if len(df) == 0:
#                 logger.warning("No rows after removing PII flagged data")
#                 return None
            
#             # Step 5: Remove Unknown/Unclear themes
#             logger.info("-" * 60)
#             logger.info("STEP 5: Data validation - Removing Unknown/Unclear themes")
#             df = remove_unknown_themes(df)
            
#             if len(df) == 0:
#                 logger.warning("No rows after removing Unknown/Unclear themes")
#                 return None
            
#             # Step 5.5: Remove rows with spelling mistakes
#             logger.info("-" * 60)
#             logger.info("STEP 5.5: Data validation - Removing spelling mistakes")
#             df = remove_spelling_mistakes(df)
            
#             if len(df) == 0:
#                 logger.warning("No rows after removing spelling mistakes")
#                 return None
            
#             # Step 5.6: Remove semantic duplicates
#             logger.info("-" * 60)
#             logger.info("STEP 5.6: Data validation - Removing semantic duplicates")
#             df = remove_semantic_duplicates(df, similarity_threshold=0.85)
            
#             if len(df) == 0:
#                 logger.warning("No rows after removing semantic duplicates")
#                 return None
        
#         # Step 6: Apply business logic filters
#         logger.info("-" * 60)
#         logger.info("STEP 6: Applying business logic filters")
#         filtered_df = filter_data_by_theme(df)
        
#         if len(filtered_df) == 0:
#             logger.warning("No rows matched business logic criteria")
#             return None
        
#         # Add voices_raised column only if not already present
#         if 'voices_raised' not in filtered_df.columns and theme_counts is not None:
#             filtered_df['voices_raised'] = filtered_df.apply(
#                 lambda row: theme_counts.get((row['theme_id'], row['theme_name']), 0), 
#                 axis=1
#             )
            
#             # Reorder columns to place voices_raised after theme_name
#             cols = list(filtered_df.columns)
#             theme_name_index = cols.index('theme_name')
#             cols.insert(theme_name_index + 1, cols.pop(cols.index('voices_raised')))
#             filtered_df = filtered_df[cols]
        
#         # Step 7: Construct JSON
#         logger.info("-" * 60)
#         logger.info("STEP 7: Constructing JSON")
#         json_output = construct_json(filtered_df, theme_counts)
        
#         # Step 8: Save locally
#         logger.info("-" * 60)
#         logger.info("STEP 8: Saving locally")
#         local_filename = output_filename.split('/')[-1]
#         local_filepath = save_json_locally(json_output, local_output_dir, local_filename)

#         # Step 8.5: Save CSV
#         logger.info("-" * 60)
#         logger.info("STEP 8.5: Saving CSV")
#         csv_filepath = save_csv_locally(filtered_df, local_output_dir, local_filename)
        
#         # Step 9: Upload to GCP
#         logger.info("-" * 60)
#         logger.info("STEP 9: Uploading to GCP")
        
#         upload_success = upload_to_gcp(json_output, bucket_name, output_filename, credentials)
        
#         if not upload_success:
#             logger.info("Trying gsutil fallback...")
#             upload_success = upload_to_gcp_gsutil(local_filepath, bucket_name, output_filename)
        
#         if not upload_success:
#             logger.warning("⚠️  Auto-upload failed. Please upload manually:")
#             logger.warning(f"gsutil cp {local_filepath} gs://{bucket_name}/{output_filename}")
        
#         logger.info("="*60)
#         logger.info("✓ PIPELINE COMPLETED SUCCESSFULLY!")
#         logger.info("="*60)
#         logger.info(f"Local JSON: {local_filepath}")
#         logger.info(f"Local CSV: {csv_filepath}")
#         logger.info(f"GCP: gs://{bucket_name}/{output_filename}")
        
#         return json_output
        
#     except Exception as e:
#         logger.error("="*60)
#         logger.error("✗ PIPELINE FAILED")
#         logger.error("="*60)
#         logger.error(f"Error: {str(e)}", exc_info=True)
#         raise

# def main():
#     #local configurations 
#     LOCAL_CSV_PATH = config.get("GCP", "THEMES_LOCAL_CSV_PATH")
#     OUTPUT_FILENAME = config.get("GCP", "THEMES_OUTPUT_FILENAME")
#     LOCAL_OUTPUT_DIR = config.get("GCP", "LOCAL_OUTPUT_DIR")
#     LOCAL_JSON_PATH = os.path.join(LOCAL_OUTPUT_DIR, OUTPUT_FILENAME)

#     #cloud configurations
#     INPUT_BLOB_PATH = config.get("GCP", "INPUT_BLOB_PATH")
#     BUCKET_NAME = config.get("GCP", "BUCKET_NAME")  
#     OUTPUT_BLOB_NAME = config.get("GCP", "OUTPUT_BLOB_NAME")
#     OUTPUT_FILENAME = "themes_emerged.json"

#     credentials = get_gcp_credentials()
    
#     CSV_PATH = fetch_the_csv_file_from_gcp(LOCAL_CSV_PATH, BUCKET_NAME, INPUT_BLOB_PATH, credentials)
      
#     logger.info("Application started")
#     logger.info(f"Configuration loaded")
    
#     try:
#         result = process_csv(CSV_PATH, BUCKET_NAME, OUTPUT_FILENAME, LOCAL_OUTPUT_DIR)
        
#         if result:
#             print('Total themes in output JSON:', len(result["data"]))
        

#         logger.info("Application finished successfully")
        
#     except Exception as e:
#         logger.critical(f"Application terminated: {str(e)}")
#         raise

# if __name__ == "__main__":
#     main()
