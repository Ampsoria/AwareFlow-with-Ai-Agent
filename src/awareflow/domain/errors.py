class WorkflowError(Exception):
    """Safe, user-facing domain error; do not include secrets in messages."""


class ValidationError(WorkflowError):
    pass


class Forbidden(WorkflowError):
    pass


class NotFound(WorkflowError):
    pass


class Conflict(WorkflowError):
    pass


class InvalidTransition(Conflict):
    pass


class NoSuitableSources(WorkflowError):
    pass


class AgentNeedsInput(WorkflowError):
    pass


class ProviderConfigurationError(WorkflowError):
    pass


class ProviderUnavailable(WorkflowError):
    pass


class TransientProviderError(ProviderUnavailable):
    pass
