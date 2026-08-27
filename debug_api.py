from fastapi.testclient import TestClient
from app import app
import json

client = TestClient(app)

def test_filters():
    print("--- INITIATING BACKEND VERIFICATION ---")
    try:
        response = client.get("/api/predict/filters")
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print("Response JSON Keys:", list(data.keys()))
            print(f"Categories Count: {len(data.get('categories', []))}")
            print(f"Branches Count: {len(data.get('branches', []))}")
            print(f"Cities Count: {len(data.get('cities', []))}")
            print(f"College Types Count: {len(data.get('college_types', []))}")
            
            # Print sample values
            print("\nSample Categories:", data.get('categories', [])[:5])
            print("Sample Branches:", data.get('branches', [])[:5])
            print("Sample Cities:", data.get('cities', [])[:5])
            
            # Show full response for first 2 categories/branches to verify structure
            print("\nFull JSON Response (First part):")
            print(json.dumps({k: v[:2] if isinstance(v, list) else v for k, v in data.items()}, indent=2))
        else:
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Test Execution Failed: {e}")

if __name__ == "__main__":
    test_filters()
