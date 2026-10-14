
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from logs.init import logger


async def listar_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user
    agora = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")

    logger.info(
        "usuario_id=%s | data_hora=%s | comando=/listar_cargos",
        usuario.id,
        agora
    )

    # Verifica se o usuário está cadastrado
    with sqlite3.connect("bd/admins.db") as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT ROLE FROM admins WHERE ID = ?",
            (usuario.id,)
        )

        autor = cursor.fetchone()

        if autor is None or autor[0] not in ("ADMIN", "OWNER"):
            logger.warning(
                "usuario_id=%s | comando=/listar_admins | acesso_negado",
                usuario.id
            )
            await update.effective_message.reply_text(
                "Você não tem autorização. Apenas ADMINs e OWNERs podem usar este comando."
            )
            return

        # Busca todos os administradores e afiliados cadastrados
        cursor.execute("""
                SELECT
                    ID,
                    Nome,
                    ROLE,
                    Ofertas_Enviadas_no_Grupo,
                    Ofertas_Criadas,
                    Quem_adicionou,
                    Desde_quando_existe
                FROM admins
                ORDER BY Desde_quando_existe ASC
            """)

        admins = cursor.fetchall()

    if not admins:
        await update.message.reply_text("Nenhum usuário cadastrado.")
        return

    mensagem = "LISTA DE USUÁRIOS CADASTRADOS\n\n"

    for admin in admins:
        (id_usuario,
        nome,
        cargo,
        enviadas,
        criadas,
        quem_adicionou,
        data_cadastro
        ) = admin

        mensagem += (
            f"ID: {id_usuario}\n"
            f"Nome: {nome}\n"
            f"Cargo: {cargo}\n"
            f"Ofertas enviadas: {enviadas}\n"
            f"Ofertas criadas: {criadas}\n"
            f"Quem adicionou: {quem_adicionou or 'Sistema'}\n"
            f"Cadastrado em: {data_cadastro}\n"
            f"{'-' * 25}\n"
        )

    # Divide a resposta em partes para evitar o limite do Telegram
    for inicio in range(0, len(mensagem), 4000):
        await update.message.reply_text(
            mensagem[inicio:inicio + 4000]
            )
