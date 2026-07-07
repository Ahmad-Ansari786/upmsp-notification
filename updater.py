import os
import re
import sys
import hashlib
import requests
from bs4 import BeautifulSoup
import boto3
from botocore.exceptions import NoCredentialsError
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import google.generativeai as genai  # 🌟 ADDED: Google AI SDK

# =====================================================================
# ⚙️ FULL CONFIGURATION BLOCK (GitHub Secrets Encryption Layer)
# =====================================================================
CLOUDFLARE_ACCESS_KEY = os.environ.get("CF_ACCESS_KEY")
CLOUDFLARE_SECRET_KEY = os.environ.get("CF_SECRET_KEY")
CLOUDFLARE_ENDPOINT = os.environ.get("CF_ENDPOINT")
CLOUDFLARE_PUBLIC_BASE_URL = os.environ.get("CF_PUBLIC_URL")
CLOUDFLARE_BUCKET_NAME = os.environ.get("CF_BUCKET_NAME")

# 🌟 ADDED: Gemini API Key Setup
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

FIREBASE_SERVICE_ACCOUNT_JSON = "serviceAccountKey.json"

# =====================================================================
# 🚀 CHANNELS INITIALIZATION (Core Connectivity Engine)
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
# 🤖 GOOGLE AI (GEMINI) SUMMARY GENERATOR
# =====================================================================
def generate_ai_summary(bytes_payload, mime_type, title):
    """
    🎯 IN-MEMORY SUMMARY: Bina Cloudflare Class B use kiye summary nikalna.
    """
    if not GEMINI_API_KEY:
        return "AI Summary unavailable (No API Key)"
        
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        prompt = (
            f"Notice Title: '{title}'\n"
            "Task: Please read the entire attached document thoroughly from start to finish. "
            "Carefully analyze all the pages, extract key information such as important dates, deadlines, "
            "rules, and the main purpose of the notice. "
            "After reading the complete document, provide a clear, highly accurate, and easy-to-understand "
            "4-5 line (bullet point) summary in Hindi(script also).If more important then more lines also."
        )
        # Supported formats for inline data in Gemini
        if mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
            response = model.generate_content([
                prompt,
                {"mime_type": mime_type, "data": bytes_payload}
            ])
            return response.text.strip()
        else:
            return "Document format not supported for direct AI summary."
    except Exception as e:
        print(f"⚠️ Google AI Summary Error: {e}")
        return "Summary generation failed."

# =====================================================================
# 🛠️ HELPER PARSING FUNCTIONS (Data Security & Formatting)
# =====================================================================
def clean_document_id(file_name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', file_name.replace(".pdf", ""))
    if len(safe_name) > 50:
        hash_suffix = hashlib.md5(file_name.encode()).hexdigest()[:6]
        return f"{safe_name[:40]}_{hash_suffix}"
    return safe_name

def get_smart_content_type(extension):
    types_map = {
        'pdf': 'application/pdf',
        'jpeg': 'image/jpeg',
        'jpg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc': 'application/msword',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'xls': 'application/vnd.ms-excel',
        'zip': 'application/zip',
        'rar': 'application/x-rar-compressed'
    }
    return types_map.get(extension.lower(), 'application/octet-stream')

def send_fcm_push_notification(notice_title, is_webpage_link):
    """
    🎯 REALTIME ALERTS TRANSMITTER: Notification ko stylish banakar bacho ke phone par bhejna
    """
    try:
        display_title = "📢 UPMSP BOARD ALERT!"
        
        if is_webpage_link:
            display_body = f"🔗 New Portal Link Open:\n{notice_title}"
        else:
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
        print(f"📢 STYLISH PUSH NOTIFICATION SENT SUCCESSFULLY -> Token ID: {response}")
    except Exception as n_err:
        print(f"⚠️ Notification Dispatch System Error: {n_err}")
        

# =====================================================================
# 🎯 MAIN EXTRACTION ENGINE (Universal Link & Multi-File Handler)
# =====================================================================
def run_upmsp_pipeline():
    print(f"\n🌐 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connecting to official UPMSP Notice Portal...")
    portal_url = "https://upmsp.edu.in/"  
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(portal_url, headers=headers, timeout=20)
        if response.status_code != 200:
            print(f"❌ Portal Down: HTTP Status {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    all_notice_rows = soup.find_all('tr', class_='trspace')
    print(f"🔍 Found {len(all_notice_rows)} verified notice row nodes in table layout. Processing...")

    success_count = 0
    skip_count = 0

    for row in all_notice_rows:
        cells = row.find_all('td')
        if len(cells) < 4:
            continue  
            
        raw_title = cells[1].get_text(separator=" ").strip()
        # Clean title: Removes extra spaces and the "new" text logo
        cleaned_title = re.sub(r'\s+', ' ', raw_title).replace("Download", "").replace("new", "").strip()
        
        original_website_date = cells[2].get_text().strip()
        if not original_website_date:
            original_website_date = datetime.now().strftime("%d-%m-%Y")

        # 🌟 SMART LINK EXTRACTOR: Handling both Download Column and Description Links
        links_to_process = []

        download_anchor = cells[3].find('a', href=True)
        if download_anchor:
            # Agar normal download column me link hai
            links_to_process.append({
                "title": cleaned_title,
                "url": download_anchor['href'].strip()
            })
        else:
            # Agar download column khali hai, toh description column me links dhoondho
            description_anchors = cells[1].find_all('a', href=True)
            if description_anchors:
                for anchor in description_anchors:
                    sub_link_text = anchor.get_text().strip()
                    specific_title = f"{cleaned_title.split('।')[0].strip()} - {sub_link_text}"
                    links_to_process.append({
                        "title": specific_title,
                        "url": anchor['href'].strip()
                    })
            else:
                continue # Koi link nahi mila, is row ko skip karo

        # Ab un saare links ko ek-ek karke process karenge
        for link_data in links_to_process:
            href_link = link_data["url"]
            final_title = link_data["title"]

            link_parts = href_link.split('?')[0].split('/')[-1].split('.')
            link_extension = link_parts[-1].lower() if len(link_parts) > 1 else ""

            known_file_extensions = ['pdf', 'jpeg', 'jpg', 'png', 'gif', 'docx', 'doc', 'xlsx', 'xls', 'zip', 'rar']
            is_webpage_link = ".aspx" in href_link.lower() or link_extension not in known_file_extensions

            if href_link.startswith('http://') or href_link.startswith('https://'):
                target_url = href_link
            else:
                clean_path = href_link.lstrip('./')
                target_url = "https://upmsp.edu.in/" + clean_path

            file_name = target_url.split('/')[-1] if not is_webpage_link else "portal_link.pdf"
            
            if is_webpage_link:
                doc_id = clean_document_id(hashlib.md5(target_url.encode()).hexdigest()[:12])
            else:
                doc_id = clean_document_id(file_name)

            try:
                doc_ref = firestore_collection.document(doc_id)
                doc_snapshot = doc_ref.get()
                if doc_snapshot.exists:
                    skip_count += 1
                    continue
            except Exception as err:
                print(f"⚠️ Registry read error for [{doc_id}]: {err}")
                continue

            live_entry_date = datetime.now().strftime("%d-%m-%Y")

            print("-" * 50)
            print(f"📋 New Entry Match: {final_title[:50]}...")
            print(f"📅 Website Date: {original_website_date} | ⚡ Entry Date: {live_entry_date}")
            print(f"🔗 Target Link Type: {'WEBPORTAL' if is_webpage_link else f'FILE ({link_extension.upper()})'}")
            
            cloudflare_permanent_url = target_url
            ai_summary_text = "Portal link notice - please visit the portal for full details."

            if not is_webpage_link:
                print(f"📥 Streaming file bytes from UPMSP for [{file_name}]...")
                try:
                    file_response = requests.get(target_url, headers=headers, timeout=15)
                    if file_response.status_code != 200:
                        print(f"⚠️ File Stream failed ({file_response.status_code}). Skipping...")
                        continue
                        
                    bytes_payload = file_response.content
                    content_type_header = get_smart_content_type(link_extension)

                    print("🧠 Generating Google AI Summary...")
                    ai_summary_text = generate_ai_summary(bytes_payload, content_type_header, final_title)

                    print(f"☁️ Pushing binary data to Cloudflare R2 [Mime: {content_type_header}]...")
                    r2_client.put_object(
                        Bucket=CLOUDFLARE_BUCKET_NAME,
                        Key=f"notices/{file_name}",
                        Body=bytes_payload,
                        ContentType=content_type_header
                    )
                    cloudflare_permanent_url = f"{CLOUDFLARE_PUBLIC_BASE_URL.rstrip('/')}/notices/{file_name}"
                    print(f"✅ R2 Permanent Backup URL: {cloudflare_permanent_url}")

                except NoCredentialsError:
                    print("❌ Invalid Cloudflare API Credentials! Stopping pipeline execution.")
                    return
                except Exception as e:
                    print(f"❌ R2 Upload execution error: {e}")
                    continue
            else:
                print("🌐 [Webportal Detected] Cloudflare upload & AI Summary skipped. Directing link to Firestore...")

            print("⚡ Synchronizing Firestore Realtime Nodes...")
            try:
                is_pdf_file = (link_extension == 'pdf')
                doc_ref.set({
                    "id": doc_id,
                    "title": final_title,
                    "date": live_entry_date,  
                    "originalWebsiteDate": original_website_date,  
                    "fileName": file_name,
                    "department": "UPMSP Board Office",
                    "serverFileUrl": cloudflare_permanent_url,
                    "summary": ai_summary_text, 
                    "isWebpage": is_webpage_link,
                    "isPdf": is_pdf_file,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })
                print(f"✅ SUCCESS: Complete Sync Saved for [{doc_id}]")
                send_fcm_push_notification(final_title, is_webpage_link)
                success_count += 1
            except Exception as e:
                print(f"❌ Database Transaction Crash: {e}")

    print("\n" + "=" * 50)
    print(f"🏁 CYCLE COMPLETE | New Pushed: {success_count} | Duplicates Bypassed: {skip_count}")
    print("=" * 50)

if __name__ == "__main__":
    run_upmsp_pipeline()
