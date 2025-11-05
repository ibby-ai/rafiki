"""Utilities for Modal Volume operations and sandbox management."""

from typing import Iterable
import modal


def get_session_volume(name: str) -> modal.Volume:
    """Get or create a Modal Volume by name.
    
    Args:
        name: Volume identifier.
        
    Returns:
        A Modal Volume instance.
    """
    return modal.Volume.from_name(name, create_if_missing=True)


def upload_paths_to_volume(
    vol: modal.Volume, 
    local_paths: Iterable[str], 
    dest_prefix: str = ""
) -> None:
    """Upload local file paths to a Modal Volume.
    
    Args:
        vol: Target Modal Volume.
        local_paths: Iterable of local file/directory paths to upload.
        dest_prefix: Optional prefix for remote paths.
    """
    items = []
    for p in local_paths:
        rp = f"{dest_prefix.rstrip('/')}/" if dest_prefix else ""
        items.append((p, rp))
    vol.batch_upload(items)

