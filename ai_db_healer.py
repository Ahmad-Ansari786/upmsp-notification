import os
import time
import json
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# =====================================================================
# ⚙️ CONFIGURATION & INITIALIZATION
# =====================================================================
print("🔄 Initializing AI Healer Agent...")

# API Keys setup (Aap apne environment variables ya yahan direct daal sakte hain)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YAHAN_APNI_GEMINI_API_KEY_DAALEIN").strip()
if GEMINI_API_KEY and GEMINI_API_KEY != "YAHAN_APNI_GEMINI_API_KEY_DAALEIN":
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("❌ Gemini API Key missing! AI Healing will fail.")
    exit(1)

FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"
if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
    print(f"❌ Error: '{FIREBASE_SERVICE_ACCOUNT_JSON}' file nahi mili!")
    exit(1)

cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_JSON)
firebase_admin.initialize_app(cred)
db = firestore.client()
collection_ref = db.collection("live_notices")

# =====================================================================
# 🧠 GEMINI AI ENGINE (For reading missing documents)
# =====================================================================
def generate_ai_metadata(bytes_payload, mime_type, title):
    """File ko padhkar Missing Text aur Summary generate karna"""
    try:
        model = genai.GenerativeModel('gemini-3.1-flash-lite')
        prompt = (
            f"Notice Title: '{title}'\n"
            "Task: Read the attached document. "
            "Return ONLY a valid JSON object with EXACTLY these four keys:\n"
            "1. 'summary': A 4-5 line bulleted summary in Hindi.\n"
            "2. 'englishSummary': A 4-5 line bulleted summary in English.\n"
            "3. 'search_keywords': An array of 10-15 keywords (include hindi/english terms, numbers, dates).\n"
            "4. 'fullText': Extract ALL readable text exactly as written (OCR).\n"
            "Do NOT include markdown like ```json."
        )
        
        response = model.generate_content([prompt, {"mime_type": mime_type, "data": bytes_payload}])
        raw_text = response.text.strip()
        
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.replace("```", "").strip()
            
        return json.loads(raw_text)
    except Exception as e:
        print(f"⚠️ Gemini AI Error: {e}")
        return None

def get_mime_type(url):
    ext = url.split('?')[0].split('.')[-1].lower()
    return 'application/pdf' if ext == 'pdf' else 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png' if ext == 'png' else 'application/octet-stream'

# =====================================================================
# 🩺 THE AI HEALER FUNCTION
# =====================================================================
def heal_database_with_ai():
    print("🔍 Scanning 'live_notices' for broken documents requiring AI Healing...\n")
    docs = collection_ref.stream()
    
    ai_fixed_count = 0
    basic_fixed_count = 0
    perfect_count = 0

    for doc in docs:
        data = doc.to_dict()
        updates = {}
        title = data.get("title", "Unknown Notice")
        file_url = data.get("serverFileUrl", "")
        is_webpage = data.get("isWebpage", False)
        
        # 🎯 CONDITION: Kya is document me AI Data missing hai?
        # Agar fullText khali hai ya summary me default text pada hai
        ai_data_missing = (
            not data.get("fullText") or 
            not data.get("summary") or 
            "पोर्टल लिंक" in data.get("summary", "")
        )

        # ==========================================
        # 🤖 AI HEALING PROCESS (File Download & Process)
        # ==========================================
        if ai_data_missing and not is_webpage and file_url.startswith("http"):
            print(f"🛠️ [AI HEALING REQUIRED] Document ID: {doc.id} | Title: {title[:30]}...")
            print(f"   📥 Downloading file from Cloudflare: {file_url.split('/')[-1]}")
            
            try:
                response = requests.get(file_url, timeout=20)
                if response.status_code == 200:
                    mime_type = get_mime_type(file_url)
                    print("   🧠 Running Gemini 1.5 Flash (OCR & Summary)...")
                    
                    ai_result = generate_ai_metadata(response.content, mime_type, title)
                    
                    if ai_result:
                        updates["fullText"] = ai_result.get("fullText", "")
                        updates["summary"] = ai_result.get("summary", "")
                        updates["englishSummary"] = ai_result.get("englishSummary", "")
                        updates["search_keywords"] = ai_result.get("search_keywords", [])
                        ai_fixed_count += 1
                        print("   ✅ AI Extraction Successful!")
                    else:
                        print("   ❌ AI Extraction Failed (Returned None).")
                else:
                    print(f"   ❌ Download Failed (Status: {response.status_code}).")
                
                # Gemini free tier rate limits se bachne ke liye 3 second ka break
                time.sleep(3)
                
            except Exception as e:
                print(f"   ❌ Network Error during AI Healing: {e}")

        # ==========================================
        # 🔧 BASIC FIXES (Jo Webpage hain ya jinme sirf metrics gayab hain)
        # ==========================================
        if is_webpage and ai_data_missing:
            if "summary" not in data or data["summary"] == "":
                updates["summary"] = "पोर्टल लिंक या नोटिस के लिए कृपया आधिकारिक वेबसाइट देखें।"
                updates["englishSummary"] = "Please visit the official portal for detailed information regarding this notice."
                updates["search_keywords"] = ["notice", "upmsp", "update"]
                updates["fullText"] = ""

        if "viewCount" not in data: updates["viewCount"] = 0
        if "department" not in data: updates["department"] = "Exam"
        if "targetClass" not in data: updates["targetClass"] = "All"
        if "isTrade" not in data: updates["isTrade"] = False

        if "timestamp" not in data:
            updates["timestamp"] = int(time.time() * 1000)
        elif not isinstance(data["timestamp"], int):
            try:
                updates["timestamp"] = int(data["timestamp"].timestamp() * 1000)
            except:
                updates["timestamp"] = int(time.time() * 1000)

        # ==========================================
        # 💾 UPLOAD FIXES TO FIRESTORE
        # ==========================================
        if updates:
            doc.reference.update(updates)
            if not ai_data_missing or is_webpage:
                basic_fixed_count += 1
            print(f"💾 Saved Updates for [{doc.id}]: {list(updates.keys())}\n")
        else:
            perfect_count += 1

    print("="*50)
    print(f"🏁 AI DATABASE SCAN & HEAL COMPLETE!")
    print(f"✅ Perfect Documents: {perfect_count}")
    print(f"🤖 AI Recovered & Fixed: {ai_fixed_count}")
    print(f"🔧 Basic Data Fixed: {basic_fixed_count}")
    print("="*50)

if __name__ == "__main__":
    heal_database_with_ai()
