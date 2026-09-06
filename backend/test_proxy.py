import requests
import os
from dotenv import load_dotenv

load_dotenv()
email = os.getenv("CCTV_EMAIL")
password = os.getenv("CCTV_PASSWORD")
host = os.getenv("CCTV_HOST")

session = requests.Session()
login_url = f"https://{host}/auth/login"
resp = session.post(login_url, data={"email": email, "password": password}, allow_redirects=True)
print("Login status:", resp.status_code)
print("Cookies:", session.cookies.get_dict())

m3u8_url = f"https://{host}/cam04/index.m3u8"
resp = session.get(m3u8_url)
print("M3U8 status:", resp.status_code)
if resp.status_code != 200:
    print("Error text:", resp.text)
else:
    print("M3U8 ok:", resp.text[:100])
