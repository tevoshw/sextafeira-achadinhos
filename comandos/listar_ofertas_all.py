import sqlite3
from contextlib import closing

from telegram import Update
from telegram.ext import ContextTypes

from comandos.buscar_oferta import CATEGORIAS  # ajuste ao caminho real
from logs.init import logger

ADMINS_DB = "bd/admins.db"
OFERTAS_DB = "bd/ofertas.db"
ROLES_AUTORIZADAS = frozenset({"OWNER", "ADMIN"})


def usuario_autorizado(usuario_id: int) -> bool:
    with closing(sqlite3.connect(ADMINS_DB)) as conn:
        row = conn.execute(
            "SELECT ROLE FROM admins WHERE ID = ?", (usuario_id,)
        ).fetchone()
    return row is not None and row[0] in ROLES_AUTORIZADAS


def contar_por_categoria() -> dict[int, int]:
    """Retorna {categoria: total}. A chave 0 reúne ofertas sem categoria."""
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        linhas = conn.execute(
            "SELECT COALESCE(CATEGORIA, 0), COUNT(*) FROM ofertas GROUP BY 1"
        ).fetchall()
    return dict(linhas)


def montar_resumo(contagens: dict[int, int]) -> str:
    total = sum(contagens.values())
    linhas = [f"TOTAL OFERTAS = {total}", ""]

    # todas as categorias cadastradas, mesmo as que estão com 0
    for cid, nome in CATEGORIAS.items():
        linhas.append(f"OFERTAS CATEGORIA {cid} ({nome}) = {contagens.get(cid, 0)}")

    # categorias que existem no banco mas não estão no dicionário
    desconhecidas = sorted(c for c in contagens if c != 0 and c not in CATEGORIAS)
    for cid in desconhecidas:
        linhas.append(f"OFERTAS CATEGORIA {cid} (DESCONHECIDA) = {contagens[cid]}")

    if contagens.get(0):
        linhas.append(f"OFERTAS SEM CATEGORIA = {contagens[0]}")

    return "\n".join(linhas)


async def listar_ofertas_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("COMANDO RECEBIDO | comando=/listar_ofertas_all | usuario_id=%s", usuario.id)

    if not usuario_autorizado(usuario.id):
        logger.warning("ACESSO NEGADO | comando=/listar_ofertas_all | usuario_id=%s", usuario.id)
        await msg.reply_text("Você não tem autorização para listar ofertas.")
        return

    try:
        contagens = contar_por_categoria()
    except sqlite3.Error:
        logger.exception("ERRO AO CONTAR OFERTAS | usuario_id=%s", usuario.id)
        await msg.reply_text("Erro ao consultar as ofertas. Verifique as logs.")
        return

    logger.info(
        "RESUMO DE OFERTAS | usuario_id=%s | total=%d",
        usuario.id, sum(contagens.values()),
    )

    await msg.reply_text(montar_resumo(contagens))