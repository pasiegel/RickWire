"""Add RickWire's entries to the PyInstaller-made RickWire.app/Contents/Info.plist (run before code signing).

    python packaging/mac/info_plist.py dist/RickWire.app 0.2.8
"""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

BUNDLE_ID = "app.rickwire.RickWire"   # must match rickwire/macos.py
TORRENT_UTI = "org.bittorrent.torrent"


def patch(app: Path, version: str) -> None:
    path = app / "Contents" / "Info.plist"
    with path.open("rb") as fh:
        info = plistlib.load(fh)
    info.update({
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "RickWire",
        "CFBundleDisplayName": "RickWire",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "RickWire",
        # magnet: links
        "CFBundleURLTypes": [{"CFBundleURLName": "Magnet link", "CFBundleURLSchemes": ["magnet"],
                              "CFBundleTypeRole": "Viewer"}],
        # .torrent files (the type is declared here too, in case no torrent app has declared it yet)
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "BitTorrent file", "CFBundleTypeRole": "Viewer",
                                   "LSHandlerRank": "Owner", "LSItemContentTypes": [TORRENT_UTI],
                                   "CFBundleTypeExtensions": ["torrent"]}],
        "UTImportedTypeDeclarations": [{
            "UTTypeIdentifier": TORRENT_UTI, "UTTypeDescription": "BitTorrent file",
            "UTTypeConformsTo": ["public.data"],
            "UTTypeTagSpecification": {"public.filename-extension": ["torrent"],
                                       "public.mime-type": ["application/x-bittorrent"]}}],
        # shown by macOS when RickWire first talks to the home network (paired phones, NAS download folders)
        "NSLocalNetworkUsageDescription": "RickWire serves the phones you pair with it on your home network.",
    })
    with path.open("wb") as fh:
        plistlib.dump(info, fh)


if __name__ == "__main__":
    patch(Path(sys.argv[1]), sys.argv[2])
    print("Info.plist updated")
