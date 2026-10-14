
import sqlite3

# =========================
# BANCO DE OFERTAS
# =========================

conn_ofertas = sqlite3.connect("bd/ofertas.db")
conn_ofertas.execute("""
    CREATE TABLE ofertas (
        ID_PRODUTO INTEGER PRIMARY KEY AUTOINCREMENT,
        link TEXT NOT NULL,
        nome_do_produto TEXT NOT NULL,
        descricao TEXT,
        imagem TEXT,
        nota REAL,
        preco REAL,
        quantidade INTEGER DEFAULT 0,
        afiliado_que_adicionou INTEGER,
        desde_quando_existe TEXT DEFAULT CURRENT_TIMESTAMP,
        vezes_enviada INTEGER DEFAULT 0
    )
""")

conn_ofertas.commit()
conn_ofertas.close()


# =========================
# BANCO DE ADMINISTRADORES
# =========================

conn_admins = sqlite3.connect("bd/admins.db")

conn_admins.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            ID INTEGER PRIMARY KEY,
            Nome TEXT NOT NULL,
            ROLE TEXT NOT NULL,
            Ofertas_Enviadas_no_Grupo INTEGER DEFAULT 0,
            Ofertas_Criadas INTEGER DEFAULT 0,
            Quem_adicionou TEXT,
            Desde_quando_existe TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

admins = [
    (8696625816, "TEVO", "OWNER", 0, 0, "TEVO", "2026-10-14"),
    (6316870167, "MAG", "ADMIN", 0, 0, "TEVO", "2026-10-14")
]

conn_admins.executemany("""
    INSERT INTO admins (
        ID, Nome, ROLE,
        Ofertas_Enviadas_no_Grupo,
        Ofertas_Criadas,
        Quem_adicionou,
        Desde_quando_existe
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ID) DO UPDATE SET
        Nome = excluded.Nome,
        ROLE = excluded.ROLE,
        Quem_adicionou = excluded.Quem_adicionou
""", admins)

conn_admins.commit()

print("Bancos recriados, TEVO & MAD ADICIONADOS")
