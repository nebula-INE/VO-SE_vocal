# modules/gui/main_window_patch.py
"""
VO-SE Vocal — MainWindow への優先度1・2機能の統合パッチ

このファイルを app_main.py の VO_SE_Engine 生成直後に呼び出すことで、
既存の main_window.py を書き換えずに以下を有効化する。

優先度1 (致命的) の対応:
  [P1-1] VCV 連音: VcvResolver を engine に統合
  [P1-2] Oto.ini 先行発声: align_vocal_timing が oto_parser を参照するよう接続
  [P1-3] UST 完全対応: load_ust_file / export_as_ust を MainWindow に追加

優先度2 (重要) の対応:
  [P2-1] ビブラートカーブ: export_to_wav_v2 で NoteEvent/UST VBR を反映
  [P2-2] 漢字→ひらがな自動変換: on_lyric_input で convert_kanji_to_kana を挟む
  [P2-3] MIDI リアルタイム入力: midi_port_selector → MidiInputManager を接続

使い方 (app_main.py の末尾):
    from modules.gui.main_window_patch import patch_main_window
    patch_main_window(window)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # MainWindow の型ヒント用（循環インポート回避）

logger = logging.getLogger(__name__)


def patch_main_window(window) -> None:
    """
    MainWindow インスタンスにパッチを当てる。

    Args:
        window: MainWindow のインスタンス
    """
    _patch_engine(window)
    _patch_file_io(window)
    _patch_lyric_input(window)
    _patch_midi_realtime(window)
    _patch_menus(window)
    logger.info("MainWindow パッチ適用完了 (P1/P2)")


# ---------------------------------------------------------------------------
# [P1-1, P1-2] エンジン: VCV + 先行発声
# ---------------------------------------------------------------------------

def _patch_engine(window) -> None:
    """vo_se_engine に VCV リゾルバーと新 export を適用する"""
    engine = getattr(window, "vo_se_engine", None)
    if engine is None:
        logger.warning("vo_se_engine が見つかりません。エンジンパッチをスキップ。")
        return

    from modules.audio.vo_se_engine_patch import apply_patch
    apply_patch(type(engine))

    # 音源ライブラリを VCV 対応版で再スキャン
    engine.refresh_voice_library_v2()

    # export_to_wav_v2 は ProjectIOMixin が直接優先利用する。
    # export_to_wav 自体を monkey-patch すると mode_flag / progress / cancel の
    # シグネチャを壊してしまうため、既存APIは上書きしない。
    if not callable(getattr(engine, "export_to_wav_v2", None)):
        logger.warning("export_to_wav_v2 が利用できません。v1 export を維持します。")

    logger.info("エンジンパッチ: VCV + 先行発声 + ビブラートカーブ 適用")


# ---------------------------------------------------------------------------
# [P1-3] ファイル IO: UST 読み込み / 書き出し
# ---------------------------------------------------------------------------

def _patch_file_io(window) -> None:
    """load_ust_file / export_as_ust を window に追加する"""
    from modules.gui.mixins.project_io_mixin import ProjectIOMixin
    import types

    # ProjectIOMixin のメソッドを window に動的に追加
    for method_name in (
        "load_ust_file",
        "export_as_ust",
        "import_external_project",
        "save_file_dialog_and_save_midi",
        "load_json_project",
        "load_midi_file_from_path",
        "save_oto_ini",
        "_load_vsqx",
    ):
        method = getattr(ProjectIOMixin, method_name, None)
        if method is not None and not hasattr(window, method_name):
            setattr(window, method_name, types.MethodType(method, window))

    logger.info("ファイルIOパッチ: UST/MIDI/JSON 読み書き 適用")


# ---------------------------------------------------------------------------
# [P2-2] 歌詞入力: 漢字→ひらがな自動変換
# ---------------------------------------------------------------------------

def _patch_lyric_input(window) -> None:
    """
    タイムライン上でノートの歌詞を編集したとき、
    漢字が入力されていれば convert_kanji_to_kana() を通す。
    """
    timeline = getattr(window, "timeline_widget", None)
    if timeline is None:
        return

    text_analyzer = getattr(getattr(window, "vo_se_engine", None), "text_analyzer", None)
    if text_analyzer is None:
        return

    original_edit = getattr(timeline, "on_lyric_edit_finished", None)

    def _on_lyric_edit_finished_with_kana(lyric: str) -> str:
        converted = text_analyzer.convert_kanji_to_kana(lyric)
        if converted != lyric:
            logger.debug("歌詞変換: '%s' → '%s'", lyric, converted)
        if original_edit:
            return original_edit(converted)
        return converted

    if hasattr(timeline, "on_lyric_edit_finished"):
        import types
        timeline.on_lyric_edit_finished = types.MethodType(
            lambda self, lyric: _on_lyric_edit_finished_with_kana(lyric),
            timeline,
        )
        logger.info("歌詞入力パッチ: 漢字→ひらがな自動変換 適用")


# ---------------------------------------------------------------------------
# [P2-3] MIDI リアルタイム入力
# ---------------------------------------------------------------------------

def _patch_midi_realtime(window) -> None:
    """
    midi_port_selector の変更シグナルを MidiInputManager に接続する。
    """
    port_selector = getattr(window, "midi_port_selector", None)
    if port_selector is None:
        logger.debug("midi_port_selector が見つかりません。MIDI パッチをスキップ。")
        return

    from modules.data.midi_manager import MidiInputManager

    midi_mgr = MidiInputManager()
    window._midi_input_manager = midi_mgr  # GC 防止のため window に保持

    def _on_port_changed(port_name: str) -> None:
        """ポートセレクタ変更時に MIDI 入力を切り替える"""
        if window._midi_input_manager.port is not None:
            window._midi_input_manager.stop()

        if port_name and port_name not in ("なし", "None", ""):
            window._midi_input_manager.port_name = port_name
            window._midi_input_manager.start()
            window.statusBar().showMessage(f"MIDI 入力: {port_name}")
        else:
            window.statusBar().showMessage("MIDI 入力: 切断")

    # セレクタのシグナルに接続
    try:
        port_selector.currentTextChanged.connect(_on_port_changed)

        # 利用可能ポートを列挙してセレクタに追加
        ports = MidiInputManager.get_available_ports()
        port_selector.clear()
        port_selector.addItem("なし")
        for p in ports:
            port_selector.addItem(p)

        logger.info("MIDI リアルタイムパッチ: %d ポート検出", len(ports))
    except Exception as exc:
        logger.warning("MIDI パッチ失敗: %s", exc)


# ---------------------------------------------------------------------------
# メニューへの追加
# ---------------------------------------------------------------------------

def _patch_menus(window) -> None:
    """
    ファイルメニューに UST 読み込み / UST 書き出し を追加する。
    """
    menu_bar = getattr(window, "menuBar", None)
    if menu_bar is None:
        return

    try:
        from PySide6.QtGui import QAction

        # ファイルメニューを探す
        file_menu = None
        for action in menu_bar().actions():
            if "ファイル" in (action.text() or "") or "File" in (action.text() or ""):
                file_menu = action.menu()
                break

        if file_menu is None:
            return

        # セパレーター
        file_menu.addSeparator()

        # UST 読み込み
        act_import_ust = QAction("UTAU プロジェクト (.ust) を開く...", window)
        act_import_ust.triggered.connect(
            lambda: window.load_ust_file(
                __import__("PySide6.QtWidgets", fromlist=["QFileDialog"])
                .QFileDialog.getOpenFileName(window, "UST を開く", "", "UTAU (*.ust)")[0]
            ) if hasattr(window, "load_ust_file") else None
        )
        file_menu.addAction(act_import_ust)

        # UST 書き出し
        act_export_ust = QAction("UTAU プロジェクト (.ust) として書き出す...", window)
        act_export_ust.triggered.connect(
            lambda: window.export_as_ust() if hasattr(window, "export_as_ust") else None
        )
        file_menu.addAction(act_export_ust)

        logger.info("メニューパッチ: UST 読み込み/書き出し 追加")

    except Exception as exc:
        logger.warning("メニューパッチ失敗: %s", exc)
