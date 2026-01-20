import os
import logging
from datetime import datetime
from google.cloud import storage
from dotenv import load_dotenv
from backend_voices_data import feeds, themes, stories, animations
from .utils import get_env_var, get_gcp_credentials

# --------------------------------------------------
# CONFIG & LOGGING
# --------------------------------------------------

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BackendUploader")

CSV_TYPES = [
    "Micro Improvement Feed",
    "Themes Emerged",
    "Stories",
    "Cracks and Flowers"
]

# --------------------------------------------------
# UTILS
# --------------------------------------------------

def generate_timestamp():
    """Safe timestamp for filenames"""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def generate_csv_filename(csv_type):
    if csv_type == "Micro Improvement Feed":
        return f"microimprovement_feeds.csv"

    if csv_type == "Themes Emerged":
        return f"themes_emerged.csv"
    
    if csv_type == "Stories":
        return f"stories.csv"
    
    if csv_type == "Cracks and Flowers":
        return f"cracks_and_flowers.csv"

    safe_type = csv_type.replace(" ", "_").lower()
    return f"{safe_type}.csv"

# --------------------------------------------------
# CORE LOGIC
# --------------------------------------------------

def upload_to_gcp(uploaded_file, csv_type):
    """
    Upload CSV → GCS → Trigger processing → Upload JSON
    """
    if csv_type not in CSV_TYPES:
        return False, f"Invalid CSV type: {csv_type}"

    try:
        bucket_name = get_env_var("BUCKET_NAME")
        if not bucket_name:
            return False, "Bucket name not configured"

        credentials = get_gcp_credentials()
        logger.info("✓ GCP credentials loaded successfully")
        
        client = storage.Client(
            credentials=credentials,
            project=credentials.project_id
        )
        bucket = client.bucket(bucket_name)

        # -------- CSV UPLOAD --------
        csv_filename = generate_csv_filename(csv_type)
        
        # Prepare valid blob path (remove bucket name if present in INPUT_BLOB_PATH)
        raw_input_path = get_env_var("INPUT_BLOB_PATH")
        if raw_input_path.startswith(f"{bucket_name}/"):
            raw_input_path = raw_input_path[len(bucket_name)+1:]
        
        # Clean up slashes
        raw_input_path = raw_input_path.strip("/")
        csv_blob_path = f"{raw_input_path}/{csv_filename}"

        uploaded_file.seek(0)
        csv_blob = bucket.blob(csv_blob_path)
        csv_blob.upload_from_file(uploaded_file, content_type="text/csv")

        logger.info(f"CSV uploaded → gs://{bucket_name}/{csv_blob_path}")
        
        success, msg = trigger_processing(csv_type)

        if not success:
            return False, msg

        return True, f"Upload + Processing successful ({csv_filename})"

    except Exception as e:
        logger.error("Upload failed", exc_info=True)
        return False, str(e)


def trigger_processing(csv_type):
    """
    Call feeds.py / themes.py / stories.py / animations.py after upload
    """

    try:
        if csv_type == "Micro Improvement Feed":
            feeds.main()
            logger.info("Microimprovement Feed processed")
            return True, "Microimprovement Feed processed"

        if csv_type == "Themes Emerged":
            themes.main()
            logger.info("Themes Emerged processed")
            return True, "Themes Emerged processed"
            
        if csv_type == "Stories":
            stories.main()
            logger.info("Stories processed")
            return True, "Stories processed"
        
        if csv_type == "Cracks and Flowers":
            animations.main()
            logger.info("Cracks and Flowers processed")
            return True, "Cracks and Flowers processed"

        logger.info(f"No processing defined for {csv_type}")
        return True, "Upload completed (no processing)"

    except Exception as e:
        logger.error("Processing failed", exc_info=True)
        return False, str(e)
