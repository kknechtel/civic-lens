"""Public municipality profile — the web client renders names, entities,
branding and feature flags from this instead of hardcoding a town."""
from fastapi import APIRouter

from municipality import get_municipality

router = APIRouter()


@router.get("")
@router.get("/")
def municipality_profile():
    return get_municipality().public_profile()
