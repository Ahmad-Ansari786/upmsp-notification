import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# GitHub Secrets se credentials load karein
service_account_info = json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT_JSON'])
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred)

db = firestore.client()

def update_daily_views():
    # 'live_notices' collection ko reference karein
    docs = db.collection('live_notices').stream()
    batch = db.batch()
    
    count = 0
    for doc in docs:
        data = doc.to_dict()
        # Sirf un documents ko update karein jinme 'viewCount' hai
        if 'viewCount' in data:
            # viewCount ki value ko viewCountDaily mein copy karein
            batch.update(doc.reference, {
                'viewCountDaily': data['viewCount']
            })
            count += 1
            
    if count > 0:
        batch.commit()
        print(f"Successfully updated {count} documents.")
    else:
        print("No documents with 'viewCount' found.")

if __name__ == "__main__":
    update_daily_views()

