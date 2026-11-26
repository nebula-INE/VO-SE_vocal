#GUI/voice_manager.py

import os
import platform
import sys
import logging
import json
import tempfile


MAX_VOICE_ZIP_ENTRIES = 200_000
MAX_VOICE_ZIP_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_VOICE_ZIP_FILE_BYTES = 1024 * 1024 * 1024


def get_resource_path(relative_path: str) -> str:
    """
    リソースファイルの絶対パスを取得する。
    PyInstallerの _MEIPASS 属性へのアクセスを getattr で安全に行い、
    Actions の reportAttributeAccessIssue を完全に回避します。
    """
    import sys
    import os

    # 1. getattr を使用して sys._MEIPASS を安全に取得
    # 第2引数に os.path.abspath(".") を指定することで、通常実行時もカバーします
    base_path: str = str(getattr(sys, '_MEIPASS', os.path.abspath(".")))

    # 2. 相対パスと結合してフルパスを生成
    full_path: str = os.path.join(base_path, relative_path)

    # 3. 代表の設計通り、パスを正規化して返却
    return os.path.normpath(full_path)

class VoiceManager:
    # Ownership metadata is kept outside voicebank folders so the installed UTAU
    # payload remains byte-for-byte/structure compatible with the source bank.
    MANAGED_VOICE_REGISTRY = ".vose-managed-voicebanks.json"
    MANAGED_VOICE_REGISTRY_VERSION = 1

    def __init__(self):
        self.system = platform.system()
        # 実行環境(PyInstaller)と開発環境のパス解決を統一
        if getattr(sys, 'frozen', False):
            # 代表、ここが修正の核心です。
            # 外部関数を呼ばず、直接 getattr で sys._MEIPASS を安全に取得します。
            # これにより F821 Undefined name エラーを 100% 回避します。
            self.base_path = str(getattr(sys, '_MEIPASS', os.path.abspath(".")))
        else:
            # modules/audio/ から見たプロジェクトルート (代表の設計通り)
            self.base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # ボイスディレクトリの確定
        self.voices_dir = os.path.join(self.base_path, "assets", "voices")
        self.internal_voice_dir = os.path.join(self.base_path, "voice_banks")
        self.voices = {}  # { "キャラ名": "絶対パス" }

    def _repair_garbled_names(self, cancel_event=None) -> None:
        """アプリの保存先(voice_banks 等)内の、文字化けしたフォルダ名・ファイル名を直す。"""
        from modules.utils.text_encoding import repair_mojibake_tree
        seen = set()
        for root in (self.internal_voice_dir, self.user_voice_dir):
            key = os.path.normpath(root)
            if key in seen:
                continue
            seen.add(key)
            try:
                for old, new in repair_mojibake_tree(
                    root,
                    cancel_event=cancel_event,
                    max_entries=200_000,
                ):
                    self._sync_managed_voice_registry_rename(old, new)
                    print(f"[VoiceManager] 文字化けを修復: {os.path.basename(old)} -> {os.path.basename(new)}")
            except OSError as e:
                print(f"[VoiceManager] 文字化けの修復をスキップ: {e}")

    def _sync_managed_voice_registry_rename(self, old_path, new_path):
        """Keep ownership records aligned when a managed bank's top-level name is repaired."""
        managed_root = os.path.normcase(os.path.realpath(os.path.abspath(self.user_voice_dir)))
        old_abs = os.path.abspath(old_path)
        new_abs = os.path.abspath(new_path)
        old_parent = os.path.normcase(os.path.realpath(os.path.dirname(old_abs)))
        new_parent = os.path.normcase(os.path.realpath(os.path.dirname(new_abs)))
        if old_parent != managed_root or new_parent != managed_root:
            return

        old_name = os.path.basename(old_abs)
        new_name = os.path.basename(new_abs)
        if not old_name or not new_name or old_name == new_name:
            return

        names = self._read_managed_voice_registry()
        if old_name not in names:
            return
        names.discard(old_name)
        names.add(new_name)
        self._write_managed_voice_registry(names)

    @property
    def user_voice_dir(self) -> str:
        """「音源を追加」で取り込んだ音源の保存先（書き込み可能な場所）。

        通常実行では <プロジェクト>/voice_banks。PyInstaller 等で固めた場合は
        展開先(_MEIPASS)が読み取り専用の一時フォルダになるため、ユーザーデータ領域を使う。
        """
        if not getattr(sys, 'frozen', False):
            return self.internal_voice_dir
        if self.system == "Windows":
            root = os.getenv("APPDATA") or os.path.expanduser("~")
        elif self.system == "Darwin":
            root = os.path.expanduser("~/Library/Application Support")
        else:
            root = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(root, "VO-SE", "voices")

    def scan_voices(self, cancel_event=None, progress_callback=None):
        """Scan voicebank folders incrementally with cancellation and traversal limits."""
        self._repair_garbled_names(cancel_event=cancel_event)

        search_paths = []
        if self.system == "Darwin":
            search_paths.append(os.path.expanduser("~/Library/Application Support/OpenUTAU/Content/Voices/"))
            search_paths.append(os.path.expanduser("~/Library/Application Support/OpenUTAU/Singers"))
            search_paths.append(os.path.expanduser("~/Library/Application Support/Vocaloid/Voices/"))
        elif self.system == "Windows":
            search_paths.append(r"C:\Program Files (x86)\UTAU\voice")
            appdata_roaming = os.getenv("APPDATA")
            if appdata_roaming:
                search_paths.append(os.path.join(appdata_roaming, "OpenUTAU", "Content", "Voices"))
                search_paths.append(os.path.join(appdata_roaming, "OpenUTAU", "Singers"))
        else:
            data_home = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            search_paths.append(os.path.join(data_home, "OpenUTAU", "Singers"))
            search_paths.append(os.path.join(data_home, "OpenUTAU", "Content", "Voices"))

        search_paths.append(self.internal_voice_dir)
        if os.path.normpath(self.user_voice_dir) != os.path.normpath(self.internal_voice_dir):
            search_paths.append(self.user_voice_dir)

        self.voices.clear()
        scanned_directories = 0
        discovered_banks = 0
        stop_scan = False
        max_directories = 200_000
        max_banks = 20_000

        for search_root in search_paths:
            if stop_scan or (cancel_event is not None and cancel_event.is_set()):
                break
            if not os.path.isdir(search_root) or os.path.islink(search_root):
                continue

            def _walk_error(error):
                logging.warning("音源フォルダを走査できません (%s): %s", search_root, error)

            for current, directories, files in os.walk(
                search_root,
                topdown=True,
                followlinks=False,
                onerror=_walk_error,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    stop_scan = True
                    break
                scanned_directories += 1
                if scanned_directories > max_directories:
                    logging.warning(
                        "音源一覧スキャンのディレクトリ上限 (%s) に到達しました。残りはスキップします。",
                        max_directories,
                    )
                    stop_scan = True
                    break

                directories[:] = sorted(
                    name for name in directories
                    if not os.path.islink(os.path.join(current, name))
                )
                if any(name.casefold() == "oto.ini" for name in files):
                    voice_name = os.path.basename(current)
                    self.voices[voice_name] = current
                    discovered_banks += 1
                    if discovered_banks >= max_banks:
                        logging.warning(
                            "音源一覧スキャンの登録上限 (%s) に到達しました。残りはスキップします。",
                            max_banks,
                        )
                        stop_scan = True
                        break

                if progress_callback is not None and scanned_directories % 128 == 0:
                    try:
                        progress_callback(scanned_directories)
                    except Exception:
                        logging.debug("音源スキャンの進捗通知をスキップ", exc_info=True)

        self.voices["VO-SE Official (Embedded)"] = "__INTERNAL__"
        logging.info(
            "VO-SE: %d 件の音源を検出しました (%d directories scanned, cancelled=%s).",
            len(self.voices),
            scanned_directories,
            bool(cancel_event is not None and cancel_event.is_set()),
        )
        return self.voices

    def is_internal(self, voice_name):
        """選択された音源が内蔵（公式）かどうかを判定"""
        return self.voices.get(voice_name) == "__INTERNAL__"

    
    def parse_oto_ini(self, voice_dir):
        """
        指定された音源フォルダの oto.ini を解析。
        戻り値: { "あ": {"filename": "a.wav", "pre_utterance": 10.0, ...} }
        """
        config = {}
        ini_path = os.path.join(voice_dir, "oto.ini")
        if not os.path.exists(ini_path):
            return config

        try:
            # UTAU音源は伝統的に Shift-JIS (cp932)
            with open(ini_path, 'r', encoding='cp932', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or '=' not in line:
                        continue
                    
                    # フォーマット: ファイル名.wav=エイリアス,左ブランク,固定範囲,右ブランク,先行発声,オーバーラップ
                    fname, params = line.split('=', 1)
                    p = params.split(',')
                    
                    alias = p[0] if p[0] else fname.replace(".wav", "")
                    
                    # 単位はミリ秒(ms)としてパース（エンジン側で秒に変換することを想定）
                    config[alias] = {
                        "filename": fname,
                        "left_blank": float(p[1]) if len(p) > 1 and p[1] else 0.0,
                        "fixed_range": float(p[2]) if len(p) > 2 and p[2] else 0.0,
                        "right_blank": float(p[3]) if len(p) > 3 and p[3] else 0.0,
                        "pre_utterance": float(p[4]) if len(p) > 4 and p[4] else 0.0,
                        "overlap": float(p[5]) if len(p) > 5 and p[5] else 0.0
                    }
        except Exception as e:
            logging.error(f"oto.ini の解析中にエラー: {e}")
            
        return config

    def install_voice_from_zip(self, zip_path):
        """ZIP音源を voice_banks に展開して追加する。"""
        import zipfile
        import tempfile
        from modules.utils.text_encoding import safe_extract_zip

        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP not found: {zip_path}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # ZIP bombs and malformed archives must fail before extraction starts.
                zip_infos = zf.infolist()
                if len(zip_infos) > MAX_VOICE_ZIP_ENTRIES:
                    raise ValueError(
                        f"ZIP内のファイル数が安全な上限 ({MAX_VOICE_ZIP_ENTRIES:,}) を超えています。"
                    )
                total_uncompressed = 0
                for info in zip_infos:
                    if info.is_dir():
                        continue
                    if info.file_size < 0 or info.file_size > MAX_VOICE_ZIP_FILE_BYTES:
                        raise ValueError(
                            f"ZIP内の単一ファイルが安全なサイズ上限を超えています: {info.filename}"
                        )
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_VOICE_ZIP_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            "ZIP展開後の合計サイズが安全な上限 "
                            f"({MAX_VOICE_ZIP_UNCOMPRESSED_BYTES:,} bytes) を超えています。"
                        )

                # Windows 等で作られた ZIP は日本語名を CP932 で保存し UTF-8 フラグを立てない。
                # そのまま展開するとフォルダ名・wav 名が化けて oto.ini と一致しなくなるため、
                # 名前を正しく読み直しながら安全に展開する。
                safe_extract_zip(zf, tmp_dir)

            candidates = self.find_oto_dirs(tmp_dir)
            if not candidates:
                raise ValueError('oto.ini が見つからないため、UTAU音源として認識できません。')

            src_dir = candidates[0]
            dest_dir = self._copy_voice_dir(src_dir)

        self.scan_voices()
        return os.path.basename(dest_dir)

    # ------------------------------------------------------------------
    # フォルダからの取り込み
    # ------------------------------------------------------------------
    @staticmethod
    def find_oto_dirs(root_dir, max_depth=4):
        """root_dir 以下（max_depth 階層まで）で oto.ini を含むフォルダを浅い順に返す。"""
        found = []
        root_dir = os.path.normpath(root_dir)
        base_depth = root_dir.count(os.sep)
        for cur, dirs, files in os.walk(root_dir):
            if cur.count(os.sep) - base_depth >= max_depth:
                dirs[:] = []
            if any(f.lower() == 'oto.ini' for f in files):
                found.append(cur)
        return sorted(found, key=lambda p: (p.count(os.sep), p))

    def install_voice_from_folder(self, folder_path):
        """UTAU音源フォルダを保存先(user_voice_dir)へコピーして追加する。

        選んだフォルダ自体、またはその下の浅い階層に oto.ini があればよい
        （「音源フォルダの1つ上」を選んでしまった場合にも対応）。
        すでに保存先の中にある音源を選んだ場合はコピーせず、そのまま使う。
        戻り値: 追加された音源の表示名（フォルダ名）
        """
        if not folder_path or not os.path.isdir(folder_path):
            raise FileNotFoundError(f"フォルダが見つかりません: {folder_path}")

        candidates = self.find_oto_dirs(folder_path)
        if not candidates:
            raise ValueError('oto.ini が見つからないため、UTAU音源として認識できません。')
        src_dir = candidates[0]

        root = os.path.realpath(self.user_voice_dir)
        real_src = os.path.realpath(src_dir)
        try:
            inside = os.path.commonpath([root, real_src]) == root
        except ValueError:      # 別ドライブなど
            inside = False
        if inside:
            self.scan_voices()
            return os.path.basename(real_src)

        managed_before = self._read_managed_voice_registry()
        dest_dir = self._copy_voice_dir(src_dir)
        self.scan_voices()

        installed_name = os.path.basename(dest_dir)
        managed_after = self._read_managed_voice_registry()
        if installed_name not in managed_after:
            added_names = managed_after - managed_before
            if len(added_names) == 1:
                installed_name = next(iter(added_names))
        return installed_name

    def _copy_voice_dir(self, src_dir):
        """src_dir を user_voice_dir 配下へ重複しない名前でコピーし、コピー先パスを返す。"""
        import shutil

        voice_name = os.path.basename(src_dir.rstrip(os.sep)) or 'ImportedVoice'
        safe_name = ''.join(ch for ch in voice_name if ch not in '<>:"/\\|?*').strip() or 'ImportedVoice'

        os.makedirs(self.user_voice_dir, exist_ok=True)
        dest_dir = os.path.join(self.user_voice_dir, safe_name)
        base = dest_dir
        idx = 1
        while os.path.exists(dest_dir):
            dest_dir = f"{base}_{idx}"
            idx += 1

        try:
            shutil.copytree(src_dir, dest_dir)
            # Register only folders that VO-SE itself copied. Keep this index
            # outside the voicebank so its original file list is not modified.
            self._register_managed_voice(dest_dir)
        except Exception:
            # 途中まで作られたコピーを残さない（次回スキャンで壊れた音源として出るため）
            shutil.rmtree(dest_dir, ignore_errors=True)
            try:
                self._unregister_managed_voice(dest_dir)
            except OSError:
                logging.exception("Failed to clean up Voicebank ownership registry")
            raise
        return dest_dir

    def _managed_voice_registry_path(self):
        return os.path.join(self.user_voice_dir, self.MANAGED_VOICE_REGISTRY)

    def _read_managed_voice_registry(self):
        """Read the app-owned list of copied bank directory names."""
        registry_path = self._managed_voice_registry_path()
        if os.path.islink(registry_path) or not os.path.isfile(registry_path):
            return set()
        try:
            with open(registry_path, "r", encoding="utf-8") as registry_file:
                data = json.load(registry_file)
        except (OSError, UnicodeError, ValueError):
            logging.warning("Voicebank ownership registry is unreadable: %s", registry_path)
            return set()

        if not isinstance(data, dict) or data.get("version") != self.MANAGED_VOICE_REGISTRY_VERSION:
            return set()
        names = data.get("managed_voicebanks", [])
        if not isinstance(names, list):
            return set()
        return {
            name for name in names
            if isinstance(name, str)
            and name not in ("", ".", "..")
            and "/" not in name
            and "\\" not in name
        }

    def _write_managed_voice_registry(self, names):
        """Atomically persist the set of managed voicebank directory names."""
        root = self.user_voice_dir
        os.makedirs(root, exist_ok=True)
        registry_path = self._managed_voice_registry_path()
        fd, temp_path = tempfile.mkstemp(
            prefix=".vose-managed-voicebanks-",
            suffix=".tmp",
            dir=root,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as registry_file:
                json.dump(
                    {
                        "version": self.MANAGED_VOICE_REGISTRY_VERSION,
                        "managed_voicebanks": sorted(names),
                    },
                    registry_file,
                    ensure_ascii=False,
                    indent=2,
                )
                registry_file.write("\n")
            os.replace(temp_path, registry_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                logging.warning("Could not remove temporary Voicebank registry file: %s", temp_path)

    def _managed_voice_directory_name(self, voice_path):
        if not isinstance(voice_path, str):
            return None
        raw_path = os.path.expanduser(voice_path.strip())
        if not raw_path or raw_path.startswith("__INTERNAL__") or os.path.islink(raw_path):
            return None
        root = os.path.realpath(self.user_voice_dir)
        target = os.path.realpath(os.path.abspath(raw_path))
        if not root or target == root:
            return None
        if os.path.dirname(target) != root:
            return None
        try:
            if os.path.commonpath([root, target]) != root:
                return None
        except ValueError:
            return None
        if not os.path.isdir(target) or os.path.islink(target):
            return None
        if not os.path.isfile(os.path.join(target, "oto.ini")):
            return None
        return os.path.basename(target)

    def _register_managed_voice(self, voice_path):
        name = self._managed_voice_directory_name(voice_path)
        if name is None:
            raise ValueError("VO-SE管理領域の直下にある音源フォルダを登録できません。")
        names = self._read_managed_voice_registry()
        names.add(name)
        self._write_managed_voice_registry(names)

    def _unregister_managed_voice(self, voice_path):
        name = self._managed_voice_directory_name(voice_path)
        if name is None:
            # The folder may already have been removed; derive its direct-child name
            # only after verifying that its lexical parent is the managed root.
            if not isinstance(voice_path, str):
                return
            raw_path = os.path.realpath(os.path.abspath(os.path.expanduser(voice_path.strip())))
            root = os.path.realpath(self.user_voice_dir)
            if os.path.dirname(raw_path) != root or os.path.islink(raw_path):
                return
            name = os.path.basename(raw_path)
        names = self._read_managed_voice_registry()
        if name in names:
            names.remove(name)
            self._write_managed_voice_registry(names)

    def is_local_voice(self, voice_path):
        """Return whether a voicebank is a direct, real folder in VO-SE's writable store.

        This intentionally includes voicebanks imported by older releases before
        the ownership registry existed. External/OpenUTAU folders, embedded assets,
        nested paths, and symlinks are never considered local.
        """
        try:
            return self._managed_voice_directory_name(voice_path) is not None
        except (OSError, TypeError, ValueError):
            return False

    def is_managed_voice(self, voice_path):
        """Return whether this is a copied bank registered by the current VO-SE release.

        The registry is outside voicebank folders. Use is_local_voice() when the
        caller needs to manage legacy folders already stored in user_voice_dir.
        """
        try:
            name = self._managed_voice_directory_name(voice_path)
            if name is None or name not in self._read_managed_voice_registry():
                return False
            target = os.path.realpath(os.path.abspath(os.path.expanduser(voice_path.strip())))
            return (
                os.path.isdir(target)
                and not os.path.islink(target)
                and os.path.isfile(os.path.join(target, "oto.ini"))
            )
        except (OSError, TypeError, ValueError):
            return False

    def delete_user_voice(self, voice_path):
        """Delete one voicebank folder stored directly in VO-SE's writable voice directory.

        This supports both registry-managed imports and local voicebanks installed by
        older releases. Only the selected directory inside user_voice_dir is removed;
        folders elsewhere (including OpenUTAU/system voice directories) are untouched.
        """
        self.scan_voices()
        if not isinstance(voice_path, str) or not self.is_local_voice(voice_path):
            raise ValueError(
                "削除できるのはVO-SEの音源保存先直下にあるUTAU音源だけです。"
            )

        target = os.path.realpath(os.path.abspath(os.path.expanduser(voice_path)))
        registered_paths = set()
        for registered in self.voices.values():
            if isinstance(registered, (str, os.PathLike)):
                value = str(registered)
                if value and not value.startswith("__INTERNAL__"):
                    registered_paths.add(os.path.realpath(os.path.abspath(os.path.expanduser(value))))
        if target not in registered_paths:
            raise ValueError("音源一覧に登録されていない音源は削除できません。")

        import shutil

        # Re-check immediately before removal so a stale selection or replaced
        # symlink cannot redirect this operation outside the writable voice store.
        if not self.is_local_voice(target):
            raise ValueError("削除対象の音源パスが安全でないため、中止しました。")
        shutil.rmtree(target)
        self._unregister_managed_voice(target)
        self.scan_voices()
        return os.path.basename(target)

    def get_voice_path(self, name):
        return self.voices.get(name)
