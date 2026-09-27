# modules/gui/mixins/voice_management_mixin.py
"""
VO-SE Vocal — 音源管理ミックスイン

MainWindow から以下の音源関連メソッドを分離:
  - 音源フォルダスキャン (UTAU / 公式)
  - oto.ini 解析 / キャッシュ
  - ボイスカード一覧更新
  - キャラクター切り替え時のエンジン反映
"""
from __future__ import annotations

import os
import pickle
from typing import Any, Dict

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QMessageBox

# このミックスインは MainWindow と組み合わせて使うことを前提とする
# self は MainWindow インスタンス


class VoiceManagementMixin:
    """
    音源管理機能を提供するミックスイン。
    MainWindow に mix-in して使用する。
    """

    # ------------------------------------------------------------------
    # 音源スキャン
    # ------------------------------------------------------------------

    def scan_utau_voices(self: Any) -> Dict[str, Dict[str, str]]:
        """音源フォルダをスキャンし統合管理（UTAU + 公式）"""
        voice_roots = [
            os.path.join(os.getcwd(), "voices"),
            os.path.join(os.getcwd(), "voice_banks"),
        ]

        for root in voice_roots:
            os.makedirs(root, exist_ok=True)

        found_voices: Dict[str, Dict[str, str]] = {}

        # 1. ユーザー追加音源のスキャン
        for voice_root in voice_roots:
            for dir_name in os.listdir(voice_root):
                dir_path = os.path.join(voice_root, dir_name)
                if not os.path.isdir(dir_path):
                    continue

                oto_path = os.path.join(dir_path, "oto.ini")
                if not os.path.exists(oto_path):
                    continue

                char_name = dir_name
                char_txt = os.path.join(dir_path, "character.txt")

                if os.path.exists(char_txt):
                    content = self.read_file_safely(char_txt)  # type: ignore[attr-defined]
                    if content:
                        for line in content.splitlines():
                            if line.startswith("name="):
                                char_name = line.split("=", 1)[1].strip()
                                break

                if char_name in found_voices:
                    char_name = f"{char_name} ({os.path.basename(voice_root)})"

                found_voices[char_name] = {
                    "path": dir_path,
                    "icon": (
                        os.path.join(dir_path, "icon.png")
                        if os.path.exists(os.path.join(dir_path, "icon.png"))
                        else "resources/default_avatar.png"
                    ),
                    "id": f"{os.path.basename(voice_root)}:{dir_name}",
                }

        # 2. 公式音源のスキャン
        base_path = getattr(self, "base_path", os.getcwd())
        official_base = os.path.join(base_path, "assets", "official_voices")

        if os.path.exists(official_base):
            for char_dir in os.listdir(official_base):
                full_dir = os.path.join(official_base, char_dir)
                if not os.path.isdir(full_dir):
                    continue

                display_name = f"[Official] {char_dir}"
                found_voices[display_name] = {
                    "path": full_dir,
                    "icon": "resources/official_icon.png",
                    "id": f"__INTERNAL__:{char_dir}",
                }

        voice_manager = getattr(self, "voice_manager", None)
        if voice_manager and hasattr(voice_manager, "voices"):
            voice_manager.voices = found_voices

        return found_voices

    # ------------------------------------------------------------------
    # oto.ini 解析
    # ------------------------------------------------------------------

    def parse_oto_ini(self: Any, voice_path: str) -> dict:
        """
        oto.iniを解析して辞書に格納する
        戻り値:
        {
            "あ": {
                "wav_path": ".../a.wav",
                "offset": 50.0,
                "consonant": 100.0,
                "blank": 0.0,
                "preutterance": 120.0,
                "overlap": 30.0
            },
            ...
        }
        """
        oto_map: dict = {}

        oto_path = os.path.join(voice_path, "oto.ini")
        if not os.path.exists(oto_path):
            return oto_map

        content = self.read_file_safely(oto_path)  # type: ignore[attr-defined]
        if not content:
            return oto_map

        for line in content.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue

            try:
                wav_file, params = line.split("=", 1)
                wav_file = wav_file.strip()

                parts = params.split(",")

                alias = parts[0].strip() if parts and parts[0].strip() else os.path.splitext(wav_file)[0]

                oto_map[alias] = {
                    "wav_path": os.path.join(voice_path, wav_file),
                    "offset": self.safe_to_float(parts[1]) if len(parts) > 1 else 0.0,
                    "consonant": self.safe_to_float(parts[2]) if len(parts) > 2 else 0.0,
                    "blank": self.safe_to_float(parts[3]) if len(parts) > 3 else 0.0,
                    "preutterance": self.safe_to_float(parts[4]) if len(parts) > 4 else 0.0,
                    "overlap": self.safe_to_float(parts[5]) if len(parts) > 5 else 0.0,
                }

            except Exception as e:
                # oto.ini は壊れている行が普通にあるので黙殺が正解
                print(f"DEBUG: oto.ini parse skipped line: {line} ({e})")
                continue

        return oto_map

    def safe_to_float(self: Any, val: Any) -> float:
        """文字列や数値を安全に浮動小数点数に変換。変換不能なら 0.0 を返す。"""
        if val is None:
            return 0.0
        try:
            if isinstance(val, (int, float)):
                return float(val)
            s_val = str(val).strip()
            if not s_val:
                return 0.0
            return float(s_val)
        except (ValueError, TypeError, AttributeError):
            return 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # UI 更新
    # ------------------------------------------------------------------

    def refresh_voice_ui_with_scan(self: Any) -> None:
        """スキャンを実行してUIを最新状態にする"""
        status_bar = self.statusBar()
        if status_bar:
            status_bar.showMessage("音源フォルダをスキャン中...")

        self.scan_utau_voices()
        self.update_voice_list()

        voice_manager = getattr(self, "voice_manager", None)
        count = len(voice_manager.voices) if voice_manager else 0

        if status_bar:
            status_bar.showMessage(f"スキャン完了: {count} 個の音源", 3000)

    def update_voice_list(self: Any) -> None:
        """VoiceManagerと同期してUI（カード一覧）を再構築"""
        if self.voice_cards is None:
            self.voice_cards = []
        else:
            self.voice_cards.clear()

        if self.voice_grid is None:
            return

        for i in reversed(range(self.voice_grid.count())):
            item = self.voice_grid.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        voice_manager = getattr(self, "voice_manager", None)
        voices_dict = voice_manager.voices if voice_manager else {}

        for index, (name, data) in enumerate(voices_dict.items()):
            path = data.get("path", "")
            icon_path = data.get("icon", os.path.join(path, "icon.png"))

            color = "#FFFFFF"
            if voice_manager and hasattr(voice_manager, "get_character_color"):
                color = voice_manager.get_character_color(path)

            try:
                from modules.gui.widgets import VoiceCardWidget
                card = VoiceCardWidget(name, icon_path, color)
                card.clicked.connect(self.on_voice_selected)  # type: ignore[attr-defined]
                self.voice_grid.addWidget(card, index // 3, index % 3)
                self.voice_cards.append(card)
            except ImportError:
                pass

        if self.character_selector is not None:
            self.character_selector.clear()
            self.character_selector.addItems(list(voices_dict.keys()))

    @Slot(str)
    def on_voice_selected(self: Any, character_name: str) -> None:
        """
        ボイスカード選択時の処理。
        音源データのロード、エンジンの更新、トークマネージャーの設定を同期。
        """
        # 1. UIの表示更新（選択状態のハイライト切り替え）
        if self.voice_cards:
            for card in self.voice_cards:
                if card is not None and hasattr(card, "set_selected"):
                    card.set_selected(getattr(card, "name", "") == character_name)

        # 2. 音源データの取得準備
        voice_manager = getattr(self, "voice_manager", None)
        if voice_manager is None:
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage("エラー: voice_manager が初期化されていません")
            return

        voices_dict = voice_manager.voices
        if character_name not in voices_dict:
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"エラー: {character_name} のデータが見つかりません")
            return

        voice_data = voices_dict[character_name]
        path = voice_data.get("path", "")
        if not path:
            return

        try:
            # 3. 原音設定(oto.ini)の解析と保持
            oto_data = self.parse_oto_ini(path)
            self.current_oto_data = oto_data if isinstance(oto_data, dict) else {}

            # 4. エンジン(vo_se_engine)への音源反映
            if self.vo_se_engine is not None:
                self.vo_se_engine.set_voice_library(path)
                self.vo_se_engine.set_oto_data(self.current_oto_data)

            self.current_voice = character_name

            # 5. トーク用音源(htsvoice)のチェックと設定
            talk_model = os.path.join(path, "talk.htsvoice")
            if os.path.exists(talk_model) and self.talk_manager is not None:
                self.talk_manager.set_voice(talk_model)

            # 6. キャラクターカラーの取得と完了通知
            char_color = "#FFFFFF"
            if voice_manager and hasattr(voice_manager, "get_character_color"):
                char_color = voice_manager.get_character_color(path)

            msg = f"【{character_name}】に切り替え完了 ({len(self.current_oto_data)} 音素ロード)"

            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(msg, 5000)

            print(f"Selected voice: {character_name} at {path} (Color: {char_color})")

        except Exception as e:
            print(f"Error loading voice: {e}")
            QMessageBox.critical(
                self,
                "音源ロードエラー",
                f"音源の読み込み中にエラーが発生しました:\n{str(e)}",
            )

    def refresh_voice_list(self: Any) -> None:
        """voice_banksフォルダを再スキャン（ギャラリー更新）"""
        if hasattr(self, "scan_utau_voices"):
            self.scan_utau_voices()
        elif hasattr(self, "voice_manager") and hasattr(self.voice_manager, "scan_utau_voices"):
            self.voice_manager.scan_utau_voices()  # type: ignore[attr-defined]

        gallery = getattr(self, "voice_gallery", None)
        if gallery is not None and hasattr(gallery, "setup_gallery"):
            gallery.setup_gallery()

        print("ボイスリストを更新しました")

    def play_selected_voice(self: Any, note_text: str) -> None:
        """選択されたボイスでプレビュー再生"""
        if not hasattr(self, "character_selector") or self.character_selector is None:
            return

        selected_name = self.character_selector.currentText()
        voices_path_map = getattr(self, "voices", {})
        if voices_path_map is None:
            voices_path_map = {}

        voice_path = voices_path_map.get(selected_name, "")

        if voice_path and voice_path.startswith("__INTERNAL__"):
            char_id = voice_path.split(":")[1]
            internal_key = f"{char_id}_{note_text}"

            engine = getattr(self, "vose_engine", getattr(self, "vo_se_engine", None))
            if engine and hasattr(engine, "play_voice"):
                engine.play_voice(internal_key)

    # ------------------------------------------------------------------
    # キャッシュ管理
    # ------------------------------------------------------------------

    def get_cached_oto(self: Any, voice_path: str) -> dict:
        """原音設定のキャッシュ管理。pickleによる高速化"""
        cache_path = os.path.join(voice_path, "oto_cache.vose")
        ini_path = os.path.join(voice_path, "oto.ini")

        if os.path.exists(cache_path) and os.path.exists(ini_path):
            try:
                if os.path.getmtime(cache_path) > os.path.getmtime(ini_path):
                    with open(cache_path, "rb") as f:
                        data = pickle.load(f)
                        if data:
                            return data
            except (pickle.UnpicklingError, EOFError, AttributeError, ImportError):
                pass

        oto_data = self.parse_oto_ini(voice_path)
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(oto_data, f)
        except Exception as e:
            print(f"DEBUG: Cache save failed: {e}")

        return oto_data

    def smart_cache_purge(self: Any) -> None:
        """メモリ最適化。未使用キャッシュの強制解放"""
        vm = getattr(self, "voice_manager", None)
        if vm and hasattr(vm, "clear_unused_cache"):
            vm.clear_unused_cache()  # type: ignore[attr-defined]
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage("Memory Optimized.", 2000)
        else:
            import gc
            gc.collect()
            print("DEBUG: Direct memory optimization executed.")
