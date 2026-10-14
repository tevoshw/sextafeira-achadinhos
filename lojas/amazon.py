import asyncio
import time
from typing import Optional

import httpx

from config import (
    AMAZON_CREDENTIAL_ID as CLIENT_ID,
    AMAZON_CREDENTIAL_SECRET as CLIENT_SECRET,
    AMAZON_TAG as TAG,
)

TOKEN_URL = "https://api.amazon.com/auth/o2/token"  # NA (versão 3.1): inclui BR
API_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
MARKETPLACE = "www.amazon.com.br"

SORT = {
    "relevancia": "Relevance",
    "mais_vendidos": "Featured",  # a API não ordena por vendas
    "mais_baratos": "Price:LowToHigh",
}

RESOURCES = [
    "itemInfo.title",
    "images.primary.large",
    "offersV2.listings.price",
    "offersV2.listings.merchantInfo",
]

# cache do token (válido por 1 hora)
_token: Optional[str] = None
_token_expira: float = 0.0
_lock = asyncio.Lock()


async def _obter_token(client: httpx.AsyncClient, forcar: bool = False) -> str:
    global _token, _token_expira

    async with _lock:
        if not forcar and _token and time.time() < _token_expira - 60:
            return _token

        resp = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "creatorsapi::default",
            },
        )
        resp.raise_for_status()
        dados = resp.json()

        _token = dados["access_token"]
        _token_expira = time.time() + int(dados.get("expires_in", 3600))
        return _token


def _normalizar(item: dict) -> dict:
    asin = item.get("asin", "")

    listagens = (item.get("offersV2") or {}).get("listings") or []
    oferta = listagens[0] if listagens else {}
    preco_info = oferta.get("price") or {}
    preco = (preco_info.get("money") or {}).get("amount")
    desconto = (preco_info.get("savings") or {}).get("percentage")

    titulo = ((item.get("itemInfo") or {}).get("title") or {}).get("displayValue")
    imagem = (((item.get("images") or {}).get("primary") or {}).get("large") or {}).get("url")

    return {
        "itemId": asin,
        "productName": titulo,
        "imageUrl": imagem,
        "priceMin": preco,
        "priceMax": preco,
        "priceDiscountRate": desconto,
        "sales": None,
        "ratingStar": None,
        "shopName": (oferta.get("merchantInfo") or {}).get("name"),
        "commissionRate": None,
        "commission": None,
        "offerLink": item.get("detailPageURL"),  # já contém a sua tag
        "productLink": f"https://{MARKETPLACE}/dp/{asin}" if asin else None,
    }


async def buscar(keyword: str, criterio: str, limite: int) -> list[dict]:
    if not (CLIENT_ID and CLIENT_SECRET and TAG):
        raise RuntimeError("Credenciais da Amazon ausentes no .env")

    payload = {
        "keywords": keyword,
        "partnerTag": TAG,
        "marketplace": MARKETPLACE,
        "itemCount": max(1, min(limite, 10)),  # a API aceita de 1 a 10
        "sortBy": SORT[criterio],
        "resources": RESOURCES,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        for tentativa in range(2):
            token = await _obter_token(client, forcar=tentativa > 0)
            resp = await client.post(
                API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-marketplace": MARKETPLACE,
                },
            )
            if resp.status_code == 401 and tentativa == 0:
                continue  # token expirou: renova e tenta de novo
            resp.raise_for_status()
            break

    itens = (resp.json().get("searchResult") or {}).get("items") or []
    return [_normalizar(i) for i in itens]