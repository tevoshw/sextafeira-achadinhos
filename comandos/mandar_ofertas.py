import html
import random
import sqlite3
import time
import uuid
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from comandos.buscar_oferta import CATEGORIAS  # ajuste ao caminho real
from logs.init import logger

from config import GRUPO_ID as GRUPO_ID

ADMIN_DB = "bd/admins.db"
OFERTAS_DB = "bd/ofertas.db"
ROLES_AUTORIZADAS = frozenset({"OWNER", "ADMIN"})


TZ = ZoneInfo("America/Sao_Paulo")
INTERVALO = timedelta(minutes=10)
HORARIOS = (8, 10, 12, 14, 16, 18, 20, 22)
QUANTIDADES = (5, 10, 15, 20, 30, 50)
MAX_QUANTIDADE = 100
TTL_RASCUNHO = 900  # segundos até o assistente expirar

# A descrição salva pelo /buscar_oferta tem dados internos (marketplace, critério).
# Deixe False para não aparecer no grupo.
MOSTRAR_DESCRICAO = False

RASCUNHOS_KEY = "mo_rascunhos"
LOTES_KEY = "mo_lotes"


# =========================================================
# BANCO
# =========================================================

def usuario_autorizado(user_id: int) -> bool:
    with closing(sqlite3.connect(ADMIN_DB)) as conn:
        row = conn.execute(
            "SELECT ROLE FROM admins WHERE ID = ?", (user_id,)
        ).fetchone()
    return row is not None and row[0] in ROLES_AUTORIZADAS


def contar_por_categoria() -> dict[int, int]:
    """{categoria: total}. A chave 0 reúne ofertas sem categoria."""
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        linhas = conn.execute(
            "SELECT COALESCE(CATEGORIA, 0), COUNT(*) FROM ofertas GROUP BY 1"
        ).fetchall()
    return dict(linhas)


def selecionar_ofertas(categorias: set[int], quantidade: int) -> list[int]:
    """Escolhe IDs distribuindo entre as categorias (rodízio).

    Dentro de cada categoria, prioriza as menos enviadas e sorteia o desempate.
    Nunca repete o mesmo link no mesmo lote.
    """
    filas = []
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        for cid in categorias:
            filtro = "CATEGORIA IS NULL" if cid == 0 else "CATEGORIA = ?"
            params = () if cid == 0 else (cid,)
            linhas = conn.execute(
                f"""
                SELECT ID_PRODUTO, link FROM ofertas
                WHERE {filtro}
                ORDER BY COALESCE(vezes_enviada, 0) ASC, RANDOM()
                """,
                params,
            ).fetchall()
            filas.append(deque(linhas))

    random.shuffle(filas)  # não favorece sempre as mesmas categorias
    escolhidas: list[int] = []
    links: set[str] = set()

    while len(escolhidas) < quantidade and any(filas):
        for fila in filas:
            while fila:
                id_produto, link = fila.popleft()
                if link in links:
                    continue
                links.add(link)
                escolhidas.append(id_produto)
                break
            if len(escolhidas) >= quantidade:
                break

    return escolhidas


def buscar_oferta(id_produto: int):
    with closing(sqlite3.connect(OFERTAS_DB)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT ID_PRODUTO, nome_do_produto, descricao, preco, nota,
                   link, imagem, afiliado_que_adicionou
            FROM ofertas WHERE ID_PRODUTO = ?
            """,
            (id_produto,),
        ).fetchone()


def registrar_envio(oferta) -> None:
    """Soma 1 em vezes_enviada e no contador do afiliado, de forma atômica."""
    afiliado = oferta["afiliado_que_adicionou"]

    conn = sqlite3.connect(OFERTAS_DB, isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS admin_db", (ADMIN_DB,))
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                UPDATE ofertas
                SET vezes_enviada = COALESCE(vezes_enviada, 0) + 1
                WHERE ID_PRODUTO = ?
                """,
                (oferta["ID_PRODUTO"],),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Oferta não encontrada: {oferta['ID_PRODUTO']}")

            if afiliado is not None:
                conn.execute(
                    """
                    UPDATE admin_db.admins
                    SET Ofertas_Enviadas_no_Grupo =
                        COALESCE(Ofertas_Enviadas_no_Grupo, 0) + 1
                    WHERE ID = ?
                    """,
                    (afiliado,),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# =========================================================
# FORMATAÇÃO E ENVIO
# =========================================================

def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _nome(cid: int) -> str:
    return "SEM CATEGORIA" if cid == 0 else CATEGORIAS.get(cid, f"CATEGORIA {cid}")


def _ordem(cids) -> list[int]:
    return sorted(cids, key=lambda c: (c == 0, c))  # "sem categoria" por último


def _rotulo(dt: datetime) -> str:
    hoje = datetime.now(TZ).date()
    if dt.date() == hoje:
        prefixo = "hoje"
    elif dt.date() == hoje + timedelta(days=1):
        prefixo = "amanhã"
    else:
        prefixo = dt.strftime("%d/%m")
    return f"{prefixo} às {dt:%H:%M}"


def formatar_oferta(o) -> str:
    e = html.escape
    nome = o["nome_do_produto"]
    nome = nome if len(nome) <= 150 else nome[:147] + "…"

    linhas = [f"🛍️ <b>{e(nome)}</b>", ""]
    if MOSTRAR_DESCRICAO and o["descricao"]:
        linhas += [e(o["descricao"]), ""]
    if o["preco"] is not None:
        linhas.append(f"💰 {_brl(o['preco'])}")
    if o["nota"] is not None:
        linhas.append(f"⭐ {o['nota']:.1f}/5")
    linhas.append(f'🔗 <a href="{e(o["link"], quote=True)}">Ver oferta</a>')
    return "\n".join(linhas)


async def enviar_oferta(bot, o) -> None:
    """Envia com foto; se a imagem for recusada, envia só o texto."""
    texto = formatar_oferta(o)

    if o["imagem"] and len(texto) <= 1024:  # limite de legenda
        try:
            await bot.send_photo(
                chat_id=GRUPO_ID,
                photo=o["imagem"],
                caption=texto,
                parse_mode=ParseMode.HTML,
            )
            return
        except BadRequest:
            logger.warning(
                "Imagem recusada | ID: %s. Enviando só texto.", o["ID_PRODUTO"]
            )

    await bot.send_message(chat_id=GRUPO_ID, text=texto, parse_mode=ParseMode.HTML)


# =========================================================
# TELAS DO ASSISTENTE
# =========================================================

def _botao(texto: str, token: str, acao: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(texto, callback_data=f"mo:{token}:{acao}")


def _cancelar(token: str) -> list[InlineKeyboardButton]:
    return [_botao("❌ Cancelar", token, "x")]


def tela_quantidade(token: str, total: int):
    botoes = [_botao(str(n), token, f"qtd:{n}") for n in QUANTIDADES]
    linhas = [botoes[i:i + 3] for i in range(0, len(botoes), 3)]
    linhas.append(_cancelar(token))
    texto = (
        "📦 Quantas ofertas você quer enviar?\n\n"
        f"{total} ofertas cadastradas no total.\n"
        f"Para outro valor, use /mandar_ofertas 25 (máx. {MAX_QUANTIDADE})."
    )
    return texto, InlineKeyboardMarkup(linhas)


def tela_categorias(token: str, r: dict, contagens: dict[int, int]):
    selecionadas = r["categorias"]
    botoes = []
    for cid in _ordem(c for c, n in contagens.items() if n > 0):
        marca = "✅" if cid in selecionadas else "⬜"
        rotulo = _nome(cid) if cid == 0 else f"{cid} - {_nome(cid)}"
        botoes.append(_botao(f"{marca} {rotulo} ({contagens[cid]})", token, f"cat:{cid}"))

    linhas = [botoes[i:i + 2] for i in range(0, len(botoes), 2)]
    linhas.append([
        _botao("☑️ Marcar todas", token, "todas"),
        _botao("🧹 Limpar", token, "limpar"),
    ])
    linhas.append([_botao("Continuar ▶️", token, "ok")])
    linhas.append(_cancelar(token))

    disponiveis = sum(contagens.get(c, 0) for c in selecionadas)
    texto = (
        "📂 Quais categorias você quer enviar?\n\n"
        f"Quantidade pedida: {r['quantidade']}\n"
        f"Selecionadas: {len(selecionadas)} categoria(s) · {disponiveis} ofertas"
    )
    return texto, InlineKeyboardMarkup(linhas)


def tela_horario(token: str):
    linhas = [
        [
            _botao("🕐 Sim, escolher horário", token, "hor:sim"),
            _botao("▶️ Não, começar agora", token, "hor:nao"),
        ],
        _cancelar(token),
    ]
    return "⏰ Quer um horário marcado para começar?", InlineKeyboardMarkup(linhas)


def tela_horas(token: str, aviso: str = ""):
    botoes = [_botao(f"{h:02d}:00", token, f"h:{h}") for h in HORARIOS]
    linhas = [botoes[i:i + 4] for i in range(0, len(botoes), 4)]
    linhas.append(_cancelar(token))
    return f"{aviso}🕐 Escolha o horário de início:", InlineKeyboardMarkup(linhas)


def calcular_inicio(hora: int | None) -> datetime:
    agora = datetime.now(TZ)
    if hora is None:
        return agora + timedelta(seconds=3)

    inicio = agora.replace(hour=hora, minute=0, second=0, microsecond=0)
    if inicio <= agora:  # já passou hoje: vale para amanhã
        inicio += timedelta(days=1)
    return inicio


def tela_resumo(token: str, r: dict):
    ids, inicio = r["ids"], r["inicio"]
    n = len(ids)
    fim = inicio + (n - 1) * INTERVALO

    aviso = ""
    if n < r["quantidade"]:
        aviso = f"\n⚠️ Só há {n} oferta(s) disponível(is) (pedido: {r['quantidade']})."

    texto = (
        "📋 Resumo do envio\n\n"
        f"📦 Ofertas: {n}{aviso}\n"
        f"📂 Categorias: {', '.join(_nome(c) for c in _ordem(r['categorias']))}\n"
        f"⏰ Início: {'agora' if r['hora'] is None else _rotulo(inicio)}\n"
        f"🏁 Término previsto: {_rotulo(fim)}\n"
        f"⏱️ Ritmo: 1 oferta a cada {int(INTERVALO.total_seconds() // 60)} min\n\n"
        "Confirmar o agendamento?"
    )
    linhas = [[_botao("✅ Confirmar envio", token, "go"), _botao("❌ Cancelar", token, "x")]]
    return texto, InlineKeyboardMarkup(linhas)


# =========================================================
# ESTADO EM MEMÓRIA
# =========================================================

def _rascunhos(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault(RASCUNHOS_KEY, {})


def _lotes(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault(LOTES_KEY, {})


def _limpar_expirados(rascunhos: dict) -> None:
    agora = time.time()
    for token in [t for t, v in rascunhos.items() if agora - v["ts"] > TTL_RASCUNHO]:
        del rascunhos[token]


async def _editar(query, texto: str, teclado=None) -> None:
    try:
        await query.edit_message_text(texto, reply_markup=teclado)
    except BadRequest as erro:
        if "not modified" not in str(erro).lower():
            raise


# =========================================================
# COMANDO /mandar_ofertas
# =========================================================

async def mandar_ofertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("Comando /mandar_ofertas | Usuário: %s | ID: %s", usuario.full_name, usuario.id)

    if not usuario_autorizado(usuario.id):
        logger.warning("Acesso negado ao /mandar_ofertas | Usuário: %s", usuario.id)
        await msg.reply_text("Você não tem permissão para executar esse comando.")
        return

    if context.job_queue is None:
        await msg.reply_text(
            "O agendamento não está disponível. Instale:\n"
            'pip install "python-telegram-bot[job-queue]"'
        )
        return

    try:
        contagens = contar_por_categoria()
    except sqlite3.Error:
        logger.exception("Erro ao contar ofertas | Usuário: %s", usuario.id)
        await msg.reply_text("Erro ao consultar as ofertas. Verifique as logs.")
        return

    total = sum(contagens.values())
    if not total:
        await msg.reply_text("Não existem ofertas cadastradas.")
        return

    rascunhos = _rascunhos(context)
    _limpar_expirados(rascunhos)
    token = uuid.uuid4().hex[:8]
    r = {
        "autor_id": usuario.id,
        "ts": time.time(),
        "quantidade": None,
        "categorias": set(),
        "hora": None,
        "ids": [],
        "inicio": None,
    }
    rascunhos[token] = r

    # atalho: /mandar_ofertas 25 já define a quantidade
    if context.args:
        try:
            quantidade = int(context.args[0])
            if not 1 <= quantidade <= MAX_QUANTIDADE:
                raise ValueError
        except ValueError:
            rascunhos.pop(token, None)
            await msg.reply_text(
                f"Use: /mandar_ofertas ou /mandar_ofertas N (de 1 a {MAX_QUANTIDADE})."
            )
            return
        r["quantidade"] = quantidade
        texto, teclado = tela_categorias(token, r, contagens)
    else:
        texto, teclado = tela_quantidade(token, total)

    await msg.reply_text(texto, reply_markup=teclado)


# =========================================================
# BOTÕES DO ASSISTENTE
# =========================================================

async def callback_mandar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    usuario = query.from_user
    _, token, acao, *resto = query.data.split(":")

    rascunhos = _rascunhos(context)
    r = rascunhos.get(token)

    if r is None:
        await query.answer()
        await _editar(query, "⌛ Essa solicitação expirou. Use /mandar_ofertas novamente.")
        return

    if usuario.id != r["autor_id"]:
        await query.answer("Só quem iniciou pode usar esses botões.", show_alert=True)
        return

    if not usuario_autorizado(usuario.id):
        rascunhos.pop(token, None)
        await query.answer("Você não tem mais permissão.", show_alert=True)
        return

    if acao == "ok" and not r["categorias"]:
        await query.answer("Selecione ao menos uma categoria.", show_alert=True)
        return

    await query.answer()
    r["ts"] = time.time()

    try:
        # ---- cancelar ----
        if acao == "x":
            rascunhos.pop(token, None)
            await _editar(query, "❌ Envio cancelado. Nada foi agendado.")

        # ---- 1. quantidade ----
        elif acao == "qtd":
            r["quantidade"] = int(resto[0])
            texto, teclado = tela_categorias(token, r, contar_por_categoria())
            await _editar(query, texto, teclado)

        # ---- 2. categorias ----
        elif acao in ("cat", "todas", "limpar"):
            contagens = contar_por_categoria()
            validas = {c for c, n in contagens.items() if n > 0}

            if acao == "cat":
                cid = int(resto[0])
                if cid in validas:
                    r["categorias"] ^= {cid}  # alterna marcada / desmarcada
            elif acao == "todas":
                r["categorias"] = set(validas)
            else:
                r["categorias"] = set()

            texto, teclado = tela_categorias(token, r, contagens)
            await _editar(query, texto, teclado)

        elif acao == "ok":
            texto, teclado = tela_horario(token)
            await _editar(query, texto, teclado)

        # ---- 3. horário ----
        elif acao == "hor" and resto[0] == "sim":
            texto, teclado = tela_horas(token)
            await _editar(query, texto, teclado)

        elif acao in ("hor", "h"):
            r["hora"] = None if acao == "hor" else int(resto[0])
            if r["hora"] is not None and r["hora"] not in HORARIOS:
                return

            r["ids"] = selecionar_ofertas(r["categorias"], r["quantidade"])
            if not r["ids"]:
                rascunhos.pop(token, None)
                await _editar(query, "Nenhuma oferta disponível nas categorias escolhidas.")
                return

            r["inicio"] = calcular_inicio(r["hora"])
            texto, teclado = tela_resumo(token, r)
            await _editar(query, texto, teclado)

        # ---- 4. confirmar e agendar ----
        elif acao == "go":
            if r["hora"] is not None and r["inicio"] <= datetime.now(TZ):
                texto, teclado = tela_horas(token, "⚠️ Esse horário já passou.\n\n")
                await _editar(query, texto, teclado)
                return

            # remove antes de agendar: um duplo clique não agenda duas vezes
            rascunhos.pop(token, None)
            await agendar_lote(query, context, r)

    except sqlite3.Error:
        logger.exception("Erro de banco no assistente | Usuário: %s", usuario.id)
        await _editar(query, "Erro ao consultar o banco de dados. Verifique as logs.")


async def agendar_lote(query, context: ContextTypes.DEFAULT_TYPE, r: dict) -> None:
    ids = r["ids"]
    inicio = r["inicio"] if r["hora"] is not None else calcular_inicio(None)
    lote_id = uuid.uuid4().hex[:8]

    _lotes(context)[lote_id] = {
        "autor_id": r["autor_id"],
        "chat_id": query.message.chat_id,
        "total": len(ids),
        "enviadas": 0,
        "falhas": 0,
        "puladas": 0,
        "erro_contador": 0,
    }

    for i, id_produto in enumerate(ids):
        context.job_queue.run_once(
            job_enviar,
            when=inicio + i * INTERVALO,
            data={"lote": lote_id, "id": id_produto},
            name=f"envio:{lote_id}",
            job_kwargs={"misfire_grace_time": 600},
        )

    fim = inicio + (len(ids) - 1) * INTERVALO
    logger.info(
        "Lote agendado | lote=%s | autor=%s | ofertas=%d | início=%s | fim=%s | grupo=%s",
        lote_id, r["autor_id"], len(ids), inicio.isoformat(), fim.isoformat(), GRUPO_ID,
    )

    await _editar(
        query,
        f"✅ Agendado! {len(ids)} oferta(s), 1 a cada 10 min.\n\n"
        f"⏰ Início: {'agora' if r['hora'] is None else _rotulo(inicio)}\n"
        f"🏁 Término previsto: {_rotulo(fim)}\n\n"
        "Para parar o envio, use /cancelar_envio.",
    )


# =========================================================
# ENVIO AGENDADO (1 JOB POR OFERTA)
# =========================================================

async def job_enviar(context: ContextTypes.DEFAULT_TYPE):
    dados = context.job.data
    lotes = _lotes(context)
    lote = lotes.get(dados["lote"])
    if lote is None:  # lote cancelado
        return

    oferta = buscar_oferta(dados["id"])

    if oferta is None:
        lote["puladas"] += 1
        logger.warning("Oferta removida antes do envio | ID: %s", dados["id"])
    else:
        enviada = False
        try:
            await enviar_oferta(context.bot, oferta)
            enviada = True
            lote["enviadas"] += 1
            logger.info(
                "Oferta enviada | lote=%s | ID: %s | Produto: %s | Cadastrada por: %s",
                dados["lote"], oferta["ID_PRODUTO"], oferta["nome_do_produto"],
                oferta["afiliado_que_adicionou"],
            )
        except Exception:
            lote["falhas"] += 1
            logger.exception("Falha ao enviar oferta ID %s ao grupo %s", dados["id"], GRUPO_ID)

        if enviada:
            try:
                registrar_envio(oferta)
            except Exception:
                lote["erro_contador"] += 1
                logger.exception(
                    "Oferta ID %s enviada, mas os contadores não foram atualizados.",
                    dados["id"],
                )

    processadas = lote["enviadas"] + lote["falhas"] + lote["puladas"]
    if processadas >= lote["total"]:
        lotes.pop(dados["lote"], None)

        texto = (
            "🏁 Envio concluído!\n\n"
            f"✅ Enviadas: {lote['enviadas']}\n"
            f"❌ Falhas: {lote['falhas']}\n"
            f"⏭️ Puladas (removidas do banco): {lote['puladas']}"
        )
        if lote["erro_contador"]:
            texto += (
                f"\n⚠️ {lote['erro_contador']} oferta(s) enviada(s) sem atualizar "
                "os contadores. Verifique as logs."
            )
        try:
            await context.bot.send_message(lote["chat_id"], texto)
        except Exception:
            logger.exception("Falha ao enviar o resumo do lote %s", dados["lote"])


# =========================================================
# COMANDO /cancelar_envio
# =========================================================

async def cancelar_envio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    msg = update.effective_message

    logger.info("Comando /cancelar_envio | Usuário: %s", usuario.id)

    if not usuario_autorizado(usuario.id):
        await msg.reply_text("Você não tem permissão para executar esse comando.")
        return

    lotes = _lotes(context)
    meus = [(lid, l) for lid, l in lotes.items() if l["autor_id"] == usuario.id]

    if not meus:
        await msg.reply_text("Você não tem nenhum envio em andamento.")
        return

    linhas = []
    for lote_id, lote in meus:
        if context.job_queue:
            for job in context.job_queue.get_jobs_by_name(f"envio:{lote_id}"):
                job.schedule_removal()
        lotes.pop(lote_id, None)

        processadas = lote["enviadas"] + lote["falhas"] + lote["puladas"]
        cancelada = lote["total"] - processadas
        linhas.append(
            f"• Lote {lote_id}: {lote['enviadas']} enviada(s), {cancelada} cancelada(s)"
        )
        logger.info(
            "Lote cancelado | lote=%s | autor=%s | enviadas=%d | canceladas=%d",
            lote_id, usuario.id, lote["enviadas"], cancelada,
        )

    await msg.reply_text("🛑 Envio cancelado.\n\n" + "\n".join(linhas))