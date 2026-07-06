import os
import time
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# =====================================================================
# ⚙️ CONFIGURATION
# =====================================================================
# Apna Gemini API Key yahan set karein 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YAHAN_APNI_API_KEY_DAALEIN_AGAR_ENV_NAHI_HAI")
FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"

if not GEMINI_API_KEY or GEMINI_API_KEY == "YAHAN_APNI_API_KEY_DAALEIN_AGAR_ENV_NAHI_HAI":
    print("❌ Error: Gemini API Key set nahi hai!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# Firebase Initialize
if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
    print(f"❌ Error: '{FIREBASE_SERVICE_ACCOUNT_JSON}' file nahi mili!")
    exit(1)

print("🔄 Connecting to Firebase...")
cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()
collection_ref = db.collection("live_notices")

# =====================================================================
# 🛠️ HELPER FUNCTIONS
# =====================================================================
def get_smart_content_type(extension):
    types_map = {
        'pdf': 'application/pdf',
        'jpeg': 'image/jpeg',
        'jpg': 'image/jpeg',
        'png': 'image/png'
    }
    return types_map.get(extension.lower(), 'application/pdf')

def generate_ai_summary(bytes_payload, mime_type, title):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        prompt = (
            f"Notice Title: '{title}'\n"
            "Task: Please read the entire attached document thoroughly from start to finish. "
            "Carefully analyze all the pages, extract key information such as important dates, deadlines, "
            "rules, and the main purpose of the notice. "
            "After reading the complete document, provide a clear, highly accurate, and easy-to-understand "
            "2-3 line summary in Hindi or English."
        )
        
        if mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
            response = model.generate_content([
                prompt,
                {"mime_type": mime_type, "data": bytes_payload}
            ])
            return response.text.strip()
        else:
            return "Document format not supported for AI summary."
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        return None

# =====================================================================
# 🚀 MAIN BACKFILL PROCESS
# =====================================================================
def run_backfill():
    # 🌟 BADLAAV YAHAN HAI: Sirf wahi fetch karo jinka department UPMSP Board Office hai
    print("\n🔍 Fetching 'UPMSP Board Office' documents from Firestore...")
    
    # Firestore Query Filter
    docs = collection_ref.where("department", "==", "UPMSP Board Office").stream()
    
    updated_count = 0
    skipped_count = 0
    
    for doc in docs:
        doc_data = doc.to_dict()
        doc_id = doc.id
        title = doc_data.get("title", "Unknown Title")
        is_webpage = doc_data.get("isWebpage", False)
        file_url = doc_data.get("serverFileUrl", "")
        
        # 1. Check karo agar summary pehle se hai toh skip kar do
        if "summary" in doc_data:
            skipped_count += 1
            continue
            
        print("-" * 50)
        print(f"📄 Processing: {title[:50]}...")
        
        # 2. Webpage Handle Karo
        if is_webpage:
            print("🌐 Webpage detected, setting default summary...")
            collection_ref.document(doc_id).update({
                "summary": "Portal link notice - please visit the portal for full details."
            })
            updated_count += 1
            continue
            
        # 3. File Download Karo
        if not file_url:
            print("⚠️ Koi file URL nahi mila, skipping...")
            continue
            
        print(f"📥 Downloading file from: {file_url}")
        try:
            response = requests.get(file_url, timeout=20)
            if response.status_code != 200:
                print(f"⚠️ Download failed with status {response.status_code}")
                continue
                
            bytes_payload = response.content
            ext = file_url.split('.')[-1].lower() if '.' in file_url else 'pdf'
            mime_type = get_smart_content_type(ext)
            
            print("🧠 Generating AI Summary (This will take a few seconds)...")
            ai_summary = generate_ai_summary(bytes_payload, mime_type, title)
            
            if ai_summary:
                print("✅ Summary Generated! Updating Firestore...")
                collection_ref.document(doc_id).update({
                    "summary": ai_summary
                })
                updated_count += 1
                
                # 🛑 RATE LIMIT PROTECTION
                print("⏳ Sleeping for 35 seconds to respect Gemini Free Tier limits...")
                time.sleep(35)
            else:
                print("❌ Failed to generate summary for this document.")
                
        except Exception as e:
            print(f"❌ Error processing document {doc_id}: {e}")

    print("\n" + "=" * 50)
    print(f"🏁 BACKFILL COMPLETE | Updated: {updated_count} | Skipped (Already Had Summary): {skipped_count}")
    print("=" * 50)

if __name__ == "__main__":
    run_backfill()
