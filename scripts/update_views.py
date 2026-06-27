import firebase_admin
from firebase_admin import credentials, firestore
import os

# Initialize Firestore
cred = credentials.Certificate("serviceAccountKey.json") # Ye wahi file hai jo aapke repo mein hai
firebase_admin.initialize_app(cred)
db = firestore.client()

def reset_and_sync_views():
    # 'live_notices' collection ko read karein
    docs = db.collection("live_notices").stream()
    batch = db.batch()
    
    for doc in docs:
        data = doc.to_dict()
        # Sirf tab update karein agar 'viewCount' exist karta hai
        if 'viewCount' in data:
            batch.update(doc.reference, {
                'viewCountDaily': data['viewCount']
            })
            
    batch.commit()
    print("✅ Daily view counts synchronized successfully.")

if __name__ == "__main__":
    reset_and_sync_views()
