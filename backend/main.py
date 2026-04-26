from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, catalog, extract, history, zipcode
from settings import settings

app = FastAPI(title="Chat2Order API", version="2.0.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(extract.router, prefix="/api", tags=["extract"])
app.include_router(catalog.router, prefix="/api", tags=["catalog"])
app.include_router(zipcode.router, prefix="/api", tags=["zipcode"])
app.include_router(history.router, prefix="/api", tags=["history"])
