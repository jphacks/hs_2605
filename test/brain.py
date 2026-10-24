from ast import Import
import sys

import json
import time
import math
import collections
import numpy as np
from scipy import signal


class Brain:
    def __init__(self):
        self.first_cm = 0
        self.step_mm = 0
        self.num_bins = 0
        self.scale = 1.0
        self.fs = 16.6 
        self.buffer_size = 200
        self.time_buffer = collections.deque(maxlen=self.buffer_size)
        self.iq_buffers = {} 
        self.last_breath_time = time.time()
        self.last_moving_time = time.time()  # 👈 追加：静止期間の追跡用
        self.state = "Sleeping, breathing (OK)"  # 👈 変更：初期状態の文字列
    

        # 👈 前回出力からの新サンプル追跡用バッファ
        self.new_disp_samples = []
        self.frame_count = 0 

        # 4階バタワースフィルター（呼吸帯域: 0.15Hz 〜 0.6Hz）
        nyq = 0.5 * self.fs
        low = 0.15 / nyq
        high = 0.45 / nyq
        self.b, self.a = signal.butter(4, [low, high], btype='band')

    def feed(self, line: str) -> list[dict]:
        """サーバーから毎行呼ばれる関数。戻り値として辞書のリストを返す"""
        line = line.strip()
        if not line: return []
        parts = line.split(',')
        msg_type = parts[0]
        
        outputs = []
        
        if msg_type == 'C':
            # C,15.0,37.5,24,10
            self.first_cm = float(parts[1])
            self.step_mm = float(parts[2])
            self.num_bins = int(parts[3])
            self.scale = float(parts[4])
            self.iq_buffers = {k: collections.deque(maxlen=self.buffer_size) for k in range(self.num_bins)}
            
        elif msg_type == 'D':
            if not hasattr(self, 'num_bins') or self.num_bins == 0: return []
            t_ms = int(parts[2])
            t_sec = t_ms / 1000.0
            
            # 24個のBinのI, Qをパース
            for k in range(self.num_bins):
                i_val = int(parts[3 + k*2]) / self.scale
                q_val = int(parts[4 + k*2]) / self.scale
                self.iq_buffers[k].append((i_val, q_val))
            
            self.time_buffer.append(t_sec)

            self.frame_count += 1
            
            # データが十分に溜まったら処理（4秒分 = 約66フレーム）
            if len(self.time_buffer) >= 80 and self.frame_count % 4 == 0:  # 4フレームごとに処理
                res = self.process_vital(t_sec, t_ms)
                if res:
                    outputs.append(res)
                    
        return outputs
   

    def process_vital(self, current_time, t_ms):
        max_variance = -1
        best_bin = -1
        displacements_dict = {}
        
        for k in range(self.num_bins):
            # 👈 ズレ修正：25cm未満（Bin 0 と Bin 1）はアンテナリークのため完全に無視
            if k < 2: continue 
                
            iq_data = np.array(self.iq_buffers[k])
            I = iq_data[:, 0]
            Q = iq_data[:, 1]
            
            phase = np.arctan2(Q, I)
            unwrapped = np.unwrap(phase)
            
            # 👈 ズレ修正：本物の物理数式 mm = unwrapped_phase * 5.0 / (4 * pi)
            disp_mm = unwrapped * 5.0 / (4 * np.pi)
            displacements_dict[k] = disp_mm
            
            var = np.var(disp_mm[-33:]) # 直近2秒
            if var > max_variance:
                max_variance = var
                best_bin = k
        
        # 不在判定の閾値（mm単位の分散に合わせた調整）
        if max_variance < 0.0001:
            self.state = "Not in bed, ALERT"  # 👈 変更：初期状態の文字列"
            return {"t": t_ms, "state": self.state, "bpm": None, "disp_mm": [], "motion": 0, "present": False, "chest_cm": 0}

        # 距離計算の修正
        chest_cm = self.first_cm + best_bin * (self.step_mm / 10.0)
        selected_disp = displacements_dict[best_bin]
        
        detrended = signal.detrend(selected_disp)
        filtered_disp = signal.lfilter(self.b, self.a, detrended)
        
        # 最新の1サンプルを新サンプルバッファに追加
        exported_samples = [round(float(x), 3) for x in filtered_disp[-4:]]

        self.new_disp_samples = [round(float(x), 3) for x in filtered_disp[-4:]]
        peaks, _ = signal.find_peaks(filtered_disp, distance=16, prominence=0.02)
        bpm = None
        motion_val = int(max_variance * 1000)
        
        
        # -------------------------------------------------------------
        # 👈 ここから下を完全に置き換え（仕様表の判定ロジック）
        # -------------------------------------------------------------
        MOTION_THRESHOLD = 50  # 体動のしきい値（環境に合わせて調整）
        event_dict = None
        
        if motion_val > MOTION_THRESHOLD:
            # 👉 動いている (Moving: yes / 仕様表2行目)
            elapsed_still = time.time() - self.last_moving_time
            if elapsed_still > 10.0:  # 長い静止期間の後に動き始めた場合
                self.state = "Awake / moving (OK), with 'woke up'"
            else:
                self.state = "Awake / moving (OK)"
            self.last_breath_time = time.time()
        else:
            # 👉 動いていない (Moving: no)
            self.last_moving_time = time.time()
            
            if len(peaks) >= 2:
                # 👉 呼吸している (Breathing: yes / 仕様表1行目)
                self.last_breath_time = time.time()
                peak_intervals = np.diff(peaks)
                bpm = round(60.0 / (np.mean(peak_intervals) / self.fs), 1)
                self.state = "Sleeping, breathing (OK)"
            else:
                # 👉 呼吸していない (Breathing: no / 仕様表3行目)
                elapsed_no_breath = time.time() - self.last_breath_time
                if elapsed_no_breath >= 10.0:  # デモ用の10秒閾値
                    self.state = "No breathing, ALERT"
                    event_dict = {"type": "alert", "reason": "no_breath", "state": self.state, "t": t_ms}
                else:
                    self.state = "Sleeping, breathing (OK)"  # アラート時間未満はOK状態を維持
        # -------------------------------------------------------------
        # 👈 ここまで
        # -------------------------------------------------------------
        
        # サーバーに通知するイベント（Not in bed 用のアラートイベントを追加）
        if "Not in bed" in self.state:
            event_dict = {"type": "alert", "reason": "not_in_bed", "state": self.state, "t": t_ms}

        
        # 👈 ズレ修正：前回の出力から増えた分だけをdisp_mmに入れてバッファを空にする
        exported_samples = list(self.new_disp_samples)
        self.new_disp_samples = []

        self.output_status(t_ms, bpm, exported_samples, motion_val, True, int(chest_cm))
        
        # サーバーが期待するステータス構造
        status = {
            "t": t_ms, # ボードの時間(ms)
            "state": self.state,
            "bpm": bpm,
            "disp_mm": exported_samples,
            "motion": motion_val,
            "present": True,
            "chest_cm": int(chest_cm)
        }
        
        # イベントがあればステータスと一緒に返す、無ければステータスだけ返す
        return event_dict if event_dict else status


    def output_status(self, current_time, bpm, disp_mm, motion, present, chest_cm):
        status = {"t": current_time, "state": self.state, "bpm": bpm, "disp_mm": disp_mm, "motion": motion, "present": present, "chest_cm": chest_cm}
        print(json.dumps(status))
        sys.stdout.flush()

if __name__ == "__main__":
    brain = Brain()
    # 👈 標準入力からデータを1行ずつリアルタイムに読み込む
    for line in sys.stdin:
        results = brain.feed(line)
        for res in results:
            print(json.dumps(res))
            sys.stdout.flush()