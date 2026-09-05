from __future__ import annotations
import logging
from starlette.applications import Starlette
from mail_archive_server.config import load_config
from mail_archive_server.http import create_app
from mail_archive_server.indexer import reindex
from mail_archive_server.store import open_db

logger = logging.getLogger("mail_archive_server")


def bootstrap(env: dict[str, str] | None = None) -> Starlette:
    config = load_config(env)
    conn = open_db(config.db_path)
    logger.info("indexing %s at startup", config.maildir_path)
    reindex(conn, config.maildir_path)
    return create_app(config, conn, config.maildir_path)


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    app = bootstrap()
    config = app.state.config
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
