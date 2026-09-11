import os
import shutil
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

APP_DATA_FOLDER = "QS_Ajuda_Custos"
LEGACY_DB_NAME = "qs_ajuda_custos_v081.db"
DB_NAME = "qs_ajuda_custos.db"


def _resolve_data_dir() -> Path:
    override = os.getenv("QS_AJUDA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / APP_DATA_FOLDER
    return Path.home() / ".qs_ajuda_custos"


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / DB_NAME
MIGRATION_SOURCE = None


def _legacy_candidates() -> list[Path]:
    cwd = Path.cwd().resolve()
    candidates = [cwd / LEGACY_DB_NAME]
    parent = cwd.parent
    candidates.extend([
        parent / "qs-ajuda-custos-v081" / LEGACY_DB_NAME,
        parent / "qs-ajuda-custos-v08" / "qs_ajuda_custos_v08.db",
    ])
    try:
        candidates.extend(parent.glob(f"*/{LEGACY_DB_NAME}"))
    except OSError:
        pass
    # Deduplica preservando somente arquivos existentes.
    unique = {}
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.resolve() != DB_PATH.resolve():
                unique[str(candidate.resolve())] = candidate.resolve()
        except OSError:
            continue
    return list(unique.values())


if not DB_PATH.exists():
    candidates = _legacy_candidates()
    if candidates:
        source = max(candidates, key=lambda p: p.stat().st_mtime)
        shutil.copy2(source, DB_PATH)
        MIGRATION_SOURCE = str(source)

DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
