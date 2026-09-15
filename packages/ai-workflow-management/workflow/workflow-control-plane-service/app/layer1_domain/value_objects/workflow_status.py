from enum import Enum


class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    PUBLISHED = "PUBLISHED"
    DEPLOYED = "DEPLOYED"
    ARCHIVED = "ARCHIVED"
