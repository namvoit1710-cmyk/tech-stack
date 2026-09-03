from dataclasses import dataclass, field
from typing import List


@dataclass
class ConversationMetadata:
    main_conv_id: str = ""
    sub_conv_ids: List[str] = field(default_factory=list)
    uploaded_file_ids: List[str] = field(default_factory=list)
