from fastapi.testclient import TestClient
from main import app
import traceback
import sys

client = TestClient(app)
try:
    response = client.post("/api/calculate", json={"origin":"천호역","destination":"강남역","appointment_time":"2026-05-30 09:00","prep_time":30,"lateness_bias":10,"mode":"transit"})
    print(response.status_code)
    try:
        print(response.json())
    except:
        print(response.text)
except Exception as e:
    traceback.print_exc(file=sys.stdout)
