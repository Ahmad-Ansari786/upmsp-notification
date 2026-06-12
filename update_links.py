import os
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import time

print("Connecting to Firebase...")

# GitHub Secrets se JSON string fetch karne ki koshish karein
firebase_secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY")

if firebase_secret:
    # 🌟 Tarika 1: GitHub Actions ke through chal raha hai (Secure)
    print("Using GitHub Secrets for Authentication...")
    cred_dict = json.loads(firebase_secret)
    cred = credentials.Certificate(cred_dict)
else:
    # 💻 Tarika 2: Aapke local laptop par chal raha hai
    print("Using Local JSON file for Authentication...")
    cred = credentials.Certificate("serviceAccountKey.json")

# Firebase initialize karein
firebase_admin.initialize_app(cred)
db = firestore.client()

current_timestamp = int(time.time() * 1000)

# Aapke Quick Links ka data
quick_links_data = [
    {
        "id": "10th_result", 
        "title": "10th Result",
        "webUrl": "https://upresults.nic.in/",
        "iconEmoji": "🎓"
    },
    {
        "id": "12th_result",
        "title": "12th Result",
        "webUrl": "https://upmsp.edu.in/Result/ResultIntermediate.aspx",
        "iconEmoji": "📜"
    },
    {
        "id": "search_roll_no",
        "title": "Search Roll No",
        "webUrl": "https://upmsp.edu.in/SearchRollNumber.aspx",
        "iconEmoji": "🔍"
    }
]

print("Updating Quick Links in Firestore...")

for link in quick_links_data:
    doc_id = link.pop("id") 
    link["timestamp"] = current_timestamp 
    
    db.collection("quick_links").document(doc_id).set(link)
    print(f"✅ Updated: {link['title']}")

print("🎉 Saare Quick Links successfully update ho gaye!")