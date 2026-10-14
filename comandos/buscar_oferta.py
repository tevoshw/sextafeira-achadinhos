import asyncio
import html
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from logs.init import logger
from lojas.main import buscar_produtos

ADMINS_DB = "bd/admins.db"
OFERTAS_DB = "bd/ofertas.db"
ROLES_AUTORIZADAS = frozenset({"OWNER", "ADMIN", "AFILIADO"})

# aceita aspas simples, duplas e as "inteligentes" que o Telegram mobile insere
PADRAO_TERMO = re.compile(r"""^\s*['"‘“](.+?)['"’”]\s*$""", re.S)

MENSAGEM_USO = (
    "Uso correto:\n"
    "/buscar_oferta 'produto'\n\n"
    "Exemplo:\n"
    "/buscar_oferta 'garrafa térmica'"
)

# ---------- configuração ----------

# Os números ficam gravados no banco: nunca renumere. Para criar novas, acrescente no fim.
CATEGORIAS = {
    1: "MÓVEIS",
    2: "ROUPAS / CALÇADOS / ETC",
    3: "PERIFÉRICOS",
    4: "PERFUMES",
    5: "OBJETOS EM GERAL",
    6: "TECNOLOGIA",
    7: "CASA E COZINHA",
    8: "ELETRODOMÉSTICOS",
    9: "BELEZA E CUIDADOS PESSOAIS",
    10: "SAÚDE E BEM-ESTAR",
    11: "ESPORTES E LAZER",
    12: "GAMES E CONSOLES",
    13: "BRINQUEDOS E INFANTIL",
    14: "BEBÊS",
    15: "PET SHOP",
    16: "AUTOMOTIVO",
    17: "FERRAMENTAS E CONSTRUÇÃO",
    18: "LIVROS E PAPELARIA",
    19: "ACESSÓRIOS",
    20: "OUTROS",
}

ROTULOS_CRITERIO = {
    "relevancia": "🎯 Mais relevante",
    "mais_vendidos": "🔥 Mais vendido",
    "mais_baratos": "💸 Mais barato",
}
ORDEM_CRITERIO = {"relevancia": 0, "mais_vendidos": 1, "mais_baratos": 2}

ROTULOS_LOJA = {
    "shopee": "🟠 Shopee",
    "aliexpress": "🔴 AliExpress",
    "amazon": "🟡 Amazon",
}

# False = empilha tudo, mesmo que o link já exista no banco.
# True  = pula produtos cujo link já está salvo.
IGNORAR_DUPLICADOS = False

PENDENTES_KEY = "ofertas_pendentes"
TTL_PENDENTE = 3600     # segundos até os botões expirarem
INTERVALO_ENVIO = 0.5   # pausa entre mensagens (limite do Telegram)


# ---------- modelo ----------

def _num(valor: Any) -> Optional[float]:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Produto:
    item_id: str
    nome: str
    imagem: Optional[str]
    preco_min: Optional[float]
    preco_max: Optional[float]
    desconto: Optional[float]
    vendas: Optional[int]
    nota: Optional[float]
    loja: Optional[str]
    taxa_comissao: Optional[float]
    comissao: Optional[float]
    link_oferta: Optional[str]
    link_produto: Optional[str]
    marketplace: Optional[str] = None
    criterio: Optional[str] = None

    @classmethod
    def from_api(cls, d: dict) -> "Produto":
        vendas = _num(d.get("sales"))
        return cls(
            item_id=str(d.get("itemId", "")),
            nome=(d.get("productName") or "").strip(),
            imagem=d.get("imageUrl") or None,
            preco_min=_num(d.get("priceMin")),
            preco_max=_num(d.get("priceMax")),
            desconto=_num(d.get("priceDiscountRate")),
            vendas=int(vendas) if vendas is not None else None,
            nota=_num(d.get("ratingStar")),
            loja=d.get("shopName") or None,
            taxa_comissao=_num(d.get("commissionRate")),
            comissao=_num(d.get("commission")),
            link_oferta=d.get("offerLink") or None,
            link_produto=d.get("productLink") or None,
            marketplace=d.get("marketplace"),
            criterio=d.get("criterio"),
        )

    @property
    def link(self) -> str:
        # prioriza o link de afiliado
        return self.link_oferta or self.link_produto or ""


# ---------- formatação ----------

def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _faixa_preco(p: Produto) -> Optional[str]:
    if p.preco_min is None:
        return None
    if p.preco_max is None or p.preco_max == p.preco_min:
        return _brl(p.preco_min)
    return f"{_brl(p.preco_min)} – {_brl(p.preco_max)}"


def formatar_produto(p: Produto) -> str:
    e = html.escape
    nome = p.nome if len(p.nome) <= 150 else p.nome[:147] + "…"

    etiqueta = " · ".join(
        parte
        for parte in (
            ROTULOS_CRITERIO.get(p.criterio),
            ROTULOS_LOJA.get(p.marketplace, (p.marketplace or "").capitalize()),
        )
        if parte
    )

    linhas = []
    if etiqueta:
        linhas += [f"<b>{etiqueta}</b>", ""]
    linhas.append(f"🛍️ <b>{e(nome)}</b>")
    linhas.append("")

    if p.link:
        linhas.append(f'🔗 <a href="{e(p.link, quote=True)}">Link do produto</a>')
    if (preco := _faixa_preco(p)):
        linhas.append(f"💰 {preco}")
    if p.desconto:
        linhas.append(f"🏷️ {p.desconto:.0f}% OFF")
    if p.nota is not None:
        linhas.append(f"⭐ {p.nota:.1f}/5")
    if p.vendas is not None:
        linhas.append(f"📦 {p.vendas:,} vendidos".replace(",", "."))
    if p.loja:
        linhas.append(f"🏪 {e(p.loja)}")
    if p.taxa_comissao is not None:
        # taxa como fração (0.05 = 5%)
        extra = f" ({_brl(p.comissao)})" if p.comissao is not None else ""
        linhas.append(f"💸 Comissão: {p.taxa_comissao * 100:.1f}%{extra}")

    return "\n".join(linhas)


def _descricao(p: Produto) -> Optional[str]:
    partes = (
        f"Marketplace: {p.marketplace.capitalize()}" if p.marketplace else None,
        f"Critério: {p.criterio}" if p.criterio else None,
        f"Loja: {p.loja}" if p.loja else None,
        f"{p.desconto:.0f}% OFF" if p.desconto else None,
        f"{p.vendas} vendidos" if p.vendas is not None else None,
    )
    return " | ".join(x for x in partes if x) or None


# ---------- teclados ----------

def teclado_confirmacao(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Salvar tudo", callback_data=f"of:salvar:{token}"),
        InlineKeyboardButton("❌ Não salvar", callback_data=f"of:nao:{token}"),
    ]])


def teclado_categorias(token: str) -> InlineKeyboardMarkup:
    botoes = [
        InlineKeyboardButton(f"{cid} - {nome}", callback_data=f"of:cat:{token}:{cid}")
        for cid, nome in CATEGORIAS.items()
    ]
    linhas = [botoes[i:i + 2] for i in range(0, len(botoes), 2)]  # 2 colunas
    linhas.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"of:nao:{token}")])
    return InlineKeyboardMarkup(linhas)


# ---------- banco ----------

def usuario_autorizado(usuario_id: int) -> bool:
    with closing(sqlite3.connect(ADMINS_DB)) as conn:
        row = conn.execute(
            "SELECT ROLE FROM admins WHERE ID = ?", (usuario_id,)
        ).fetchone()
    return row is not None and row[0] in ROLES_AUTORIZADAS


def salvar_ofertas(produtos: list[Produto], autor_id: int, categoria: int) -> int:
    """Insere os produtos como novas linhas, numa única transação.

    Retorna quantas linhas foram gravadas.
    """
    with closing(sqlite3.connect(OFERTAS_DB)) as conn, conn:
        if IGNORAR_DUPLICADOS:
            links = {
                r[0] for r in conn.execute("SELECT link FROM ofertas")
            }
            produtos = [p for p in produtos if p.link not in links]

        conn.executemany(
            """
            INSERT INTO ofertas (
                link, nome_do_produto, descricao, imagem,
                nota, preco, CATEGORIA, afiliado_que_adicionou
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    p.link, p.nome, _descricao(p), p.imagem,
                    p.nota, p.preco_min, categoria, autor_id,
                )
                for p in produtos
            ],
        )

    return len(produtos)


def total_na_categoria(categoria: int) -> int:
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM ofertas WHERE CATEGORIA = ?", (categoria,)
        ).fetchone()[0]


# ---------- utilidades ----------

def extrair_termo(args: list[str]) -> Optional[str]:
    match = PADRAO_TERMO.match(" ".join(args))
    return match.group(1).strip() if match else None


def _pendentes(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault(PENDENTES_KEY, {})


def _limpar_expirados(pendentes: dict) -> None:
    agora = time.time()
    for token in [t for t, v in pendentes.items() if agora - v["ts"] > TTL_PENDENTE]:
        del pendentes[token]


async def enviar_produto(msg: Message, p: Produto) -> None:
    texto = formatar_produto(p)
    if p.imagem and len(texto) <= 1024:  # limite de legenda
        try:
            await msg.reply_photo(p.imagem, caption=texto, parse_mode=ParseMode.HTML)
            return
        except BadRequest:
            logger.warning("Falha ao enviar imagem | item_id=%s", p.item_id)
    await msg.reply_text(
        texto,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


# ---------- handlers ----------

async def buscar_oferta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("COMANDO RECEBIDO | comando=/buscar_oferta | usuario_id=%s", usuario.id)

    if not usuario_autorizado(usuario.id):
        logger.warning("ACESSO NEGADO | comando=/buscar_oferta | usuario_id=%s", usuario.id)
        await msg.reply_text("Você não tem autorização para buscar ofertas.")
        return

    termo = extrair_termo(context.args)
    if not termo:
        await msg.reply_text(MENSAGEM_USO)
        return

    aviso = await msg.reply_text(f"🔎 Buscando “{termo}”...")

    try:
        resultados = await buscar_produtos(termo)
    except Exception:
        logger.exception("ERRO NA BUSCA | termo=%s | usuario_id=%s", termo, usuario.id)
        await aviso.edit_text("Erro ao buscar os produtos. Tente novamente.")
        return

    produtos = [Produto.from_api(r) for r in resultados]
    produtos = [p for p in produtos if p.nome and p.link]
    produtos.sort(key=lambda p: ORDEM_CRITERIO.get(p.criterio, 99))  # estável

    if not produtos:
        await aviso.edit_text("Nenhum produto encontrado.")
        return

    await aviso.edit_text(f"✅ {len(produtos)} produtos encontrados para “{termo}”:")

    for p in produtos:
        await enviar_produto(msg, p)
        await asyncio.sleep(INTERVALO_ENVIO)

    pendentes = _pendentes(context)
    _limpar_expirados(pendentes)
    token = uuid.uuid4().hex[:8]
    pendentes[token] = {"produtos": produtos, "autor_id": usuario.id, "ts": time.time()}

    await msg.reply_text(
        f"Deseja salvar os {len(produtos)} produtos no banco de ofertas?",
        reply_markup=teclado_confirmacao(token),
    )


async def callback_oferta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, acao, token, *resto = query.data.split(":")

    pendentes = _pendentes(context)
    pendente = pendentes.get(token)

    if pendente is None:
        await query.answer()
        await query.edit_message_text(
            "⌛ Essa busca expirou. Use /buscar_oferta novamente."
        )
        return

    if query.from_user.id != pendente["autor_id"]:
        await query.answer(
            "Só quem fez a busca pode usar esses botões.", show_alert=True
        )
        return

    await query.answer()

    if acao == "nao":
        pendentes.pop(token, None)
        await query.edit_message_text("❌ Nada foi salvo.")
        return

    if acao == "salvar":
        await query.edit_message_text(
            "📂 Em qual categoria salvar esses produtos?",
            reply_markup=teclado_categorias(token),
        )
        return

    if acao == "cat":
        try:
            categoria = int(resto[0])
        except (IndexError, ValueError):
            return
        if categoria not in CATEGORIAS:
            return

        # remove antes de salvar: um duplo clique não grava duas vezes
        pendente = pendentes.pop(token, None)
        if pendente is None:
            return

        try:
            total = salvar_ofertas(
                pendente["produtos"], pendente["autor_id"], categoria
            )
            acumulado = total_na_categoria(categoria)
        except sqlite3.Error:
            logger.exception(
                "ERRO AO SALVAR OFERTAS | usuario_id=%s", pendente["autor_id"]
            )
            await query.edit_message_text("Erro ao salvar as ofertas no banco de dados.")
            return

        logger.info(
            "OFERTAS SALVAS | autor_id=%s | categoria=%s | total=%d | acumulado=%d",
            pendente["autor_id"], categoria, total, acumulado,
        )

        await query.edit_message_text(
            f"✅ {total} oferta(s) salva(s) em {CATEGORIAS[categoria]}.\n"
            f"📚 Essa categoria agora tem {acumulado} ofertas."
        )