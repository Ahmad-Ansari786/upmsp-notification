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
# 1. CONFIGURATION
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ ERROR: GEMINI_API_KEY environment variable missing!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# Firebase setup
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
            return file_bytes
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
    print("🔍 Scanning Firestore for documents...\n")
    # .get() ka istemal kiya taaki stream error na aaye
    docs = db.collection("live_notices").get()
    
    offline_backup = []
    count = 0

    for doc in docs:
        count += 1
        data = doc.to_dict()
        doc_id = doc.id
        title = data.get("title", "Unknown")
        file_url = data.get("serverFileUrl", "")
        is_webpage = data.get("isWebpage", False)
        
        full_doc_data = data.copy()
        full_doc_data["id"] = doc_id 
        
        needs_ai = False
        if (not data.get("fullText") or "पोर्टल लिंक" in data.get("summary", "")) and not is_webpage and file_url.startswith("http"):
            needs_ai = True

        if "ts_epoch" not in full_doc_data:
            old_ts = data.get("timestamp")
            try:
                full_doc_data["ts_epoch"] = int(old_ts.timestamp() * 1000)
            except:
                full_doc_data["ts_epoch"] = int(time.time() * 1000)

        # 🤖 AI Processing
        if needs_ai:
            print(f"[{count}] 📥 AI Processing: {title[:30]}...")
            try:
                response = requests.get(file_url, timeout=20)
                if response.status_code == 200:
                    file_content = response.content
                    if get_mime_type(file_url) == 'application/pdf':
                        file_content = limit_pdf_pages(file_content, max_pages=20)

                    model = genai.GenerativeModel('gemini-3.1-flash-lite')
                    
                    # 🛡️ THE DETAILED PROMPT IS BACK
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
                    
                    ai_response = model.generate_content([prompt, {"mime_type": get_mime_type(file_url), "data": file_content}])
                    
                    raw_text = ai_response.text.strip()
                    # JSON fix logic
                    if raw_text.startswith("```"): raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                    
                    ai_data = json.loads(raw_text)
                    full_doc_data.update(ai_data)
                    print(f"   ✅ AI data added.")
                
                time.sleep(5) 
            except Exception as e:
                print(f"   ❌ Error processing {doc_id}: {e}")

        offline_backup.append(full_doc_data)

    # 💾 Save to JSON
    with open("fixed_notices_backup.json", "w", encoding="utf-8") as f:
        json.dump(offline_backup, f, ensure_ascii=False, indent=4)
        
    print(f"\n🏁 EXTRACTION COMPLETE! Saved to 'fixed_notices_backup.json'.")

if __name__ == "__main__":
    run_offline_extractor()
