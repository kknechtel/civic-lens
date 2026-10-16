"""Web search route for supplementary research."""
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from models.user import User
from auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class WebSearchRequest(BaseModel):
    query: str
    max_results: int = 5


@router.post("", include_in_schema=False)
@router.post("/")
def web_search(req: WebSearchRequest, user: User = Depends(get_current_user)):
    """Search the web using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(req.query, max_results=req.max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return {"results": results, "query": req.query}
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return {"results": [], "query": req.query, "error": str(e)}
