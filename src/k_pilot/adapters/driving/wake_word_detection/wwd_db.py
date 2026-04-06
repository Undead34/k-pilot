import sqlite3
import time

from k_pilot.core.shared.paths import paths

DB_PATH = paths.data_dir / "kpilot_memory.db"


def init_db():
    """
    Inicializa la base de datos si no existe.
    Crea la tabla para rastrear audios y sus puntuaciones.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wakeword_tracker (
            audio_id TEXT PRIMARY KEY,
            category TEXT,              -- 'base', 'core', 'hard_negative'
            distance REAL,
            score INTEGER DEFAULT 50,
            created_at REAL
        )
    """)
    conn.commit()
    conn.close()


def register_audio(audio_id, category, distance):
    """
    Guarda un nuevo registro de audio en la base de datos.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO wakeword_tracker
        (audio_id, category, distance, created_at)
        VALUES (?, ?, ?, ?)
    """,
        (audio_id, category, distance, time.time()),
    )
    conn.commit()
    conn.close()


def update_score(audio_id, points):
    """
    Suma o resta puntos a un audio específico (Active Learning).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE wakeword_tracker
        SET score = score + ?
        WHERE audio_id = ?
    """,
        (points, audio_id),
    )
    conn.commit()
    conn.close()
