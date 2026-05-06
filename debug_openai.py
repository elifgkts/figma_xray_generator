import os
import traceback

import requests
from openai import OpenAI

print("OPENAI_API_KEY var mı:", bool(os.getenv("OPENAI_API_KEY")))
print("HTTP_PROXY:", os.getenv("HTTP_PROXY"))
print("HTTPS_PROXY:", os.getenv("HTTPS_PROXY"))
print("REQUESTS_CA_BUNDLE:", os.getenv("REQUESTS_CA_BUNDLE"))
print("SSL_CERT_FILE:", os.getenv("SSL_CERT_FILE"))

headers = {
    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"
}

print("\n--- requests testi ---")
try:
    r = requests.get("https://api.openai.com/v1/models",
                     headers=headers, timeout=30)
    print("requests status:", r.status_code)
    print("requests first 300 chars:", r.text[:300])
except Exception as e:
    print("requests exception:", repr(e))
    traceback.print_exc()

print("\n--- OpenAI SDK testi ---")
try:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.responses.create(
        model="gpt-4o",
        input="Merhaba"
    )
    print("SDK OK")
    print(resp.output_text)
except Exception as e:
    print("sdk exception type:", type(e).__name__)
    print("sdk exception repr:", repr(e))
    print("sdk cause repr:", repr(getattr(e, "__cause__", None)))
    traceback.print_exc()
