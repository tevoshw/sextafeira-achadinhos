import sqlite3
from contextlib import closing

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from comandos.buscar_oferta import CATEGORIAS  # ajuste ao caminho real
from logs.init import logger

ADMINS_DB = "bd/admins.db"
OFERTAS_DB = "bd/ofertas.db"
ROLES_AUTORIZADAS = frozenset({"OWNER", "ADMIN"})
LIMITE_MENSAGEM = 4000  # o Telegram aceita até 4096 caracteres

COLUNAS = """
    ID_PRODUTO, nome_do_produto, link, descricao, nota, preco, quantidade,
    CATEGORIA, afiliado_que_adicionou, desde_quando_existe, vezes_enviada
"""


# ---------- banco ----------

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


def buscar_ofertas_da_categoria(categoria: int) -> list[tuple]:
    filtro = "CATEGORIA IS NULL" if categoria == 0 else "CATEGORIA = ?"
    params = () if categoria == 0 else (categoria,)

    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        return conn.execute(
            f"SELECT {COLUNAS} FROM ofertas WHERE {filtro} ORDER BY ID_PRODUTO ASC",
            params,
        ).fetchall()


# ---------- formatação ----------

def nome_categoria(categoria: int) -> str:
    if categoria == 0:
        return "SEM CATEGORIA"
    return CATEGORIAS.get(categoria, f"CATEGORIA {categoria}")


def formatar_oferta(oferta: tuple) -> str:
    (
        id_produto, nome, link, descricao, nota, preco, quantidade,
        categoria, afiliado_id, data_cadastro, vezes_enviada,
    ) = oferta

    preco_txt = f"R$ {preco:.2f}" if preco is not None else "Não informado"
    nota_txt = nota if nota is not None else "Não informada"

    return (
        f"ID: {id_produto}\n"
        f"Produto: {nome}\n"
        f"Preço: {preco_txt}\n"
        f"Nota: {nota_txt}\n"
        f"Quantidade: {quantidade}\n"
        f"Categoria: {nome_categoria(categoria or 0)}\n"
        f"Adicionada por (ID): {afiliado_id or 'Desconhecido'}\n"
        f"Enviada: {vezes_enviada} vez(es)\n"
        f"Cadastrada em: {data_cadastro}\n"
        f"Link: {link}\n"
        f"Descrição: {descricao or 'Sem descrição'}\n"
        f"{'-' * 25}\n\n"
    )


def montar_mensagens(cabecalho: str, ofertas: list[tuple]) -> list[str]:
    """Agrupa os blocos em mensagens abaixo do limite do Telegram."""
    mensagens: list[str] = []
    atual = cabecalho

    for oferta in ofertas:
        bloco = formatar_oferta(oferta)
        if len(atual) + len(bloco) > LIMITE_MENSAGEM:
            mensagens.append(atual)
            atual = ""
        atual += bloco

    if atual.strip():
        mensagens.append(atual)
    return mensagens


# ---------- teclado ----------

def teclado_categorias(contagens: dict[int, int]) -> InlineKeyboardMarkup:
    botoes = [
        InlineKeyboardButton(
            f"{cid} - {nome} ({contagens.get(cid, 0)})",
            callback_data=f"lo:cat:{cid}",
        )
        for cid, nome in CATEGORIAS.items()
    ]

    # ofertas antigas, salvas sem categoria
    if contagens.get(0):
        botoes.append(
            InlineKeyboardButton(
                f"SEM CATEGORIA ({contagens[0]})", callback_data="lo:cat:0"
            )
        )

    linhas = [botoes[i:i + 2] for i in range(0, len(botoes), 2)]  # 2 colunas
    return InlineKeyboardMarkup(linhas)


def texto_menu(contagens: dict[int, int]) -> str:
    total = sum(contagens.values())
    return f"📂 Escolha uma categoria ({total} ofertas no total):"


# ---------- handlers ----------

async def listar_ofertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("COMANDO RECEBIDO | comando=/listar_ofertas | usuario_id=%s", usuario.id)

    if not usuario_autorizado(usuario.id):
        logger.warning("ACESSO NEGADO | comando=/listar_ofertas | usuario_id=%s", usuario.id)
        await msg.reply_text("Você não tem autorização para listar ofertas.")
        return

    try:
        contagens = contar_por_categoria()
    except sqlite3.Error:
        logger.exception("ERRO AO LISTAR CATEGORIAS | usuario_id=%s", usuario.id)
        await msg.reply_text("Erro ao consultar as ofertas. Verifique as logs.")
        return

    if not contagens:
        await msg.reply_text("Nenhuma oferta cadastrada.")
        return

    await msg.reply_text(
        texto_menu(contagens), reply_markup=teclado_categorias(contagens)
    )


async def callback_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    usuario = query.from_user

    # os botões ficam visíveis a todos no grupo: valida a permissão de novo
    if not usuario_autorizado(usuario.id):
        await query.answer("Você não tem autorização.", show_alert=True)
        return

    try:
        categoria = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer()
        return

    if categoria != 0 and categoria not in CATEGORIAS:
        await query.answer()
        return

    try:
        ofertas = buscar_ofertas_da_categoria(categoria)
    except sqlite3.Error:
        logger.exception(
            "ERRO AO LISTAR OFERTAS | usuario_id=%s | categoria=%s",
            usuario.id, categoria,
        )
        await query.answer("Erro ao consultar o banco.", show_alert=True)
        return

    if not ofertas:
        await query.answer(
            f"Nenhuma oferta em {nome_categoria(categoria)}.", show_alert=True
        )
        return

    await query.answer()

    logger.info(
        "OFERTAS LISTADAS | usuario_id=%s | categoria=%s | total=%d",
        usuario.id, categoria, len(ofertas),
    )

    cabecalho = f"📂 {nome_categoria(categoria)} — {len(ofertas)} oferta(s)\n\n"
    for texto in montar_mensagens(cabecalho, ofertas):
        await query.message.reply_text(texto, disable_web_page_preview=True)

    # reapresenta o menu no fim, para escolher outra categoria sem digitar o comando
    contagens = contar_por_categoria()
    await query.message.reply_text(
        texto_menu(contagens), reply_markup=teclado_categorias(contagens)
    )