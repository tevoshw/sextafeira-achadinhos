import hashlib
import hmac
import time
import httpx
from config import (
    ALIEXPRESS_APP_KEY as KEY,
    ALIEXPRESS_APP_SECRET as SECRET,
    ALIEXPRESS_TRACKING_ID as TRACKING_ID,
)

ENDPOINT = "https://api-sg.aliexpress.com/sync"

SORT = {
    "relevancia": None,
    "mais_vendidos": "LAST_VOLUME_DESC",
    "mais_baratos": "SALE_PRICE_ASC",
}

CAMPOS = (
    "product_id,product_title,product_main_image_url,target_sale_price,"
    "discount,lastest_volume,evaluate_rate,commission_rate,"
    "promotion_link,product_detail_url"
)


def _assinar(params: dict) -> str:
    base = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def _num(valor):
    if valor in (None, ""):
        return None
    limpo = str(valor).replace("R$", "").replace("US $", "").replace("%", "").strip()
    if "," in limpo and "." not in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _normalizar(item: dict) -> dict:
    preco = _num(item.get("target_sale_price"))
    nota = _num(item.get("evaluate_rate"))      # % (ex.: 96.5)
    taxa = _num(item.get("commission_rate"))    # % (ex.: 8.0)

    return {
        "itemId": str(item.get("product_id", "")),
        "productName": item.get("product_title"),
        "imageUrl": item.get("product_main_image_url"),
        "priceMin": preco,
        "priceMax": preco,
        "priceDiscountRate": _num(item.get("discount")),
        "sales": item.get("lastest_volume"),
        "ratingStar": round(nota / 20, 1) if nota is not None else None,
        "shopName": None,
        "commissionRate": taxa / 100 if taxa is not None else None,
        "commission": preco * taxa / 100 if preco and taxa else None,
        "offerLink": item.get("promotion_link"),
        "productLink": item.get("product_detail_url"),
    }


async def buscar(keyword: str, criterio: str, limite: int) -> list[dict]:
    if not (KEY and SECRET and TRACKING_ID):
        raise RuntimeError("Credenciais do AliExpress ausentes no .env")

    params = {
        "app_key": KEY,
        "method": "aliexpress.affiliate.product.query",
        "sign_method": "sha256",
        "timestamp": str(int(time.time() * 1000)),
        "keywords": keyword,
        "fields": CAMPOS,
        "page_no": "1",
        "page_size": str(limite),
        "target_currency": "BRL",
        "target_language": "PT",
        "ship_to_country": "BR",
        "tracking_id": TRACKING_ID,
    }
    if SORT[criterio]:
        params["sort"] = SORT[criterio]

    params["sign"] = _assinar(params)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(ENDPOINT, params=params)
        resp.raise_for_status()
        corpo = resp.json()

    if "error_response" in corpo:
        raise RuntimeError(corpo["error_response"])

    resultado = corpo["aliexpress_affiliate_product_query_response"]["resp_result"]
    if resultado.get("resp_code") != 200:
        raise RuntimeError(resultado.get("resp_msg"))

    itens = (
        resultado.get("result", {}).get("products", {}).get("product", [])
        )
    return [_normalizar(i) for i in itens]