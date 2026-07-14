import os
import time
import json
import requests
import io
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
from pypdf import PdfReader, PdfWriter

# ==========================================
# 1. CONFIGURATION (GitHub Secrets se uthayega)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ ERROR: GEMINI_API_KEY environment variable missing!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# Firebase setup (Local chalane ke liye serviceAccountKey.json rakhein)
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def get_mime_type(url):
    ext = url.split('?')[0].split('.')[-1].lower()
    return 'application/pdf' if ext == 'pdf' else 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png' if ext == 'png' else 'application/octet-stream'

def limit_pdf_pages(file_bytes, max_pages=20):
    """PDF ko kaat kar sirf max_pages tak rakhega"""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if len(reader.pages) <= max_pages:
            return file_bytes # Choti PDF hai, sab process karo

        print(f"   ✂️ PDF badi hai ({len(reader.pages)} pages). Sirf 20 pages process kar rahe hain...")
        writer = PdfWriter()
        for i in range(max_pages):
            writer.add_page(reader.pages[i])

        output_buffer = io.BytesIO()
        writer.write(output_buffer)
        return output_buffer.getvalue()
    except Exception as e:
        print(f"   ⚠️ PDF splitting error: {e}. Original file bhej rahe hain.")
        return file_bytes

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
        
        # Pura document backup ke liye copy kiya
        full_doc_data = data.copy()
        full_doc_data["id"] = doc_id 
        
        needs_ai = False

        # 🎯 Condition 1: AI Data missing check
        if (not data.get("fullText") or "पोर्टल लिंक" in data.get("summary", "")) and not is_webpage and file_url.startswith("http"):
            needs_ai = True

        # 🎯 Condition 2: ts_epoch check (Dual-Field Strategy)
        if "ts_epoch" not in full_doc_data:
            old_ts = data.get("timestamp")
            try:
                full_doc_data["ts_epoch"] = int(old_ts.timestamp() * 1000)
            except:
                full_doc_data["ts_epoch"] = int(time.time() * 1000)

        # 🤖 AI Processing
        if needs_ai:
            print(f"📥 AI Processing for [{doc_id}]: {title[:30]}...")
            try:
                response = requests.get(file_url, timeout=20)
                if response.status_code == 200:
                    
                    file_content = response.content
                    # PDF limiter
                    if get_mime_type(file_url) == 'application/pdf':
                        file_content = limit_pdf_pages(file_content, max_pages=20)

                    model = genai.GenerativeModel('gemini-3.1-flash-lite')
                    prompt = (
                        f"Notice Title: '{title}'\n"
                        "Task: You are a strict data extraction API. Read the attached document and extract the information into a VALID JSON format.\n\n"
                        "JSON Structure Requirements:\n"
                        "1. 'summary': A 5 to 6 line summary in Hindi.\n"
                        "2. 'englishSummary': A 5 to 6 line summary in English.\n"
                        "3. 'search_keywords': An array of 10 to 15 relevant search keywords.\n"
                        "4. 'fullText': The complete OCR text. Escape all quotes and newlines.\n\n"
                        "STRICT RULES: Return ONLY raw JSON. No markdown, no conversation."
                    )
                    
                    ai_response = model.generate_content([prompt, {"mime_type": get_mime_type(file_url), "data": file_content}])
                    
                    raw_text = ai_response.text.strip()
                    if raw_text.startswith("```"): raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                    
                    ai_data = json.loads(raw_text)
                    
                    full_doc_data["fullText"] = ai_data.get("fullText", "")
                    full_doc_data["summary"] = ai_data.get("summary", "")
                    full_doc_data["englishSummary"] = ai_data.get("englishSummary", "")
                    full_doc_data["search_keywords"] = ai_data.get("search_keywords", [])
                    print(f"   ✅ AI data added.")
                
                time.sleep(5) 
            except Exception as e:
                print(f"   ❌ Error processing {doc_id}: {e}")

        # 💾 Backup full record
        offline_backup.append(full_doc_data)

    # 💾 Save to JSON
    with open("fixed_notices_backup.json", "w", encoding="utf-8") as f:
        json.dump(offline_backup, f, ensure_ascii=False, indent=4)
        
    print(f"\n🏁 EXTRACTION COMPLETE! Saved to 'fixed_notices_backup.json'.")

if __name__ == "__main__":
    run_offline_extractor()
