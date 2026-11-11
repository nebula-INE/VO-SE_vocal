from modules.gui.mixins.project_io_mixin import ProjectIOMixin


def test_read_file_safely_decodes_utf8_bom(tmp_path):
    path = tmp_path / "utf8-bom.ust"
    expected = "[#SETTING]\nProjectName=つくよみちゃん\n"
    path.write_bytes(bytes((0xEF, 0xBB, 0xBF)) + expected.encode("utf-8"))

    assert ProjectIOMixin.read_file_safely(object(), str(path)) == expected


def test_read_file_safely_decodes_cp932_without_replacement(tmp_path):
    path = tmp_path / "cp932.ust"
    expected = "[#SETTING]\nProjectName=波音リツ\n"
    path.write_bytes(expected.encode("cp932"))

    assert ProjectIOMixin.read_file_safely(object(), str(path)) == expected
