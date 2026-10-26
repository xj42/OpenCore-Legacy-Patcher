"""
legacy_kexts.py: Relocate obsolete extensions before rebuilding kernel collections.
"""

import logging

from datetime import datetime
from pathlib import Path
from typing import Callable


def relocate_legacy_kexts(mount_location_data: str, run_as_root: Callable) -> None:
    """
    Preserve pre-October 2021 extensions outside the kext search path.

    In particular, an old AppleMobileDevice.kext in /Library/Extensions can
    override Apple's current codeless bundle when rebuilding the Boot KC.
    Its placeholder IOService personalities prevent the composite USB driver
    from configuring iPhones on newer macOS releases.

    The caller supplies the privileged, checked command runner so that a
    failed relocation cannot silently produce another conflicting cache.
    """
    # Root patching represents the live Data volume with an empty string.
    data_root = Path(mount_location_data or "/")
    extensions_path = data_root / "Library/Extensions"
    relocation_path = data_root / "Library/Relocated Extensions"

    for extension in extensions_path.glob("*.kext"):
        try:
            # Do not follow migrated or broken symlinks out of Extensions.
            if extension.is_symlink() or not extension.is_dir():
                continue
            if datetime.fromtimestamp(extension.stat().st_mtime) >= datetime(2021, 10, 1):
                continue
        except OSError as error:
            logging.warning(f"  - Unable to inspect {extension}: {error}")
            continue

        if not relocation_path.exists():
            run_as_root(["/bin/mkdir", "-p", str(relocation_path)])

        destination = relocation_path / extension.name
        # Keep previous backups, including broken symlinks, intact.
        suffix = 1
        while destination.exists() or destination.is_symlink():
            destination = relocation_path / f"{extension.stem}-{suffix}{extension.suffix}"
            suffix += 1

        logging.info(f"  - Relocating {extension.name} kext to {destination}")
        run_as_root(["/bin/mv", str(extension), str(destination)])
