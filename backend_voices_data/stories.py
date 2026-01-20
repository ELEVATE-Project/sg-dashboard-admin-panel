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
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL")
PDF_BASE_URL = os.getenv("PDF_BASE_URL")

# Required columns
REQUIRED_COLUMNS = ['story_id', 'story_title', 'updated_story_title', 'content', 'pdf_link', 'document_language', 
                    'composite_score', 'add_to_frontend', 'overall_summary', 'image_link', 'role', 'district', 'state', 
                    'translated_title', 'translated_content', 'translated_role', 'translated_district', 'translated_state']

MANDATORY_OUTPUT_COLUMNS = ['story_title', 'content', 'pdf_link', 'document_language', 'role', 'district', 'state']

# Setup Logger
logger = setup_logger(LOG_DIR, 'stories.log', 'StoriesProcessor')

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

def filter_story_data(df):
    """
    LOGIC:
    - Check if ANY row has add_to_frontend == True
    - If YES: Return ONLY those rows
    - If NO: Select up to 70 stories with filters
          - Pick only if the composite score >= 0.75
          - Distributed as 35 rows per state
          - Mixed districts within each state
    """
    logger.info("Applying business logic filters...")
    logger.debug(f"Total rows: {len(df)}")
    
    TOTAL_STORIES = 70
    STORIES_PER_STATE = 35
    
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
        # Process null rows with filters
        logger.info("No rows with add_to_frontend=True found")
        
        df_null = df[df['add_to_frontend'].isna()]
        logger.debug(f"Rows with null add_to_frontend: {len(df_null)}")
        
        if len(df_null) == 0:
            logger.warning("No rows with null add_to_frontend to process")
            return pd.DataFrame()
        
        # Apply filters: composite_score >= 0.75
        filtered_null = df_null[df_null['composite_score'] >= 0.75]
        logger.debug(f"Eligible rows (composite_score>=0.75): {len(filtered_null)}")
        
        if len(filtered_null) == 0:
            logger.warning("No rows meet the criteria (composite_score>=0.75)")
            return pd.DataFrame()
        
        # Get unique states from data
        unique_states = filtered_null['state'].str.strip().unique()
        num_states = len(unique_states)
        
        if num_states == 0:
            logger.warning("No states found in data")
            return pd.DataFrame()
        
        logger.info(f"Found {num_states} unique states: {', '.join(unique_states)}")
        logger.info(f"Sampling strategy: {STORIES_PER_STATE} stories per state (Total target: {TOTAL_STORIES})")
        
        # Select stories distributed across states and districts
        selected_rows_list = []
        
        for state in unique_states:
            state_data = filtered_null[filtered_null['state'].str.strip() == state]
            
            # Get unique districts in this state
            districts = state_data['district'].str.strip().unique()
            num_districts = len(districts)
            
            logger.info(f"\n  Processing {state}:")
            logger.info(f"    Available rows: {len(state_data)}")
            logger.info(f"    Districts: {num_districts} ({', '.join(districts)})")
            
            if len(state_data) == 0:
                logger.warning(f"    No data available for {state}")
                continue
            
            # Calculate samples per district
            samples_per_district = STORIES_PER_STATE // num_districts
            remaining_samples = STORIES_PER_STATE % num_districts
            
            state_samples = []
            total_collected_for_state = 0
            
            # First pass: Collect samples from each district (equal distribution)
            district_data_map = {}
            for idx, district in enumerate(districts):
                district_data = state_data[state_data['district'].str.strip() == district]
                
                # Calculate sample size for this district
                base_samples = samples_per_district
                if idx < remaining_samples:
                    base_samples += 1
                
                district_sample_size = min(base_samples, len(district_data))
                
                if district_sample_size > 0:
                    district_sample = district_data.sample(n=district_sample_size, random_state=42)
                    state_samples.append(district_sample)
                    total_collected_for_state += district_sample_size
                    
                    # Store remaining data for redistribution
                    district_data_map[district] = district_data.drop(district_sample.index)
                    
                    logger.info(f"      {district}: Collected {district_sample_size}/{len(district_data)} rows")
                else:
                    district_data_map[district] = district_data
                    logger.warning(f"      {district}: No data available")
            
            # Second pass: Redistribute unfilled quota to districts with remaining data
            shortfall = STORIES_PER_STATE - total_collected_for_state
            
            if shortfall > 0:
                logger.info(f"    Shortfall of {shortfall} samples for {state}, redistributing...")
                
                # Find districts with remaining data
                districts_with_extra = [(dist, data) for dist, data in district_data_map.items() 
                                       if len(data) > 0]
                
                if districts_with_extra:
                    # Distribute shortfall across districts
                    extra_per_district = shortfall // len(districts_with_extra)
                    extra_remainder = shortfall % len(districts_with_extra)
                    
                    for idx, (district, remaining_data) in enumerate(districts_with_extra):
                        extra_needed = extra_per_district
                        if idx < extra_remainder:
                            extra_needed += 1
                        
                        extra_available = min(extra_needed, len(remaining_data))
                        
                        if extra_available > 0:
                            extra_sample = remaining_data.sample(n=extra_available, random_state=42)
                            state_samples.append(extra_sample)
                            total_collected_for_state += extra_available
                            logger.info(f"      {district}: Added {extra_available} extra samples")
            
            # Combine all samples for this state
            if state_samples:
                state_df = pd.concat(state_samples, ignore_index=True)
                selected_rows_list.append(state_df)
                logger.info(f"  ✓ {state}: Total collected {len(state_df)} rows from {len(districts)} districts")
        
        if not selected_rows_list:
            logger.warning("No rows selected")
            return pd.DataFrame()
        
        result_df = pd.concat(selected_rows_list, ignore_index=True)
        
        # Summary
        logger.info(f"\n{'='*60}")
        logger.info(f"SELECTION SUMMARY:")
        logger.info(f"  Total rows selected: {len(result_df)}/{TOTAL_STORIES}")
        logger.info(f"  States: {result_df['state'].nunique()}")
        logger.info(f"  Districts: {result_df['district'].nunique()}")
        
        for state in result_df['state'].unique():
            state_count = len(result_df[result_df['state'] == state])
            state_districts = result_df[result_df['state'] == state]['district'].nunique()
            logger.info(f"    {state}: {state_count} rows across {state_districts} districts")
        
        logger.info(f"{'='*60}\n")
        
        return result_df

def clean_text(text):
    """Clean text by removing extra spaces and special characters"""
    if pd.isna(text) or text == '':
        return text
    return re.sub(r'\s+', ' ', str(text).strip())

def construct_json(df):
    logger.info("Constructing JSON...")

    grouped = df.groupby('story_id')
    data_list = []

    for story_id, group in grouped:
        english_rows = group[group['document_language'].str.lower() == 'english']
        first_row = english_rows.iloc[0] if not english_rows.empty else group.iloc[0]

        # Photos
        photos = []
        if pd.notna(first_row['image_link']) and first_row['image_link'] != '':
            photos = [img.strip() for img in str(first_row['image_link']).split('|') if img.strip()]

        download_link = first_row['pdf_link'] if pd.notna(first_row['pdf_link']) else ''

        lang_list = []

        # 1️⃣ English (ALWAYS FIRST)
        english_obj = {
            "code": "English",
            "data": {
                "title": clean_text(first_row['updated_story_title']),
                "content": clean_text(first_row['content']),
                "role": clean_text(first_row['role']),
                "district": clean_text(first_row['district']),
                "state": clean_text(first_row['state'])
            }
        }
        lang_list.append(english_obj)

        # 2️⃣ Translated language (OPTIONAL)
        translated_row = group[
            (group['document_language'].notna()) &
            (group['document_language'].str.lower() != 'english')
        ]

        if not translated_row.empty:
            row = translated_row.iloc[0]

            translated_obj = {
                "code": clean_text(row['document_language']),
                "data": {
                    "title": clean_text(row['translated_title']),
                    "content": clean_text(row['translated_content']),
                    "role": clean_text(row['translated_role']),
                    "district": clean_text(row['translated_district']),
                    "state": clean_text(row['translated_state'])
                }
            }
            lang_list.append(translated_obj)

        story_obj = {
            "id": str(story_id),
            "download_link": download_link,
            "photos": photos,
            "lang": lang_list
        }

        data_list.append(story_obj)
        logger.debug(f"Story ID {story_id}: {len(lang_list)} language objects")

    json_output = {
        "identification": "stories",
        "data": data_list
    }

    logger.info(f"✓ JSON constructed with {len(data_list)} stories")
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
    
    filtered_df = df[~df['content'].apply(is_low_quality_text)].copy()
    
    removed_count = initial_count - len(filtered_df)
    logger.info(f"✓ Removed {removed_count} rows with low-quality text. Remaining: {len(filtered_df)}")
    return filtered_df

def append_base_url(df, image_base_url, pdf_base_url):
    logger.info("Updating the base URL's")
    
    def update_links(links, base_url):
        if not base_url:
            return links
        if pd.isna(links) or links == '':
            return links
        link_list = str(links).split('|')
        updated = [f"{base_url}/{img.strip().lstrip('/')}" for img in link_list if img.strip() != '']
        return '|'.join(updated)
    
    df['image_link'] = df['image_link'].apply(lambda x: update_links(x, image_base_url))
    df['pdf_link'] = df['pdf_link'].apply(lambda x: update_links(x, pdf_base_url))
    
    return df

def remove_semantic_duplicates(df, similarity_threshold=0.85):
    """
    Remove semantically similar content within each story
    similarity_threshold: 0-1, higher means more strict (0.85 means 85% similar)
    """
    logger.info(f"Removing semantically similar content (threshold: {similarity_threshold})...")
    initial_count = len(df)
    
    # Load sentence transformer model
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    filtered_rows = []
    
    # Process each story separately
    grouped = df.groupby('story_id')
    
    for story_id, group in grouped:
        contents = group['content'].tolist()
        
        if len(contents) <= 1:
            filtered_rows.append(group)
            continue
        
        # Get embeddings for all contents in this story
        embeddings = model.encode(contents)
        
        # Calculate pairwise cosine similarity
        similarities = cosine_similarity(embeddings)
        
        # Keep track of which rows to keep
        keep_indices = []
        
        for i in range(len(contents)):
            # Check if current content is similar to any already kept content
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
            logger.info(f"  Story '{story_id}': Removed {removed} semantic duplicates, kept {len(filtered_group)}")
    
    result_df = pd.concat(filtered_rows, ignore_index=True)
    removed_count = initial_count - len(result_df)
    logger.info(f"✓ Removed {removed_count} semantically similar content. Remaining: {len(result_df)}")
    return result_df

def process_csv(csv_path, bucket_name, output_filename, local_output_dir):
    try:
        logger.info("="*60)
        logger.info("Starting Stories CSV processing pipeline")
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
        
        # Step 4: Remove rows with spelling mistakes
        logger.info("-" * 60)
        logger.info("STEP 4: Data validation - Removing spelling mistakes")
        df = remove_spelling_mistakes(df)
        
        if len(df) == 0:
            logger.warning("No rows after removing spelling mistakes")
            return None
        
        # Step 5: Apply business logic filters
        logger.info("-" * 60)
        logger.info("STEP 5: Applying business logic filters")
        filtered_df = filter_story_data(df)
        
        if len(filtered_df) == 0:
            logger.warning("No rows after business logic filters")
            return None
        
        # Step 6: Append the image and pdf with base URL (ONLY if add_to_frontend is NOT True)
        logger.info("-" * 60)
        logger.info("STEP 6: Appending the base URL's")
        
        # Check if we have rows with add_to_frontend=True
        has_frontend_true = (
            (filtered_df['add_to_frontend'] == True) | 
            (filtered_df['add_to_frontend'] == 'TRUE') |
            (filtered_df['add_to_frontend'] == 'true') |
            (filtered_df['add_to_frontend'] == 1)
        ).any()
        
        if not has_frontend_true:
            # Only append base URLs if NO rows have add_to_frontend=True
            filtered_df = append_base_url(filtered_df, IMAGE_BASE_URL, PDF_BASE_URL)
            logger.info("✓ Base URLs appended")
        else:
            logger.info("✓ Skipped base URL appending (add_to_frontend=True rows present)")
        
        # Step 7: Construct JSON
        logger.info("-" * 60)
        logger.info("STEP 7: Constructing JSON")
        json_output = construct_json(filtered_df)
        
        # Step 8: Save locally
        logger.info("-" * 60)
        logger.info("STEP 8: Saving JSON locally")
        local_filename = output_filename.split('/')[-1]
        local_filepath = save_json_locally(json_output, local_output_dir, local_filename, logger)
        
        # Step 8.5: Save CSV
        logger.info("-" * 60)
        logger.info("STEP 8.5: Saving CSV")
        csv_filepath = save_csv_locally(filtered_df, local_output_dir, local_filename, logger)

        # Step 9: Upload to GCP
        logger.info("-" * 60)
        logger.info("STEP 9: Uploading to GCP")
        
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
    LOCAL_CSV_PATH = os.getenv("STORIES_LOCAL_CSV_PATH")
    LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR")
    INPUT_BLOB_PATH = os.getenv("INPUT_BLOB_PATH")
    BUCKET_NAME = os.getenv("BUCKET_NAME")  
    OUTPUT_FILENAME = "sg-dashboard/stories.json"
    
    credentials = get_gcp_credentials()
    
    CSV_PATH = fetch_csv_from_gcp(
        LOCAL_CSV_PATH, 
        BUCKET_NAME, 
        INPUT_BLOB_PATH, 
        credentials, 
        "stories.csv",
        logger
    )

    if CSV_PATH is None:
        logger.error("Failed to fetch CSV from GCP. Exiting.")
        return

    logger.info("Application started")
    logger.info(f"Configuration loaded")
    
    try:
        result = process_csv(CSV_PATH, BUCKET_NAME, OUTPUT_FILENAME, LOCAL_OUTPUT_DIR)
        
        if result:
            print('Total stories in output JSON:', len(result["data"]))
        
        logger.info("Application finished successfully")
        
    except Exception as e:
        logger.critical(f"Application terminated: {str(e)}")
        raise

if __name__ == "__main__":
    main()
