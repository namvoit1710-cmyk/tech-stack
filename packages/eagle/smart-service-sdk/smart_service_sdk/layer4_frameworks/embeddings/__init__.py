from typing import TYPE_CHECKING

__all__ = ["LocalEmbeddingProvider", "SimpleEmbeddingProvider"]


if TYPE_CHECKING:
    from smart_service_sdk.layer4_frameworks.embeddings.local_embedding_provider import (
        LocalEmbeddingProvider,
    )
    from smart_service_sdk.layer4_frameworks.embeddings.simple_embedding_provider import (
        SimpleEmbeddingProvider,
    )


def __getattr__(name: str):
    if name == "LocalEmbeddingProvider":
        from smart_service_sdk.layer4_frameworks.embeddings.local_embedding_provider import (
            LocalEmbeddingProvider,
        )

        return LocalEmbeddingProvider
    if name == "SimpleEmbeddingProvider":
        from smart_service_sdk.layer4_frameworks.embeddings.simple_embedding_provider import (
            SimpleEmbeddingProvider,
        )

        return SimpleEmbeddingProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
