"""
Demo login for the deployed frontend.

Not a user system: there's one seeded demo account (DEMO_EMAIL/DEMO_PASSWORD),
checked against env vars, no database row, no expiry. It exists only so a
deployed demo isn't wide open on the internet and there's a real credential
pair to hand judges. It gates the frontend's own routes only - it does not
protect the API, which still runs on the X-Role header-trust model in
dependencies.py. Swap for real per-user auth before anything resembling
production.
"""
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

load_dotenv()

router = APIRouter(prefix="/auth", tags=["auth"])

DEMO_EMAIL = os.getenv("DEMO_EMAIL", "judge@netra.demo")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "netra-demo")
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "netra-local-dev-token")


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str


@router.post("/login", response_model=LoginResponse, summary="Demo account login")
def login(body: LoginRequest):
    if body.email != DEMO_EMAIL or body.password != DEMO_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return LoginResponse(token=DEMO_TOKEN)
