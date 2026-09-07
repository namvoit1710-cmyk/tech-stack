import logging
import sys
from app.layer2_application.interfaces.logger_interface import ILogger


class AppLogger(ILogger):
    def __init__(self):
        #  Added [%(filename)s:%(lineno)d] for highly detailed terminal output
        logging.basicConfig(
            level=logging.DEBUG,  # Changed to DEBUG to capture deep details
            format='%(asctime)s | %(levelname)-8s | [%(filename)s:%(lineno)d] | %(name)s | %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger("App")

    def info(self, message: str, **kwargs) -> None:
        self.logger.info(f"{message} {kwargs if kwargs else ''}")

    def error(self, message: str, **kwargs) -> None:
        self.logger.error(f"{message} {kwargs if kwargs else ''}")

    def debug(self, message: str, **kwargs) -> None:
        self.logger.debug(f"{message} {kwargs if kwargs else ''}")

    def warning(self, message: str, **kwargs) -> None:
        self.logger.warning(f"{message} {kwargs if kwargs else ''}")
