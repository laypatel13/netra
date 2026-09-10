"""
Debug script from when the cctv.corp8.cloud auth flow was first being
figured out: confirms login + a direct HLS manifest fetch work with the
session cookie. Superseded for day-to-day use by the backend's own
/feeds/catalogue + hls-proxy (backend/app/routers/feeds.py), which does
the same login/cookie dance for real - kept here as a minimal, dependency-
free way to isolate "is it the backend, or is it upstream" when something
in the HLS path breaks.

Takes CLI args instead of reading backend/.env directly - this script
moved out of backend/ into test/connectivity/, so an implicit relative
.env lookup would silently break.

Usage:
    python test_proxy.py --email you@example.com --password XXXX-XXXX-XXXX [--host cctv.corp8.cloud] [--camera-id cam04]
"""
import argparse

import requests


def main():
    parser = argparse.ArgumentParser(description="Debug cctv.corp8.cloud login + HLS manifest fetch")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--host", default="cctv.corp8.cloud")
    parser.add_argument("--camera-id", default="cam04")
    args = parser.parse_args()

    session = requests.Session()
    login_url = f"https://{args.host}/auth/login"
    resp = session.post(login_url, data={"email": args.email, "password": args.password}, allow_redirects=True)
    print("Login status:", resp.status_code)
    print("Cookies:", session.cookies.get_dict())

    m3u8_url = f"https://{args.host}/{args.camera_id}/index.m3u8"
    resp = session.get(m3u8_url)
    print("M3U8 status:", resp.status_code)
    if resp.status_code != 200:
        print("Error text:", resp.text)
    else:
        print("M3U8 ok:", resp.text[:100])


if __name__ == "__main__":
    main()
