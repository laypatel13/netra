"""
Debug script from when the cctv.corp8.cloud auth flow was first being
figured out: confirms the HLS encryption key (enc.key) is reachable both
at the host root and per-camera, since the Integrator's Guide is ambiguous
about which path serves it. See feeds.py's hls_proxy() for how the backend
actually handles this (rewrites the manifest's URI to a relative path so
the browser fetches it through the proxy either way).

Takes CLI args instead of reading backend/.env directly - this script
moved out of backend/ into test/connectivity/, so an implicit relative
.env lookup would silently break.

Usage:
    python test_proxy2.py --email you@example.com --password XXXX-XXXX-XXXX [--host cctv.corp8.cloud] [--camera-id cam04]
"""
import argparse

import requests


def main():
    parser = argparse.ArgumentParser(description="Debug cctv.corp8.cloud enc.key reachability")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--host", default="cctv.corp8.cloud")
    parser.add_argument("--camera-id", default="cam04")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    login_url = f"https://{args.host}/auth/login"
    session.post(login_url, data={"email": args.email, "password": args.password}, allow_redirects=True)

    resp = session.get(f"https://{args.host}/enc.key")
    print("/enc.key:", resp.status_code)
    resp = session.get(f"https://{args.host}/{args.camera_id}/enc.key")
    print(f"/{args.camera_id}/enc.key:", resp.status_code)


if __name__ == "__main__":
    main()
