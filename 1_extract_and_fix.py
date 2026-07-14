import os
import time
import json
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# ==========================================
# 1. SETUP (Apni API Key yahan daalein)
# ==========================================
GEMINI_API_KEY = "AQ.Ab8RN6IU2JiEGgN57q0rJ52GZr8H4ttCipq0u3WFdUxgpM9eHQ" 
genai.configure(api_key=GEMINI_API_KEY)

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def get_mime_type(url):
    ext = url.split('?')[0].split('.')[-1].lower()
    return 'application/pdf' if ext == 'pdf' else 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png' if ext == 'png' else 'application/octet-stream'

def run_offline_extractor():
    print("🔍 Scanning Firestore for old documents...\n")
    docs = db.collection("live_notices").stream()
    
    offline_backup = []

    for doc in docs:
        data = doc.to_dict()
        doc_id = doc.id
        title = data.get("title", "Unknown")
        file_url = data.get("serverFileUrl", "")
        is_webpage = data.get("isWebpage", False)
        
        # Ek dictionary banayenge jisme sirf wo field hongi jo update karni hain
        updates_needed = {"id": doc_id}
        needs_ai = False

        # 🎯 Condition 1: Check for missing AI data
        if (not data.get("fullText") or "पोर्टल लिंक" in data.get("summary", "")) and not is_webpage and file_url.startswith("http"):
            needs_ai = True

        # 🎯 Condition 2: Check for missing ts_epoch (Dual-Field Strategy)
        if "ts_epoch" not in data:
            old_ts = data.get("timestamp")
            try:
                # Purane timestamp ko Typesense ke liye Epoch (Integer) me convert karo
                updates_needed["ts_epoch"] = int(old_ts.timestamp() * 1000)
            except:
                updates_needed["ts_epoch"] = int(time.time() * 1000)

        # 🤖 AI Processing (Agar zaroorat hai toh)
        if needs_ai:
            print(f"📥 AI Processing for [{doc_id}]: {title[:30]}...")
            try:
                response = requests.get(file_url, timeout=20)
                if response.status_code == 200:
                    model = genai.GenerativeModel('gemini-3.1-flash-lite')
                    prompt = (
                              f"Notice Title: '{title}'\n"
                              "Task: You are a strict data extraction API. Read the attached document and extract the information into a VALID JSON format.\n\n"
                              "JSON Structure Requirements:\n"
                              "1. 'summary': A 5 to 6 line summary in Hindi (use bullet points if necessary).\n"
                              "2. 'englishSummary': A 5 to 6 line summary in English.\n"
                              "3. 'search_keywords': An array of 10 to 15 relevant search keywords (include dates, numbers, and Hinglish terms).\n"
                              "4. 'fullText': The complete OCR text of the document. YOU MUST properly escape all double quotes (\"), backslashes (\\), and newlines (\\n) in this text to ensure the JSON remains valid.\n\n"
                              "STRICT RULES:\n"
                              "- Return ONLY the raw JSON object.\n"
                              "- Output must start exactly with { and end with }.\n"
                              "- DO NOT include markdown code blocks like ```json or ```.\n"
                               "- DO NOT add any conversational text before or after the JSON."
                            )
                    
                    ai_response = model.generate_content([prompt, {"mime_type": get_mime_type(file_url), "data": response.content}])
                    
                    # Clean JSON response
                    raw_text = ai_response.text.strip()
                    if raw_text.startswith("```json"): raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                    elif raw_text.startswith("```"): raw_text = raw_text.replace("```", "").strip()
                    
                    ai_data = json.loads(raw_text)
                    
                    updates_needed["fullText"] = ai_data.get("fullText", "")
                    updates_needed["summary"] = ai_data.get("summary", "")
                    updates_needed["englishSummary"] = ai_data.get("englishSummary", "")
                    updates_needed["search_keywords"] = ai_data.get("search_keywords", [])
                    
                time.sleep(3) # API Limit protection (3 second delay)
            except Exception as e:
                print(f"   ❌ Error: {e}")

        # 💾 Agar doc me kuch bhi naya mila hai, toh list me save kar lo
        if len(updates_needed) > 1:
            offline_backup.append(updates_needed)
            print(f"   ✅ Fixed data stored locally. (Total Ready: {len(offline_backup)})")

    # Aakhiri me saara data ek local file me Dump kar do
    print("\n💾 Saving all extracted data to local file...")
    with open("fixed_notices_backup.json", "w", encoding="utf-8") as f:
        json.dump(offline_backup, f, ensure_ascii=False, indent=4)
        
    print(f"\n🏁 EXTRACTION COMPLETE! {len(offline_backup)} notices fixed and saved in 'fixed_notices_backup.json'.")

if __name__ == "__main__":
    run_offline_extractor()
