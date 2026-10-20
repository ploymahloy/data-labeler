import io
import time
import uuid
from typing import Any

import httpx
import jwt
from PIL import Image as PILImage

from tests.conftest import CLIENT_ID, KEY_ID, POOL_ID, PRIVATE_PEM

ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"


def make_token(
    email: str,
    roles: list[str],
    *,
    key: str = PRIVATE_PEM,
    audience: str = CLIENT_ID,
    issuer: str = ISSUER,
    expires_in: int = 3600,
    token_use: str = "id",
    issued_at: int | None = None,
) -> str:
    now = int(time.time()) if issued_at is None else issued_at
    return jwt.encode(
        {
            "sub": f"sub-{email}",
            "email": email,
            "cognito:groups": roles,
            "aud": audience,
            "iss": issuer,
            "token_use": token_use,
            "iat": now,
            "exp": int(time.time()) + expires_in,
        },
        key,
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def auth(email: str, *roles: str, issued_at: int | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(email, list(roles), issued_at=issued_at)}"}


def sub_of(email: str) -> str:
    return f"sub-{email}"


def png_bytes(seed: int, size: tuple[int, int] = (64, 48)) -> bytes:
    """Distinct, valid PNGs: each seed gets a different pixel pattern (and so a different hash)."""
    img = PILImage.new("RGB", size, (seed % 256, (seed * 7) % 256, (seed * 13) % 256))
    img.putpixel((0, 0), ((seed >> 8) % 256, seed % 256, 1))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def create_batch(client: httpx.AsyncClient, headers: dict[str, str], name: str = "b1") -> str:
    r = await client.post("/batches", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def upload_images(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    batch_id: str,
    payloads: list[bytes],
    content_type: str = "image/png",
) -> list[dict[str, Any]]:
    """Full browser flow: request tickets, PUT bytes to the presigned URL, then complete."""
    files = [
        {"filename": f"img-{i}.png", "content_type": content_type, "size": len(p)}
        for i, p in enumerate(payloads)
    ]
    r = await client.post(f"/batches/{batch_id}/uploads", json={"files": files}, headers=headers)
    assert r.status_code == 200, r.text
    tickets = r.json()["tickets"]
    async with httpx.AsyncClient() as s3:
        for ticket, payload in zip(tickets, payloads, strict=True):
            put = await s3.put(ticket["upload_url"], content=payload, headers=ticket["headers"])
            assert put.status_code == 200, put.text
    r = await client.post(
        f"/batches/{batch_id}/uploads/complete",
        json={"image_ids": [t["image_id"] for t in tickets]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["results"]


async def seed_batch(
    client: httpx.AsyncClient, n_images: int, uploader: str = "uploader@example.com"
) -> str:
    """Batch with `n_images` ready (pending) images."""
    headers = auth(uploader, "labeler")
    batch_id = await create_batch(client, headers)
    base = uuid.uuid4().int % 100000
    results = await upload_images(
        client, headers, batch_id, [png_bytes(base + i) for i in range(n_images)]
    )
    assert all(r["result"] == "ready" for r in results), results
    return batch_id


def box(x: float = 0.1, y: float = 0.1, w: float = 0.3, h: float = 0.3, label: str = "car") -> dict:
    return {"id": str(uuid.uuid4()), "label": label, "x": x, "y": y, "w": w, "h": h}
