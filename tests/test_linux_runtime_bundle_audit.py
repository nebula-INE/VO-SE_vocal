from scripts.audit_linux_runtime_bundle import (
    calculate_reduction_percent,
    classify_entry,
    parse_needed_libraries,
)


def test_classify_runtime_payload_by_function() -> None:
    assert classify_entry("pyz.pyz") == "python_bytecode"
    assert classify_entry("numpy.libs/libopenblas.so") == "numerical_python"
    assert classify_entry("onnxruntime/capi/runtime.so") == "onnx_runtime"
    assert classify_entry("pyopenjtalk/openjtalk.so") == "japanese_speech"
    assert classify_entry("pyside6/qt/plugins/platforms/libqoffscreen.so") == "qt_plugins"
    assert classify_entry("pyside6/qt/lib/libavcodec.so.63") == "qt_multimedia_codecs"
    assert classify_entry("pyside6/qt/lib/libQt6Core.so.6") == "qt_runtime"


def test_parse_needed_libraries_ignores_other_dynamic_tags() -> None:
    output = """
 0x0000000000000001 (NEEDED)             Shared library: [libQt6Core.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
 0x0000000000000001 (NEEDED)             Shared library: [libstdc++.so.6]
"""
    assert parse_needed_libraries(output) == ["libQt6Core.so.6", "libstdc++.so.6"]


def test_calculate_reduction_percent_uses_byte_counts() -> None:
    assert calculate_reduction_percent(200, 150) == 25.0
    assert calculate_reduction_percent(0, 10) is None
    assert calculate_reduction_percent(100, 120) == -20.0
