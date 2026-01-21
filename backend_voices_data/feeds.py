import pandas as pd
import json
import os
import re
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from constants import * 
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
REQUIRED_COLUMNS = FEEDS_REQUIRED_COLUMNS
MANDATORY_OUTPUT_COLUMNS = FEEDS_MANDATORY_OUTPUT_COLUMNS

# Setup Logger
logger = setup_logger(LOG_DIR, 'feeds.log', 'CSVProcessor')

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

def filter_data(df):
    """
    - Check if ANY row has add_to_frontend == True
    - If YES: Return ONLY those rows (ignore rows with null add_to_frontend)
    - If NO: Process up to 100 rows with confidence_score >= 0.9 and length > 30
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
        # IF TRUE rows exist, return ONLY those rows
        logger.info(f"✓ Found {len(true_rows)} rows with add_to_frontend=True")
        logger.info("Returning ONLY rows where add_to_frontend=True (ignoring null rows)")
        return true_rows.copy()
    
    else:
        # ELSE: No TRUE rows, process null rows with filters
        logger.info("No rows with add_to_frontend=True found")
        logger.info("Processing rows with null add_to_frontend...")
        
        df_null = df[df['add_to_frontend'].isna()]
        logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
        if len(df_null) == 0:
            logger.warning("No rows with null add_to_frontend to process")
            return df.head(0)
            # return pd.DataFrame()
        
        # Apply confidence and length filters
        filtered_null = df_null[
            (df_null['confidence_score'] >= 0.9) &
            (df_null['action_steps'].str.len() > 30) &
            (df_null['impact'].str.len() > 30)
        ]
        logger.debug(f"Eligible rows (confidence>=0.9, length>30): {len(filtered_null)}")
        
        if filtered_null.empty:
            logger.warning("No rows meet the criteria (confidence>=0.9, length>30)")
            return df.head(0)
        
        # Get unique states from data
        unique_states = filtered_null['state'].str.strip().unique()
        num_states = len(unique_states)
        
        if len(unique_states) == 0:
            logger.warning("No states found in data")
            return df.head(0)
        
        # Calculate samples per state (distribute 100 samples)
        total_needed = 100
        samples_per_state = total_needed // num_states
        remaining_samples = total_needed % num_states
        
        logger.info(f"Found {num_states} unique states: {', '.join(unique_states)}")
        logger.info(f"Sampling strategy: {samples_per_state} per state (+ {remaining_samples} additional)")
        
        # First pass: Collect samples from each state (equal distribution)
        state_samples = {}
        total_collected = 0
        
        for idx, state in enumerate(unique_states):
            state_data = filtered_null[filtered_null['state'].str.strip() == state]
            
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
                logger.info(f"  {state}: Collected {state_sample_size}/{len(state_data)} rows (requested {base_samples}) from {state_sample['district'].nunique()} districts")
            else:
                logger.warning(f"  {state}: No data available")
        
        # Second pass: Redistribute unfilled quota to states with remaining data
        shortfall = total_needed - total_collected
        
        if shortfall > 0:
            logger.info(f"Shortfall of {shortfall} samples, redistributing...")
            
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
                        logger.info(f"  {state}: Added {extra_available} extra samples (total now: {len(state_samples[state]['sampled'])})")
        
        # Collect all samples
        selected_rows_list = []
        for state, info in state_samples.items():
            selected_rows_list.append(info['sampled'])
            logger.info(f"  {state}: Final count {len(info['sampled'])} rows from {info['sampled']['district'].nunique()} districts")
        
        if not selected_rows_list:
            logger.warning("No rows selected across all states")
            return pd.DataFrame()
        
        result_df = pd.concat(selected_rows_list, ignore_index=True)
        logger.info(f"✓ Selected total of {len(result_df)} rows across all states")
        return result_df

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
    
    json_output = {"identification": "micro_improvement_feed", "data": data_list}
    logger.info(f"✓ JSON constructed with {len(data_list)} records")
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
    
    # Check both action_steps and impact columns
    mask = ~(df['action_steps'].apply(is_low_quality_text) | 
             df['impact'].apply(is_low_quality_text))
    
    filtered_df = df[mask].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} rows with low-quality text. Remaining: {len(filtered_df)}")
    return filtered_df

def remove_semantic_duplicates(df, similarity_threshold=0.85):
    """
    Remove semantically similar action_steps and impact entries
    similarity_threshold: 0-1, higher means more strict (0.85 means 85% similar)
    """
    logger.info(f"Removing semantically similar records (threshold: {similarity_threshold})...")
    initial_count = len(df)
    
    # Load sentence transformer model
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Combine action_steps and impact for similarity comparison
    combined_texts = (df['action_steps'].fillna('') + ' ' + df['impact'].fillna('')).tolist()
    
    if len(combined_texts) <= 1:
        logger.info("Only 1 or fewer records, skipping semantic duplicate removal")
        return df
    
    # Get embeddings for all combined texts
    embeddings = model.encode(combined_texts)
    
    # Calculate pairwise cosine similarity
    similarities = cosine_similarity(embeddings)
    
    # Keep track of which rows to keep
    keep_indices = []
    
    for i in range(len(combined_texts)):
        # Check if current record is similar to any already kept record
        is_duplicate = False
        for kept_idx in keep_indices:
            if similarities[i][kept_idx] > similarity_threshold:
                is_duplicate = True
                break
        
        if not is_duplicate:
            keep_indices.append(i)
    
    # Keep only non-duplicate rows
    filtered_df = df.iloc[keep_indices].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} semantically similar records. Remaining: {len(filtered_df)}")
    return filtered_df

def process_csv(csv_path, bucket_name, output_filename, local_output_dir):
    try:
        logger.info("="*60)
        logger.info("Starting CSV processing pipeline")
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
        
        # Step 4.5: Remove rows with spelling mistakes
        logger.info("-" * 60)
        logger.info("STEP 4.5: Data validation - Removing spelling mistakes")
        df = remove_spelling_mistakes(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing spelling mistakes")
            return None
        
        # Step 4.6: Remove semantic duplicates
        logger.info("-" * 60)
        logger.info("STEP 4.6: Data validation - Removing semantic duplicates")
        df = remove_semantic_duplicates(df, similarity_threshold=0.85)
        
        if len(df) == 0:
            logger.warning("No rows after removing semantic duplicates")
            return None
        
        # Step 5: Apply business logic filters
        logger.info("-" * 60)
        logger.info("STEP 5: Applying business logic filters")
        filtered_df = filter_data(df)
        
        # Step 6: Construct JSON
        logger.info("-" * 60)
        logger.info("STEP 6: Constructing JSON")
        json_output = construct_json(filtered_df)
        
        # Step 7: Save locally
        logger.info("-" * 60)
        logger.info("STEP 7: Saving JSON locally")
        local_filename = output_filename.split('/')[-1]
        local_filepath = save_json_locally(json_output, local_output_dir, local_filename, logger)
        
        # Step 7.5: Save CSV
        logger.info("-" * 60)
        logger.info("STEP 7.5: Saving CSV")
        csv_filepath = save_csv_locally(filtered_df, local_output_dir, local_filename, logger)

        # Step 8: Upload to GCP
        logger.info("-" * 60)
        logger.info("STEP 8: Uploading to GCP")
        
        # Try with credentials first, fallback to gsutil
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
    LOCAL_CSV_PATH = os.getenv("FEEDS_LOCAL_CSV_PATH")
    LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR")
    INPUT_BLOB_PATH = os.getenv("INPUT_BLOB_PATH")
    BUCKET_NAME = os.getenv("BUCKET_NAME")    

    OUTPUT_FILENAME = "sg-dashboard/microimprovement_feeds.json"
    credentials = get_gcp_credentials()
    
    CSV_PATH = fetch_csv_from_gcp(
        LOCAL_CSV_PATH, 
        BUCKET_NAME, 
        INPUT_BLOB_PATH, 
        credentials, 
        "microimprovement_feeds.csv",
        logger
    )

    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        result = process_csv(CSV_PATH, BUCKET_NAME, OUTPUT_FILENAME, LOCAL_OUTPUT_DIR)
        
        if result:
            print('Total feeds in output JSON:', len(result["data"]))

        logger.info("Application finished successfully")
        
    except Exception as e:
        logger.critical(f"Application terminated: {str(e)}")
        raise

if __name__ == "__main__":
    main()
