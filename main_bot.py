from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler
    )
from config import TELEGRAM_TOKEN as TOKEN

# Get the application

app = Application.builder().token(TOKEN).build()

# Get the /listar_admins
from comandos.listar_adms import listar_admins
app.add_handler(CommandHandler("listar_cargos", listar_admins))

# Add an ADM /addadmin
from comandos.add_admins import addadmin
app.add_handler(CommandHandler("add_cargo", addadmin))

# Remove an ADM /remover_admin
from comandos.remover_admins import remover_admin
app.add_handler(CommandHandler("remover_cargo", remover_admin))

# Buscar Oferta
from comandos.buscar_oferta import buscar_oferta, callback_oferta
app.add_handler(CommandHandler("buscar_oferta", buscar_oferta))
app.add_handler(CallbackQueryHandler(callback_oferta, pattern=r"^of:"))

# Remover oferta /remover_oferta
from comandos.remover_ofertas import callback_remover, remover_oferta
app.add_handler(CommandHandler("remover_oferta", remover_oferta))
app.add_handler(CallbackQueryHandler(callback_remover, pattern=r"^ro:"))

# Listar ofertas /listar_ofertas
from comandos.listar_ofertas import callback_listar, listar_ofertas  
app.add_handler(CommandHandler("listar_ofertas", listar_ofertas))
app.add_handler(CallbackQueryHandler(callback_listar, pattern=r"^lo:"))

# Listar todas as ofertas
from comandos.listar_ofertas_all import listar_ofertas_all  # ajuste ao caminho real
app.add_handler(CommandHandler("listar_ofertas_all", listar_ofertas_all))

# Mandar ofertas
from comandos.mandar_ofertas import callback_mandar, cancelar_envio, mandar_ofertas  
app.add_handler(CommandHandler("mandar_ofertas", mandar_ofertas))
app.add_handler(CallbackQueryHandler(callback_mandar, pattern=r"^mo:"))
app.add_handler(CommandHandler("cancelar_envio", cancelar_envio))



app.run_polling()