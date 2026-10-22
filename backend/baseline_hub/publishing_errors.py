"""Errors shared by catalog readers and restartable publication operations."""


class PublishingError(Exception):
    def __init__(self, message, status="failed", retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
