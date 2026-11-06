"""HTTP entry point for Meeting Analysis."""

from fastapi import FastAPI

app = FastAPI(title="Meeting Analysis")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
