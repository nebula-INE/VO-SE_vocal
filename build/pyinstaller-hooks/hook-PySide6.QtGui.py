"""Custom QtGui hook for the Linux validation-only bundle.

The application does not use Qt PDF or advertise PDF image import. PyInstaller's
QtGui hook collects every imageformats plugin, including QtPdf's qpdf plugin.
That plugin is the sole packaged DT_NEEDED consumer of libQt6Pdf.so.6.

Keep all other image format plugins and remove only the qpdf plugin plus its
now-orphaned QtPdf shared library. The archive audit and GUI startup smoke test
in validate-runtime-package.yml verify the result.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

kept_binaries = []
removed_pdf_binaries = []
for entry in binaries:
    source, destination = entry
    basename = Path(source).name.casefold()
    if basename == "libqpdf.so" or basename.startswith("libqt6pdf.so"):
        removed_pdf_binaries.append((source, destination))
    else:
        kept_binaries.append(entry)

binaries = kept_binaries
print(
    "Linux validation QtGui hook: removed PDF-only plugin/library entries: "
    f"{[Path(source).name for source, _destination in removed_pdf_binaries]}"
)
