import os
import sys
import boto3
import firebase_admin
from firebase_admin import credentials, firestore

# =====================================================================
# ⚙️ CONFIGURATION & SECRETS (GitHub Actions Env Variables)
# =====================================================================

# --- SOURCE CLOUDFLARE (Purana Account - Existing Scraper wale variables) ---
SOURCE_ACCESS_KEY = os.environ.get("CF_ACCESS_KEY", "").strip()
SOURCE_SECRET_KEY = os.environ.get("CF_SECRET_KEY", "").strip()
SOURCE_ENDPOINT = os.environ.get("CF_ENDPOINT", "").strip()
SOURCE_BUCKET = os.environ.get("CF_BUCKET_NAME", "").strip()
SOURCE_PUBLIC_URL = os.environ.get("CF_PUBLIC_URL", "").strip()

# --- DESTINATION CLOUDFLARE (Naya Account) ---
DEST_ACCOUNT_ID = os.environ.get("DEST_CF_ACCOUNT_ID", "").strip()
DEST_ACCESS_KEY = os.environ.get("DEST_CF_ACCESS_KEY", "").strip()
DEST_SECRET_KEY = os.environ.get("DEST_CF_SECRET_KEY", "").strip()
DEST_BUCKET = os.environ.get("DEST_CF_BUCKET_NAME", "").strip()
DEST_PUBLIC_URL = os.environ.get("DEST_CF_PUBLIC_URL", "").strip()

DEST_ENDPOINT = f"https://{DEST_ACCOUNT_ID}.r2.cloudflarestorage.com"

# --- FIREBASE SETUP ---
FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"
FIRESTORE_COLLECTION = "live_notices"
FIELD_TO_UPDATE = "serverFileUrl"

# =====================================================================
# 🚀 INITIALIZE CONNECTIONS
# =====================================================================

print("🔄 Initializing connections for Migration...")

# 1. Firebase Initialization
if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
    print(f"❌ Error: '{FIREBASE_SERVICE_ACCOUNT_JSON}' file nahi mili!")
    sys.exit(1)

if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Source R2 Client
s3_source = boto3.client(
    service_name='s3',
    endpoint_url=SOURCE_ENDPOINT,
    aws_access_key_id=SOURCE_ACCESS_KEY,
    aws_secret_access_key=SOURCE_SECRET_KEY,
    region_name='auto'
)

# 3. Destination R2 Client
s3_dest = boto3.client(
    service_name='s3',
    endpoint_url=DEST_ENDPOINT,
    aws_access_key_id=DEST_ACCESS_KEY,
    aws_secret_access_key=DEST_SECRET_KEY,
    region_name='auto'
)

# =====================================================================
# 📦 STEP 1: TRANSFER FILES FROM OLD R2 TO NEW R2
# =====================================================================
def transfer_files():
    print("\n📦 === STARTING FILE TRANSFER (R2 to R2) ===")
    
    try:
        # Paginator ka use karna safe hai agar files 1000 se zyada hon
        paginator = s3_source.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=SOURCE_BUCKET)
        
        file_count = 0
        for page in pages:
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                file_key = obj['Key']
                print(f"📥 Migrating file: {file_key}")
                
                temp_path = f"/tmp/{file_key.replace('/', '_')}"
                
                # 1. Source se file download karein
                s3_source.download_file(SOURCE_BUCKET, file_key, temp_path)
                
                # 2. Destination mein upload karein (Content-Type maintain karne ki zaroorat nahi padti yahan unless specifically required)
                s3_dest.upload_file(temp_path, DEST_BUCKET, file_key)
                
                # 3. Temp file delete karein space bachane ke liye
                os.remove(temp_path)
                file_count += 1
                
        print(f"✅ File Transfer Complete! Total {file_count} files migrated.")
        
    except Exception as e:
        print(f"❌ File Transfer Error: {e}")
        sys.exit(1)

# =====================================================================
# 🎯 STEP 2: UPDATE URLs IN FIRESTORE
# =====================================================================
def update_firestore_urls():
    print("\n🎯 === STARTING FIRESTORE URL UPDATE ===")
    
    if not SOURCE_PUBLIC_URL or not DEST_PUBLIC_URL:
        print("⚠️ Warning: SOURCE_PUBLIC_URL ya DEST_PUBLIC_URL set nahi hai. URL update skip ho raha hai.")
        return

    try:
        docs = db.collection(FIRESTORE_COLLECTION).stream()
        batch = db.batch()
        update_count = 0
        total_docs_checked = 0

        # Dono URLs se trailing slash hata dein matching ke liye
        old_domain = SOURCE_PUBLIC_URL.rstrip('/')
        new_domain = DEST_PUBLIC_URL.rstrip('/')

        for doc in docs:
            total_docs_checked += 1
            data = doc.to_dict()
            current_url = data.get(FIELD_TO_UPDATE, '')

            # Agar existing URL mein purana domain match hota hai
            if isinstance(current_url, str) and old_domain in current_url:
                new_url = current_url.replace(old_domain, new_domain)
                
                doc_ref = db.collection(FIRESTORE_COLLECTION).document(doc.id)
                batch.update(doc_ref, {FIELD_TO_UPDATE: new_url})
                update_count += 1
                print(f"🔗 URL Updated in Doc [{doc.id}]: {new_url.split('/')[-1]}")

                # Firestore batch limit 500 hoti hai
                if update_count % 400 == 0:
                    batch.commit()
                    batch = db.batch()
                    print(f"💾 Committed 400 updates to Firestore...")

        # Baaki bache huye documents commit karein
        if update_count % 400 != 0:
            batch.commit()

        print(f"✅ Firestore Update Complete! Checked {total_docs_checked} docs, Updated {update_count} links.")

    except Exception as e:
        print(f"❌ Firestore Update Error: {e}")
        sys.exit(1)

# =====================================================================
# 🚦 MAIN EXECUTION
# =====================================================================
if __name__ == "__main__":
    transfer_files()
    update_firestore_urls()
    print("\n🚀 ALL MIGRATION TASKS COMPLETED SUCCESSFULLY!")
