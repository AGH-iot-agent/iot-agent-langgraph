import logging
import os
import sys
import threading


def _resolve_log_file() -> tuple[str, str]:
    explicit = (
        os.environ.get("DEVOPS_AGENT_LOG_FILE") or os.environ.get("LOG_FILE") or ""
    ).strip()
    if explicit:
        log_file = os.path.abspath(explicit)
        return os.path.dirname(log_file), log_file
    log_dir = os.path.abspath(os.environ.get("DEVOPS_AGENT_LOG_DIR", "logs"))
    return log_dir, os.path.join(log_dir, "agent.log")


_LOG_DIR, _LOG_FILE = _resolve_log_file()


def _configure_logging() -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    log_level = os.environ.get("DEVOPS_AGENT_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    )

    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


def _register_uncaught_exception_hooks() -> None:
    def _sys_hook(exc_type, exc_value, exc_tb):
        logging.getLogger("devops_agent.unhandled").exception(
            "Unhandled process exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("devops_agent.unhandled").exception(
            "Unhandled thread exception: thread=%s", args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


_configure_logging()
_register_uncaught_exception_hooks()

__all__ = [
    "graph",
]
