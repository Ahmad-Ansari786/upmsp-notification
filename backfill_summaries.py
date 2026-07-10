import os
import time
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# =====================================================================
# ⚙️ CONFIGURATION
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YAHAN_APNI_API_KEY_DAALEIN_AGAR_ENV_NAHI_HAI")
FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"

if not GEMINI_API_KEY or GEMINI_API_KEY == "YAHAN_APNI_API_KEY_DAALEIN_AGAR_ENV_NAHI_HAI":
    print("❌ Error: Gemini API Key set nahi hai!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

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

def generate_ai_data(bytes_payload, mime_type, title):
    try:
        model = genai.GenerativeModel('gemini-3.1-flash-lite')
        
        prompt = (
            f"Notice Title: '{title}'\n"
            "Task: Please read the entire attached document thoroughly.\n"
            "1. Provide a clear, highly accurate, and easy-to-understand 5-6 line (bullet point) summary in Hindi (script also).\n"
            "2. Extract 5-10 search keywords for this notice. Include Roman Hindi (Hinglish) and English terms.\n"
            "IMPORTANT: Output exactly in the format below without any extra text.\n\n"
            "SUMMARY:\n"
            "[Your bullet points here]\n"
            "KEYWORDS:\n"
            "[comma-separated words here]"
        )
        
        if mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
            response = model.generate_content([
                prompt,
                {"mime_type": mime_type, "data": bytes_payload}
            ])
            
            raw_text = response.text.strip()
            
            summary_text = raw_text
            keywords_list = []
            
            if "KEYWORDS:" in raw_text:
                parts = raw_text.split("KEYWORDS:")
                summary_text = parts[0].replace("SUMMARY:", "").strip()
                
                raw_keywords = parts[1].strip()
                keywords_list = [k.strip().lower().strip('.') for k in raw_keywords.split(",") if k.strip()]
                
            return summary_text, keywords_list
        else:
            return "Document format not supported for AI summary.", []
            
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        return None, []

# =====================================================================
# 🚀 MAIN BACKFILL PROCESS
# =====================================================================
def run_backfill():
    print("\n🔍 Fetching up to 200 documents from Firestore...")
    
    # 🌟 BADLAAV: Yahan .limit(200) lagaya gaya hai
    docs = collection_ref.limit(200).stream()
    
    updated_count = 0
    skipped_count = 0
    
    for doc in docs:
        doc_data = doc.to_dict()
        doc_id = doc.id
        title = doc_data.get("title", "Unknown Title")
        is_webpage = doc_data.get("isWebpage", False)
        file_url = doc_data.get("serverFileUrl", "")
        
        if "summary" in doc_data and "search_keywords" in doc_data:
            skipped_count += 1
            continue
            
        print("-" * 50)
        print(f"📄 Processing: {title[:50]}...")
        
        if is_webpage:
            print("🌐 Webpage detected, setting default summary and empty keywords...")
            collection_ref.document(doc_id).update({
                "summary": "Portal link notice - please visit the portal for full details.",
                "search_keywords": ["portal", "link", "notice"]
            })
            updated_count += 1
            continue
            
        if not file_url:
            print("⚠️ Koi file URL nahi mila, skipping...")
            continue
            
        print(f"📥 Downloading file from: {file_url}")
        try:
            response = requests.get(file_url, timeout=120)
            if response.status_code != 200:
                print(f"⚠️ Download failed with status {response.status_code}")
                continue
                
            bytes_payload = response.content
            ext = file_url.split('.')[-1].lower() if '.' in file_url else 'pdf'
            mime_type = get_smart_content_type(ext)
            
            print("🧠 Generating AI Summary & Keywords (Hinglish + English)...")
            ai_summary, ai_keywords = generate_ai_data(bytes_payload, mime_type, title)
            
            if ai_summary:
                print(f"✅ Generated! Found {len(ai_keywords)} keywords. Updating Firestore...")
                collection_ref.document(doc_id).update({
                    "summary": ai_summary,
                    "search_keywords": ai_keywords
                })
                updated_count += 1
                
                print("⏳ Sleeping for 5 seconds...")
                time.sleep(5)
            else:
                print("❌ Failed to generate data for this document.")
                
        except Exception as e:
            print(f"❌ Error processing document {doc_id}: {e}")

    print("\n" + "=" * 50)
    print(f"🏁 BATCH COMPLETE | Updated: {updated_count} | Skipped: {skipped_count}")
    print("=" * 50)

if __name__ == "__main__":
    run_backfill()
