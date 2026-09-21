"""Exceptions entities for the application."""


class NotFoundException(Exception):
    def __init__(self, entity_name: str, message: str = "Not Found"):
        self.detail = f"{entity_name} {message}"


class UnauthorizedException(Exception):
    def __init__(self, message: str = "Unauthorized"):
        self.detail = message


class ForbiddenException(Exception):
    def __init__(self, message: str = "Forbidden"):
        self.detail = message


class ConflictException(Exception):
    def __init__(self, entity_name: str, message: str = "Conflict"):
        self.detail = f"{entity_name} {message}"
