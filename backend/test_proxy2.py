import requests
import os
from dotenv import load_dotenv

load_dotenv()
email = os.getenv("CCTV_EMAIL")
password = os.getenv("CCTV_PASSWORD")
host = os.getenv("CCTV_HOST")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
login_url = f"https://{host}/auth/login"
resp = session.post(login_url, data={"email": email, "password": password}, allow_redirects=True)

resp = session.get(f"https://{host}/enc.key")
print("/enc.key:", resp.status_code)
resp = session.get(f"https://{host}/cam04/enc.key")
print("/cam04/enc.key:", resp.status_code)
