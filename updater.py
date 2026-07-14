import os
import re
import sys
import time
import json
import hashlib
import requests
from bs4 import BeautifulSoup
import boto3
from botocore.exceptions import NoCredentialsError
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import google.generativeai as genai

# =====================================================================
# ⚙️ FULL CONFIGURATION BLOCK (GitHub Secrets Layer)
# =====================================================================
CLOUDFLARE_ACCESS_KEY = os.environ.get("CF_ACCESS_KEY", "").strip()
CLOUDFLARE_SECRET_KEY = os.environ.get("CF_SECRET_KEY", "").strip()
CLOUDFLARE_ENDPOINT = os.environ.get("CF_ENDPOINT", "").strip()
CLOUDFLARE_PUBLIC_BASE_URL = os.environ.get("CF_PUBLIC_URL", "").strip()
CLOUDFLARE_BUCKET_NAME = os.environ.get("CF_BUCKET_NAME", "").strip()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"

# =====================================================================
# 🚀 INITIALIZATION (Database & Cloud Storage)
# =====================================================================
print("🔄 Initializing cloud connections...")

if not os.path.exists(FIREBASE_SERVICE_ACCOUNT_JSON):
    print(f"❌ Error: '{FIREBASE_SERVICE_ACCOUNT_JSON}' file nahi mili! Automation workflow aborted.")
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
# 🧠 THE MASTER AI ENGINE (Gemini 1.5 Flash - OCR + Summary)
# =====================================================================
def generate_ai_metadata(bytes_payload, mime_type, title):
    """Gemini se JSON format me Summary, Keywords aur Full Text (OCR) lena"""
    default_response = {
        "summary": "पोर्टल लिंक या नोटिस के लिए कृपया आधिकारिक वेबसाइट देखें।",
        "englishSummary": "Please visit the official portal for detailed information regarding this notice.",
        "search_keywords": ["notice", "upmsp", "update"],
        "fullText": ""
    }
    
    if not GEMINI_API_KEY:
        return default_response
        
    try:
        model = genai.GenerativeModel('gemini-3.1-flash-lite')
        
        # 🎯 PROMPT: Gemini ko sab kuch ek sath karne ka order
        prompt = (
            f"Notice Title: '{title}'\n"
            "Task: Read the attached document carefully and extract key information. "
            "You MUST return the output ONLY as a valid JSON object with EXACTLY these four keys:\n"
            "1. 'summary': A 4-5 line bulleted summary in Hindi.\n"
            "2. 'englishSummary': A 4-5 line bulleted summary in English.\n"
            "3. 'search_keywords': An array of 10-15 highly relevant keywords (include hindi/english terms, numbers, dates).\n"
            "4. 'fullText': Extract and transcribe ALL the readable text EXACTLY as written in the document (including all numbers, dates, tables, and names). This is crucial for our search engine.\n"
            "Ensure the text does not break JSON formatting (escape quotes and newlines properly). Do NOT include any markdown formatting like ```json in your response."
        )
        
        if mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
            response = model.generate_content([
                prompt,
                {"mime_type": mime_type, "data": bytes_payload}
            ])
            
            raw_text = response.text.strip()
            # JSON Formatting Fixer
            if raw_text.startswith("```json"):
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            elif raw_text.startswith("```"):
                raw_text = raw_text.replace("```", "").strip()
                
            return json.loads(raw_text)
        else:
            return default_response
            
    except Exception as e:
        print(f"⚠️ Google AI Meta/OCR Error: {e}")
        return default_response

# =====================================================================
# 🛠️ HELPER FUNCTIONS (ID Gen, Routing, Notifications)
# =====================================================================
def clean_document_id(file_name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', file_name.replace(".pdf", ""))
    if len(safe_name) > 50:
        hash_suffix = hashlib.md5(file_name.encode()).hexdigest()[:6]
        return f"{safe_name[:40]}_{hash_suffix}"
    return safe_name

def get_smart_content_type(extension):
    types_map = {'pdf': 'application/pdf', 'jpeg': 'image/jpeg', 'png': 'image/png'}
    return types_map.get(extension.lower(), 'application/octet-stream')

def detect_target_class(title):
    """Title padh kar Class detect karna (Typesense Filter ke liye)"""
    title_lower = title.lower()
    if "हाईस्कूल" in title or "high school" in title_lower or "class 10" in title_lower: return "Class 10"
    elif "इण्टरमीडिएट" in title or "intermediate" in title_lower or "class 12" in title_lower: return "Class 12"
    elif "कक्षा 9" in title or "class 9" in title_lower: return "Class 9"
    elif "कक्षा 11" in title or "class 11" in title_lower: return "Class 11"
    else: return "All"

def send_fcm_push_notification(notice_title, is_webpage_link):
    try:
        display_body = f"🔗 New Portal Link:\n{notice_title}" if is_webpage_link else f"📄 New Document:\n{notice_title}"
        if len(display_body) > 120: display_body = display_body[:117] + "..."

        message = messaging.Message(
            data={'title': "📢 UPMSP BOARD ALERT!", 'body': display_body, 'badge': '1', 'channel_id': 'upmsp_notices_channel'},
            topic="all_users"
        )
        messaging.send(message)
    except Exception as n_err:
        print(f"⚠️ Notification System Error: {n_err}")

# =====================================================================
# 🎯 MAIN SCRAPING & PROCESSING PIPELINE
# =====================================================================
def run_upmsp_pipeline():
    print(f"\n🌐 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connecting to UPMSP Portal...")
    portal_url = "https://upmsp.edu.in/"  
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(portal_url, headers=headers, timeout=20)
        if response.status_code != 200: return
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    all_notice_rows = soup.find_all('tr', class_='trspace')
    
    success_count = 0
    skip_count = 0

    for row in all_notice_rows:
        cells = row.find_all('td')
        if len(cells) < 4: continue  
            
        raw_title = cells[1].get_text(separator=" ").strip()
        cleaned_title = re.sub(r'\s+', ' ', raw_title).replace("Download", "").replace("new", "").strip()
        original_website_date = cells[2].get_text().strip() or datetime.now().strftime("%d-%m-%Y")

        links_to_process = []
        download_anchor = cells[3].find('a', href=True)
        
        if download_anchor:
            links_to_process.append({"title": cleaned_title, "url": download_anchor['href'].strip(), "is_sublink": False})
        else:
            description_anchors = cells[1].find_all('a', href=True)
            if description_anchors:
                cell_html = str(cells[1])
                for anchor in description_anchors:
                    sub_link_text = anchor.get_text().strip()
                    href = anchor['href'].strip()
                    anchor_index = cell_html.find(str(anchor))
                    text_before_link = cell_html[:anchor_index] if anchor_index != -1 else ""

                    category = "[हाईस्कूल] " if max(text_before_link.rfind("हाईस्कूल"), text_before_link.rfind("High School")) > max(text_before_link.rfind("इण्टरमीडिएट"), text_before_link.rfind("Intermediate")) else "[इण्टरमीडिएट] " if max(text_before_link.rfind("इण्टरमीडिएट"), text_before_link.rfind("Intermediate")) > max(text_before_link.rfind("हाईस्कूल"), text_before_link.rfind("High School")) else ""
                    
                    specific_title = f"{cleaned_title.split('।')[0].strip()} - {category}{sub_link_text}"
                    links_to_process.append({"title": specific_title, "url": href, "is_sublink": True})
            else: continue 

        for link_data in links_to_process:
            href_link, final_title, is_sublink = link_data["url"], link_data["title"], link_data.get("is_sublink", False)
            link_extension = href_link.split('?')[0].split('/')[-1].split('.')[-1].lower() if '.' in href_link.split('?')[0].split('/')[-1] else ""
            
            is_webpage_link = ".aspx" in href_link.lower() or link_extension not in ['pdf', 'jpeg', 'jpg', 'png', 'docx', 'xlsx']
            target_url = href_link if href_link.startswith('http') else f"https://upmsp.edu.in/{href_link.lstrip('./')}"
            file_name = target_url.split('/')[-1] if not is_webpage_link else "portal_link.pdf"
            
            if is_webpage_link:
                unique_str = f"{target_url}_{final_title}" if is_sublink else target_url
                doc_id = clean_document_id(hashlib.md5(unique_str.encode()).hexdigest()[:12])
            else:
                doc_id = clean_document_id(file_name)

            try:
                # 🛑 Duplicate Check (Agar Firestore me ID pehle se hai, toh skip kar do)
                if firestore_collection.document(doc_id).get().exists:
                    skip_count += 1
                    continue
            except: continue

            print("-" * 50)
            print(f"📋 New Entry Match: {final_title[:50]}...")
            
            cloudflare_permanent_url = target_url
            # Default Data if Webpage
            ai_data = {"summary": "Portal Link", "englishSummary": "Portal Link", "search_keywords": [], "fullText": ""}

            if not is_webpage_link:
                print(f"📥 Streaming file: {file_name}...")
                try:
                    file_response = requests.get(target_url, headers=headers, timeout=15)
                    if file_response.status_code == 200:
                        bytes_payload = file_response.content
                        content_type_header = get_smart_content_type(link_extension)

                        # 🔥 THE MAGIC: Gemini extracts text, summary, and keywords in one go!
                        print("🧠 Google AI is reading & extracting data...")
                        ai_data = generate_ai_metadata(bytes_payload, content_type_header, final_title)

                        # ☁️ Upload file to Cloudflare Storage
                        r2_client.put_object(Bucket=CLOUDFLARE_BUCKET_NAME, Key=f"notices/{file_name}", Body=bytes_payload, ContentType=content_type_header)
                        cloudflare_permanent_url = f"{CLOUDFLARE_PUBLIC_BASE_URL.rstrip('/')}/notices/{file_name}"
                except Exception as e:
                    print(f"❌ Storage/AI error: {e}")
                    continue

            print("⚡ Synchronizing with Firestore (Typesense Ready)...")
            try:
                current_epoch_ms = int(time.time() * 1000) # Typesense integer sorting rule
                
                firestore_collection.document(doc_id).set({
                    "id": doc_id,
                    "title": final_title,
                    "date": datetime.now().strftime("%d-%m-%Y"),  
                    "originalWebsiteDate": original_website_date,  
                    "fileName": file_name,
                    "department": "Exam", 
                    "targetClass": detect_target_class(final_title),
                    "serverFileUrl": cloudflare_permanent_url,
                    "isWebpage": is_webpage_link,
                    "isPdf": (link_extension == 'pdf'),
                    "isTrade": False,
                    
                    # 📈 Business Metrics (For Typesense Ranking)
                    "viewCount": 0, 
                    "timestamp": current_epoch_ms,
                    
                    # 🚀 Typesense Search Layers
                    "fullText": ai_data.get("fullText", ""),
                    "summary": ai_data.get("summary", ""),
                    "englishSummary": ai_data.get("englishSummary", ""),
                    "search_keywords": ai_data.get("search_keywords", [])
                })
                print(f"✅ SUCCESS: Saved [{doc_id}]")
                send_fcm_push_notification(final_title, is_webpage_link)
                success_count += 1
            except Exception as e:
                print(f"❌ DB Crash: {e}")

    print(f"\n🏁 CYCLE COMPLETE | New: {success_count} | Skipped: {skip_count}")

if __name__ == "__main__":
    run_upmsp_pipeline()
