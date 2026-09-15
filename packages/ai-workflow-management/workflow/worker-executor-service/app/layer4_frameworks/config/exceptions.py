class AppError(Exception):
    pass


class TaskExecutionError(AppError):
    pass


class WorkerNotFoundError(AppError):
    pass


class RetryExhaustedError(AppError):
    pass
