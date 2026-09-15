from worker_sdk.layer2_application.interfaces.storage_interface import IStorage


class LocalStorage(IStorage):
    def save(self, filename: str, data: bytes) -> None:
        print(f"[DISK] Saving {filename}")

    def get(self, filename: str) -> bytes:
        return b"fake_content"
