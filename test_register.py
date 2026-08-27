import requests
import json

try:
    r = requests.post(
        'http://127.0.0.1:8000/api/auth/register',
        json={'full_name': 'Test User', 'email': 'test5@example.com', 'password': 'testpass123', 'role': 'USER'},
        timeout=5
    )
    print('Status Code:', r.status_code)
    print('Headers:', dict(r.headers))
    print('Content-Type:', r.headers.get('content-type'))
    print('Response Body:', repr(r.text))
    try:
        print('JSON Response:', json.loads(r.text))
    except Exception as je:
        print('JSON Parse Error:', je)
except Exception as e:
    print('Error:', e)
    import traceback
    traceback.print_exc()
