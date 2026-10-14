import hashlib
import json
import os
import time
import httpx
from config import SHOPEE_APP_ID as APP_ID, SHOPEE_SECRET as SECRET

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"

# 1=relevância, 2=mais vendidos, 4=menor preço (confira na doc)
SORT_TYPE = {"relevancia": 1, "mais_vendidos": 2, "mais_baratos": 4}

CAMPOS = """
    itemId productName imageUrl priceMin priceMax priceDiscountRate
    sales ratingStar shopName commissionRate commission offerLink productLink
"""


async def buscar(keyword: str, criterio: str, limite: int) -> list[dict]:
    query = (
        "query { productOfferV2("
        f"keyword: {json.dumps(keyword)}, "
        f"sortType: {SORT_TYPE[criterio]}, limit: {limite}"
        f") {{ nodes {{ {CAMPOS} }} }} }}"
    )
    payload = json.dumps({"query": query}, separators=(",", ":"))

    ts = str(int(time.time()))
    assinatura = hashlib.sha256(f"{APP_ID}{ts}{payload}{SECRET}".encode()).hexdigest()
    headers = {
        "Authorization": f"SHA256 Credential={APP_ID}, Timestamp={ts}, Signature={assinatura}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(ENDPOINT, content=payload, headers=headers)
        resp.raise_for_status()
        corpo = resp.json()

    if "errors" in corpo:
        raise RuntimeError(corpo["errors"])

    return corpo["data"]["productOfferV2"]["nodes"]