import sqlite3
from telegram import Update
from telegram.ext import ContextTypes
from logs.init import logger

async def remover_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user

    logger.info(
        "COMANDO RECEBIDO | comando=/remover_cargo | usuario_id=%s",
        usuario.id
    )

    with sqlite3.connect("bd/admins.db") as conn:
        conn.row_factory = sqlite3.Row

        autor = conn.execute(
            "SELECT ROLE FROM admins WHERE ID = ?",
            (usuario.id,)
        ).fetchone()

        if autor is None or autor["ROLE"] not in ("OWNER", "ADMIN"):
            logger.warning(
                "ACESSO NEGADO | comando=/remover_cargo | usuario_id=%s",
                usuario.id
            )
            await update.effective_message.reply_text(
                "Você não tem autorização para remover usuários."
            )
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text(
                "Uso correto:\n/remover_cargo USER_ID"
            )
            return

        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text(
                "O USER_ID precisa ser um número inteiro."
            )
            return

        alvo = conn.execute(
            "SELECT Nome, ROLE FROM admins WHERE ID = ?",
            (user_id,)
        ).fetchone()

        if alvo is None:
            await update.effective_message.reply_text(
                "Esse usuário não está cadastrado."
            )
            return

        # Impede remover um OWNER
        if alvo["ROLE"] == "OWNER":
            await update.effective_message.reply_text(
                "Não é permitido remover um OWNER por este comando."
            )
            return

        # ADMIN não pode remover outro ADMIN
        if autor["ROLE"] == "ADMIN" and alvo["ROLE"] == "ADMIN":
            await update.effective_message.reply_text(
                "Um ADMIN não pode remover outro ADMIN."
            )
            return

        conn.execute(
            "DELETE FROM admins WHERE ID = ?",
            (user_id,)
        )
        conn.commit()

        logger.info(
            "USUARIO REMOVIDO | autor_id=%s | alvo_id=%s | nome=%s | cargo=%s",
            usuario.id,
            user_id,
            alvo["Nome"],
            alvo["ROLE"]
        )

        await update.effective_message.reply_text(
            f"Usuário removido com sucesso!\n\n"
            f"ID: {user_id}\n"
            f"Nome: {alvo['Nome']}\n"
            f"Cargo: {alvo['ROLE']}\n"
            f"Removido por: {usuario.full_name}"
        )
