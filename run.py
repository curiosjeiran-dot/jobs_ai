from telegram_parser.config import Config

if __name__ == "__main__":
    import uvicorn

    config = Config.from_env()
    uvicorn.run(
        "telegram_parser.service:app",
        host=config.host,
        port=config.port,
        reload=False,
    )
