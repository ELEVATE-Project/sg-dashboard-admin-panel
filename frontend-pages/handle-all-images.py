import streamlit as st
from google.cloud import storage
from google.oauth2 import service_account
import os

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

credentials = service_account.Credentials.from_service_account_info(service_account_info)
client = storage.Client(credentials=credentials, project=service_account_info["project_id"])

bucket_name = os.environ.get("BUCKET_NAME")
from constants import BUCKET_PREFIX_FOR_IMAGES as prefix
bucket = client.bucket(bucket_name)
blobs = list(bucket.list_blobs(prefix=prefix)) 

st.title("GCS Images")

for blob in blobs:
    if blob.name.endswith("/"):
        continue

    col1, col2, col3, col4 = st.columns([1, 2, 4, 1])

    with col1:
        st.image(blob.public_url, width=50)

    with col2:
        st.write(blob.name.replace(prefix, ""))

    with col3:
        st.write(blob.public_url.replace(prefix, ""))

    with col4:
        if st.button("Delete", key=blob.name):
            blob.delete()
            st.success(f"Deleted {blob.name}")
            st.rerun()
