import asyncio
from typing import Optional

from logs.init import logger

from . import aliexpress, amazon, shopee

LOJAS = {
    "shopee": shopee,
    "aliexpress": aliexpress,
    "amazon": amazon,
}

# ordem importa: define a prioridade ao deduplicar dentro da mesma loja
CRITERIOS = ("relevancia", "mais_vendidos", "mais_baratos")

PRODUTOS_POR_CRITERIO = 1
CANDIDATOS_POR_BUSCA = 5  # margem para pular repetidos
TIMEOUT_POR_BUSCA = 15  # segundos


async def _buscar_criterio(loja: str, modulo, keyword: str, criterio: str) -> list[dict]:
    try:
        return await asyncio.wait_for(
            modulo.buscar(keyword, criterio, CANDIDATOS_POR_BUSCA),
            timeout=TIMEOUT_POR_BUSCA,
        )
    except Exception:
        # uma loja ou critério com falha não pode derrubar o resto
        logger.exception(
            "FALHA NA BUSCA | loja=%s | criterio=%s | keyword=%s",
            loja, criterio, keyword,
        )
        return []


def _chave(produto: dict) -> Optional[str]:
    return produto.get("productLink") or produto.get("offerLink")


async def _buscar_loja(loja: str, modulo, keyword: str) -> list[dict]:
    # os 3 critérios da mesma loja rodam em paralelo
    listas = await asyncio.gather(
        *(_buscar_criterio(loja, modulo, keyword, c) for c in CRITERIOS)
    )

    escolhidos: list[dict] = []
    vistos: set[str] = set()

    for criterio, candidatos in zip(CRITERIOS, listas):
        pegos = 0
        for produto in candidatos:
            chave = _chave(produto)
            if not chave or not produto.get("offerLink") or chave in vistos:
                continue
            vistos.add(chave)
            escolhidos.append({**produto, "marketplace": loja, "criterio": criterio})
            pegos += 1
            if pegos >= PRODUTOS_POR_CRITERIO:
                break

    return escolhidos


async def buscar_produtos(keyword: str) -> list[dict]:
    """Retorna até 9 produtos (3 lojas x 3 critérios) com link de afiliado."""
    por_loja = await asyncio.gather(
        *(_buscar_loja(nome, mod, keyword) for nome, mod in LOJAS.items())
    )
    resultados = [p for lista in por_loja for p in lista]

    logger.info("BUSCA CONCLUIDA | keyword=%s | total=%d", keyword, len(resultados))
    return resultados