from typing import Iterable
import modal

def get_session_volume(name: str) -> modal.Volume:
    return modal.Volume.from_name(name, create_if_missing=True)

def upload_paths_to_volume(vol: modal.Volume, local_paths: Iterable[str], dest_prefix: str = "") -> None:
    items = []
    for p in local_paths:
        rp = f"{dest_prefix.rstrip('/')}/" if dest_prefix else ""
        items.append((p, rp))
    vol.batch_upload(items)