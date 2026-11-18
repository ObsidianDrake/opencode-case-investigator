"""Console and daily file logging for the standalone service."""
import logging
from datetime import date
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class DailyLog(TimedRotatingFileHandler):
    def getFilesToDelete(self):
        active = Path(self.baseFilename)
        oldest = date.today().toordinal() - self.backupCount
        expired = []
        for path in active.parent.glob(active.name + ".*"):
            suffix = path.name[len(active.name) + 1:]
            if self.extMatch.fullmatch(suffix) and path.is_file():
                try:
                    archived = date.fromisoformat(suffix)
                except ValueError:
                    continue
                if archived.toordinal() < oldest:
                    expired.append(str(path))
        return expired


def configure_logging(settings):
    directory = Path(settings.get("LOG_DIR") or Path(settings["CASE_STATE"]) / "logs")
    days = int(settings.get("LOG_RETENTION_DAYS", 14))
    if days < 1:
        raise ValueError("logging.retention_days must be a positive integer")
    directory.mkdir(parents=True, exist_ok=True)
    handler = DailyLog(directory / "case-investigator.log", when="midnight",
                       backupCount=days, encoding="utf-8")
    for expired in handler.getFilesToDelete():
        Path(expired).unlink(missing_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), handler],
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s %(message)s",
                        force=True)
    logging.getLogger(__name__).info("Logging to %s (daily rotation, retention=%d days)",
                                     handler.baseFilename, days)
