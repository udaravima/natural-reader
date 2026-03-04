"""
FastAPI application factory with CORS and router setup.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .endpoints import router


def create_app() -> FastAPI:
    app = FastAPI()

    # Allow CORS so our React frontend can talk to this server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this to your frontend URL
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    return app


app = create_app()
