# modules/bridge/pipeline_bridge.py

import ctypes
from typing import List, Dict, Any, Optional

from modules.ffi.vose_api import CVoseFrame

# C++側と共有する唯一のVoseFrame定義。FFIバインド側と同じ型を使う。

class PipelineBridge:
    def __init__(self, c_engine_dll: Optional[ctypes.CDLL] = None):
        """
        VO-SE Python-C++ 高速合成パイプライン・ブリッジ
        """
        self.c_engine = c_engine_dll
        self._setup_c_interfaces()

    def _setup_c_interfaces(self) -> None:
        """C++側の関数シグネチャをctypesに厳密に登録（型安全性の担保）"""
        if self.c_engine is None:
            return

        # C++側の関数: void set_vocal_timeline(const VoseFrame* frames, int frame_count)
        # 配列の先頭ポインタと、配列の要素数を安全にトスするための設定です
        set_timeline = getattr(self.c_engine, "set_vocal_timeline", None)
        if set_timeline is not None:
            set_timeline.argtypes = [ctypes.POINTER(CVoseFrame), ctypes.c_int]
            set_timeline.restype = None

    def send_timeline_to_core(self, timeline_data: List[Dict[str, Any]]) -> bool:
        """
        Pythonのタイムライン辞書配列をCの連続メモリ領域にシリアライズし、
        C++コアエンジンへ一括転送する。
        """
        if not timeline_data:
            return False

        if self.c_engine is None:
            # オフラインモード・モック動作時のログ
            print(f"[PipelineBridge] Mock Send: {len(timeline_data)} frames serialized.")
            return True

        try:
            frame_count = len(timeline_data)
            
            # 2. C言語の連続した配列用の型を動的に生成
            # 例: VoseFrame * 1200 のようなメモリ空間を確保
            FrameArrayType = CVoseFrame * frame_count
            c_frames = FrameArrayType()

            # 3. 高速シリアライズ・ループ
            for idx, frame in enumerate(timeline_data):
                c_frames[idx].time = float(frame.get("time", 0.0))
                c_frames[idx].weight = float(frame.get("weight", 1.0))
                
                # 文字列をバイト列に変換し、固定長バッファに安全に書き込み
                p_str = str(frame.get("phoneme", "pau")).encode('utf-8')
                # 8バイト（末尾NULL用を引いて7バイト）を超えないようにスライス
                c_frames[idx].phoneme = p_str[:7]

            # 4. C++エンジンへのポインタ渡し呼び出し
            set_timeline = getattr(self.c_engine, "set_vocal_timeline", None)
            if set_timeline and callable(set_timeline):
                # 配列の先頭アドレスをポインタキャストしてC++へトス
                set_timeline(ctypes.cast(c_frames, ctypes.POINTER(CVoseFrame)), frame_count)
                return True

        except Exception as e:
            import sys
            print(f"[Error] Failed to serialize pipeline to C++: {e}", file=sys.stderr)
            return False

        return False
