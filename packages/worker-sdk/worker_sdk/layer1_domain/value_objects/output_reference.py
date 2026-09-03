from dataclasses import dataclass
from typing import Optional

from worker_sdk.layer1_domain.value_objects.data_metadata import DataMetadata


@dataclass
class OutputReference:
    uri: str
    metadata: Optional[DataMetadata] = None
