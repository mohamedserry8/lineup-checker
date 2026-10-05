#!/bin/bash
mkdir -p .streamlit
cat > .streamlit/secrets.toml << SECRETS
sheet_id = "${SHEET_ID}"
sheet_tab = "${SHEET_TAB}"

[gcp_service_account]
type = "service_account"
project_id = "lineup-checker"
private_key_id = "${GCP_PRIVATE_KEY_ID}"
private_key = "${GCP_PRIVATE_KEY}"
client_email = "${GCP_CLIENT_EMAIL}"
client_id = "${GCP_CLIENT_ID}"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "${GCP_CLIENT_CERT_URL}"
SECRETS

streamlit run app.py --server.port=10000 --server.address=0.0.0.0
