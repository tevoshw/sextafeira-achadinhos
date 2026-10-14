
import sqlite3
from telegram import Update
from telegram.ext import ContextTypes
from logs.init import logger

async def addadmin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    usuario = update.effective_user

    logger.warning(
        "COMANDO /add_cargo RECEBIDO | usuario_id=%s",
        usuario.id
    )

    # Apenas OWNER pode adicionar administradores
    conn_admins = sqlite3.connect("bd/admins.db")
    conn_admins.row_factory = sqlite3.Row

    try:
        autor = conn_admins.execute(
            "SELECT ROLE FROM admins WHERE ID = ?",
            (usuario.id,)
        ).fetchone()

        if autor is None or autor["ROLE"] not in ("OWNER", "ADMIN"):
            await update.effective_message.reply_text(
                "Você não tem autorização para adicionar cargos."
            )
            return

        # /addadmin USER_ID NOME CARGO
        if len(context.args) < 3:
            await update.effective_message.reply_text(
                "Use assim:\n"
                "/add_cargo USER_ID NOME CARGO\n\n"
                "Exemplo:\n"
                "/add_cargo 123456789 João ADMIN"
            )
            return

        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text(
                "O USER_ID precisa ser um número inteiro."
            )
            return

        nome = context.args[1]
        cargo = context.args[2].upper()

        cargos_validos = {"OWNER", "ADMIN", "AFILIADO"}

        if cargo not in cargos_validos:
            await update.effective_message.reply_text(
                "Cargo inválido. Use OWNER, ADMIN ou AFILIADO."
            )
            return

        conn_admins.execute(
            """
            INSERT INTO admins (ID, Nome, ROLE, Quem_adicionou)
            VALUES (?, ?, ?, ?)
            """,(user_id, nome, cargo, usuario.full_name)
        )
        
        conn_admins.commit()

        await update.effective_message.reply_text(
            f"Usuário cadastrado!\n\n"
            f"ID: {user_id}\n"
            f"Nome: {nome}\n"
            f"Cargo: {cargo}\n"
            f"Quem adicionou: {usuario.full_name}\n"
            f"Data: hoje"
        )

    except sqlite3.IntegrityError:
        await update.effective_message.reply_text(
            "Esse USER_ID já está cadastrado no banco."
        )

    finally:
        conn_admins.close()

