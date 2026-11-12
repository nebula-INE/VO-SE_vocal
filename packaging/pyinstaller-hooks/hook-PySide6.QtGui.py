"""QtGui hook shared by Linux validation and release bundles.

The VO-SE desktop application has no PDF-as-image functionality. PyInstaller's
standard QtGui hook collects every imageformats plugin, including QtPdf's qpdf
plugin. The baseline ELF audit showed that plugin was the sole direct packaged
consumer of libQt6Pdf.so.6.

Keep the standard Qt dependency analysis and all other image formats. Exclude
only the optional qpdf image-format plugin; the archive audit verifies that its
orphaned QtPdf library is also absent.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

kept_binaries = []
removed_pdf_plugins = []
for entry in binaries:
    source, destination = entry
    basename = Path(source).name.casefold()
    if basename == "libqpdf.so" or basename.startswith("libqt6pdf.so"):
        removed_pdf_plugins.append((source, destination))
    else:
        kept_binaries.append(entry)

binaries = kept_binaries
print(
    "Linux QtGui hook: omitted optional PDF image payload entries: "
    f"{[Path(source).name for source, _destination in removed_pdf_plugins]}"
)
