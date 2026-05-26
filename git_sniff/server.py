import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from git_sniff.schemas import (
    RepoScorecard,
    BadRepoError,
    RepoNotFoundError,
    RateLimitedError,
    EngineError,
)
from git_sniff.engine import evaluate, parse_repo
from git_sniff.auth import resolve_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("git_sniff.server")


class ServerState:
    http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    ServerState.http_client = httpx.AsyncClient()
    logger.info("Shared HTTPX AsyncClient initialized.")
    yield
    await ServerState.http_client.aclose()
    logger.info("Shared HTTPX AsyncClient closed.")


app = FastAPI(
    title="git-sniff Microservice API (DEPRECATED)",
    description=(
        "Legacy/manual HTTP adapter for the git-sniff engine. The Chrome extension "
        "now uses Native Messaging; this server is retained for CLI scripting and "
        "curl. Scheduled for removal once sniff.sh migrates off HTTP."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://github.com", "http://localhost", "http://127.0.0.1", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/sniff", response_model=RepoScorecard)
async def sniff_repository(repo: str = Query(..., description="owner/repo")):
    try:
        owner, repo_name = parse_repo(repo)
        return await evaluate(
            owner, repo_name,
            token=resolve_token(),
            http_client=ServerState.http_client,
        )
    except BadRepoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RepoNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RateLimitedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except EngineError as e:
        raise HTTPException(status_code=500, detail=str(e))
