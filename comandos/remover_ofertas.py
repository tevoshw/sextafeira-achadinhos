import re
import sqlite3
import time
import uuid
from contextlib import closing

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from comandos.buscar_oferta import CATEGORIAS  # ajuste ao caminho real
from logs.init import logger

ADMINS_DB = "bd/admins.db"
OFERTAS_DB = "bd/ofertas.db"
ROLES_AUTORIZADAS = frozenset({"OWNER", "ADMIN"})

MAX_IDS = 30           # limite por comando (mantém a mensagem abaixo de 4096 caracteres)
TTL_PENDENTE = 300     # segundos até o botão de confirmação expirar
PENDENTES_KEY = "remocoes_pendentes"

MENSAGEM_USO = (
    "Uso correto:\n"
    "/remover_oferta ID [ID ...]\n\n"
    "Exemplos:\n"
    "/remover_oferta 3\n"
    "/remover_oferta 3 5 8\n"
    "/remover_oferta 3,5,8"
)


# ---------- banco ----------

def usuario_autorizado(usuario_id: int) -> bool:
    with closing(sqlite3.connect(ADMINS_DB)) as conn:
        row = conn.execute(
            "SELECT ROLE FROM admins WHERE ID = ?", (usuario_id,)
        ).fetchone()
    return row is not None and row[0] in ROLES_AUTORIZADAS


def buscar_por_ids(ids: list[int]) -> list[dict]:
    marcadores = ",".join("?" * len(ids))
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        conn.row_factory = sqlite3.Row
        linhas = conn.execute(
            f"""
            SELECT ID_PRODUTO, nome_do_produto, CATEGORIA, preco
            FROM ofertas
            WHERE ID_PRODUTO IN ({marcadores})
            ORDER BY ID_PRODUTO
            """,
            ids,
        ).fetchall()
    return [dict(l) for l in linhas]


def remover_por_ids(ids: list[int]) -> list[int]:
    """Apaga numa única transação e devolve os IDs que realmente existiam."""
    marcadores = ",".join("?" * len(ids))
    with closing(sqlite3.connect(OFERTAS_DB)) as conn, conn:
        existentes = [
            r[0]
            for r in conn.execute(
                f"SELECT ID_PRODUTO FROM ofertas WHERE ID_PRODUTO IN ({marcadores})",
                ids,
            )
        ]
        if existentes:
            conn.execute(
                f"DELETE FROM ofertas WHERE ID_PRODUTO IN ({marcadores})", ids
            )
    return existentes


# ---------- utilidades ----------

def extrair_ids(args: list[str]) -> list[int]:
    """Aceita '3 5 8' ou '3,5,8'. Levanta ValueError se algo não for número."""
    tokens = [t for t in re.split(r"[,\s]+", " ".join(args).strip()) if t]
    if not tokens or not all(t.isdigit() for t in tokens):
        raise ValueError
    return list(dict.fromkeys(int(t) for t in tokens))  # sem repetidos, mantém a ordem


def _nome_categoria(categoria: int | None) -> str:
    if not categoria:
        return "SEM CATEGORIA"
    return CATEGORIAS.get(categoria, f"CATEGORIA {categoria}")


def _linha(oferta: dict) -> str:
    nome = oferta["nome_do_produto"]
    nome = nome if len(nome) <= 60 else nome[:57] + "…"
    preco = oferta["preco"]
    preco_txt = f"R$ {preco:.2f}" if preco is not None else "sem preço"
    return (
        f"ID {oferta['ID_PRODUTO']} · {nome} · "
        f"{_nome_categoria(oferta['CATEGORIA'])} · {preco_txt}"
    )


def _pendentes(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault(PENDENTES_KEY, {})


def _limpar_expirados(pendentes: dict) -> None:
    agora = time.time()
    for token in [t for t, v in pendentes.items() if agora - v["ts"] > TTL_PENDENTE]:
        del pendentes[token]


def teclado_confirmacao(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Confirmar remoção", callback_data=f"ro:sim:{token}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"ro:nao:{token}"),
    ]])


# ---------- handlers ----------

async def remover_oferta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("COMANDO RECEBIDO | comando=/remover_oferta | usuario_id=%s", usuario.id)

    if not usuario_autorizado(usuario.id):
        logger.warning("ACESSO NEGADO | comando=/remover_oferta | usuario_id=%s", usuario.id)
        await msg.reply_text("Você não tem autorização para remover ofertas.")
        return

    try:
        ids = extrair_ids(context.args)
    except ValueError:
        await msg.reply_text(MENSAGEM_USO)
        return

    if len(ids) > MAX_IDS:
        await msg.reply_text(f"Máximo de {MAX_IDS} IDs por comando.")
        return

    try:
        encontradas = buscar_por_ids(ids)
    except sqlite3.Error:
        logger.exception("ERRO AO BUSCAR OFERTAS | usuario_id=%s | ids=%s", usuario.id, ids)
        await msg.reply_text("Erro ao consultar as ofertas. Verifique as logs.")
        return

    if not encontradas:
        await msg.reply_text("Não encontrei nenhuma oferta com esses IDs.")
        return

    achados = {o["ID_PRODUTO"] for o in encontradas}
    nao_encontrados = [i for i in ids if i not in achados]

    texto = f"🗑️ Confirmar a remoção de {len(encontradas)} oferta(s)?\n\n"
    texto += "\n".join(_linha(o) for o in encontradas)
    if nao_encontrados:
        texto += f"\n\n⚠️ IDs não encontrados: {', '.join(map(str, nao_encontrados))}"
    texto += "\n\nEssa ação não pode ser desfeita."

    pendentes = _pendentes(context)
    _limpar_expirados(pendentes)
    token = uuid.uuid4().hex[:8]
    pendentes[token] = {
        "ofertas": encontradas,
        "autor_id": usuario.id,
        "ts": time.time(),
    }

    await msg.reply_text(texto, reply_markup=teclado_confirmacao(token))


async def callback_remover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    usuario = query.from_user
    _, acao, token = query.data.split(":")

    pendentes = _pendentes(context)
    pendente = pendentes.get(token)

    if pendente is None:
        await query.answer()
        await query.edit_message_text(
            "⌛ Essa solicitação expirou. Use /remover_oferta novamente."
        )
        return

    if usuario.id != pendente["autor_id"]:
        await query.answer(
            "Só quem pediu a remoção pode confirmar.", show_alert=True
        )
        return

    # a permissão pode ter mudado desde o comando
    if not usuario_autorizado(usuario.id):
        pendentes.pop(token, None)
        await query.answer("Você não tem mais autorização.", show_alert=True)
        return

    await query.answer()

    if acao == "nao":
        pendentes.pop(token, None)
        await query.edit_message_text("❌ Remoção cancelada. Nada foi apagado.")
        return

    if acao != "sim":
        return

    # remove antes de apagar: um duplo clique não executa duas vezes
    pendente = pendentes.pop(token, None)
    if pendente is None:
        return

    por_id = {o["ID_PRODUTO"]: o for o in pendente["ofertas"]}

    try:
        removidos = remover_por_ids(list(por_id))
    except sqlite3.Error:
        logger.exception(
            "ERRO AO REMOVER OFERTAS | usuario_id=%s | ids=%s",
            usuario.id, list(por_id),
        )
        await query.edit_message_text(
            "Ocorreu um erro ao remover as ofertas. Verifique as logs."
        )
        return

    for id_produto in removidos:
        logger.info(
            "OFERTA REMOVIDA | autor_id=%s | autor_nome=%s | produto_id=%s | produto_nome=%s",
            usuario.id, usuario.full_name, id_produto,
            por_id[id_produto]["nome_do_produto"],
        )

    sumiram = len(por_id) - len(removidos)  # já tinham sido apagadas por outra pessoa
    texto = (
        f"✅ {len(removidos)} oferta(s) removida(s).\n\n"
        + "\n".join(_linha(por_id[i]) for i in removidos)
        + f"\n\nRemovida(s) por: {usuario.full_name}"
    )
    if sumiram:
        texto += f"\n⚠️ {sumiram} já não existia(m) mais."

    await query.edit_message_text(texto)