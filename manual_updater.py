import os
import re
import sys
import glob
import hashlib
import boto3
from botocore.exceptions import NoCredentialsError
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import google.generativeai as genai

# =====================================================================
# ⚙️ CONFIGURATION LAYER (GitHub Secrets)
# =====================================================================
CLOUDFLARE_ACCESS_KEY = os.environ.get("CF_ACCESS_KEY")
CLOUDFLARE_SECRET_KEY = os.environ.get("CF_SECRET_KEY")
CLOUDFLARE_ENDPOINT = os.environ.get("CF_ENDPOINT")
CLOUDFLARE_PUBLIC_BASE_URL = os.environ.get("CF_PUBLIC_URL")
CLOUDFLARE_BUCKET_NAME = os.environ.get("CF_BUCKET_NAME")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"

# =====================================================================
# 🚀 CHANNELS INITIALIZATION
# =====================================================================
if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
    print(f"❌ Error: '{FIREBASE_SERVICE_ACCOUNT_JSON}' file nahi mili!")
    sys.exit(1)

cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
firebase_admin.initialize_app(cred)
db = firestore.client()
firestore_collection = db.collection("live_notices")

r2_client = boto3.client(
    service_name='s3',
    endpoint_url=CLOUDFLARE_ENDPOINT,
    aws_access_key_id=CLOUDFLARE_ACCESS_KEY,
    aws_secret_access_key=CLOUDFLARE_SECRET_KEY,
    region_name='auto'
)

# =====================================================================
# 🛠️ HELPER FUNCTIONS
# =====================================================================
def clean_document_id(file_name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', file_name.replace(".pdf", ""))
    if len(safe_name) > 50:
        hash_suffix = hashlib.md5(file_name.encode()).hexdigest()[:6]
        return f"{safe_name[:40]}_{hash_suffix}"
    return safe_name

def process_pdf_with_gemini(bytes_payload):
    """
    Gemini se Title aur Hindi Summary dono ek saath nikalna
    """
    if not GEMINI_API_KEY:
        return "Manual Notice", "AI Summary unavailable (No API Key)"
        
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        prompt = (
            "Task: Please read the entire attached document thoroughly from start to finish.\n"
            "1. Extract or generate a short, professional, and clear Title for this notice in Hindi/English mix.\n"
            "2. Provide a highly accurate and easy-to-understand 4-5 line (bullet point) summary in Hindi (Devanagari script).\n\n"
            "Format your response exactly as follows:\n"
            "TITLE: <write title here>\n"
            "SUMMARY: <write summary bullet points here>"
        )
        
        response = model.generate_content([
            prompt,
            {"mime_type": "application/pdf", "data": bytes_payload}
        ])
        
        text_response = response.text.strip()
        
        # Parsing Title and Summary using Regex
        title_match = re.search(r'TITLE:\s*(.*)', text_response, re.IGNORECASE)
        summary_match = re.search(r'SUMMARY:\s*(.*)', text_response, re.IGNORECASE | re.DOTALL)
        
        extracted_title = title_match.group(1).strip() if title_match else "New Manual Notice"
        extracted_summary = summary_match.group(1).strip() if summary_match else "Summary generation failed."
        
        return extracted_title, extracted_summary
    except Exception as e:
        print(f"⚠️ Google AI Processing Error: {e}")
        return "Manual Notice", "Summary generation failed due to an error."

def send_fcm_push_notification(notice_title):
    try:
        display_title = "📢 UPMSP BOARD ALERT!"
        display_body = f"📄 New Document Released:\n{notice_title}"
            
        if len(display_body) > 120:
            display_body = display_body[:117] + "..."

        message = messaging.Message(
            data={
                'title': display_title,
                'body': display_body,
                'badge': '1',
                'channel_id': 'upmsp_notices_channel'  
            },
            topic="all_users"
        )
        response = messaging.send(message)
        print(f"📢 Push Notification Sent -> {response}")
    except Exception as n_err:
        print(f"⚠️ Notification System Error: {n_err}")

# =====================================================================
# 🎯 MAIN MANUAL PIPELINE ENGINE
# =====================================================================
def run_manual_pipeline():
    input_dir = "inputs"
    
    if not os.path.exists(input_dir):
        os.makedirs(input_dir)
        print(f"📂 '{input_dir}' folder bana diya gaya hai. Isme PDF files daalein.")
        return

    # inputs folder ke saare pdf files ko scan karna
    pdf_files = glob.glob(os.path.join(input_dir, "*.pdf"))
    
    if not pdf_files:
        print("🔍 'inputs/' folder khali hai. Koi new PDF nahi mili.")
        return

    print(f"🚀 Found {len(pdf_files)} PDF file(s) to process.")

    for file_path in pdf_files:
        file_name = os.path.basename(file_path)
        doc_id = clean_document_id(file_name)

        # Duplicate Check in Firestore
        try:
            doc_ref = firestore_collection.document(doc_id)
            if doc_ref.get().exists:
                print(f"⏭️ Skipping: {file_name} (Database me already exists hai)")
                continue
        except Exception as err:
            print(f"⚠️ Firestore registry check error: {err}")
            continue

        print("-" * 50)
        print(f"📄 Processing Manual File: {file_name}")
        
        # File reading in bytes
        with open(file_path, "rb") as f:
            bytes_payload = f.read()

        # Step 1: Gemini Processing
        print("🧠 Extracting Title and Summary from AI...")
        extracted_title, ai_summary = process_pdf_with_gemini(bytes_payload)
        print(f"📝 Title: {extracted_title}")

        # Step 2: Cloudflare R2 Upload
        print("☁️ Uploading binary data to Cloudflare R2...")
        try:
            r2_client.put_object(
                Bucket=CLOUDFLARE_BUCKET_NAME,
                Key=f"notices/{file_name}",
                Body=bytes_payload,
                ContentType="application/pdf"
            )
            cloudflare_permanent_url = f"{CLOUDFLARE_PUBLIC_BASE_URL.rstrip('/')}/notices/{file_name}"
            print(f"✅ R2 URL: {cloudflare_permanent_url}")
        except Exception as e:
            print(f"❌ Cloudflare Upload Error: {e}")
            continue

        # Step 3: Firestore Synchronization
        print("⚡ Saving data to Firestore...")
        live_entry_date = datetime.now().strftime("%d-%m-%Y")
        try:
            doc_ref.set({
                "id": doc_id,
                "title": extracted_title,
                "date": live_entry_date,  
                "originalWebsiteDate": live_entry_date,  
                "fileName": file_name,
                "department": "UPMSP Board Office (Manual)",
                "serverFileUrl": cloudflare_permanent_url,
                "summary": ai_summary, 
                "isWebpage": False,
                "isPdf": True,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
            print(f"✅ SUCCESS: Saved Firestore node successfully for [{doc_id}]")
            
            # Send Notification
            send_fcm_push_notification(extracted_title)
            
        except Exception as e:
            print(f"❌ Database Sync Crash: {e}")

if __name__ == "__main__":
    run_manual_pipeline()
