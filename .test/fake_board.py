import time
import math 
import sys
import json
import collections
import numpy as np
from scipy import signal  # type: ignore[import-not-found]

    
def run_fake_board():
    # 仕様書通りの起動メッセージ
    print("S,0,IBUKI v2 fake boot")
    print("C,30,75,3,1000") # 30cmから75mm幅で3エリア測る設定
    sys.stdout.flush() # 詰まり防止のおまじない
    
    seq = 1532
    t_ms = 76600
    interval_ms = 50  # 毎秒20回 = 50ミリ秒ごと
    
    # 30秒間は正常な呼吸（OK）、その後15秒間は息止め（ALERT）にするシミュレーション
    start_time = time.time()

    RECORD_DURATION_SEC = 300 
    
    while True:
        current_elapsed = time.time() - start_time
        t_sec = t_ms / 1000.0

        if current_elapsed >= RECORD_DURATION_SEC:
            print("設定時間に達したため、自動停止しました。", file=sys.stderr)
            break
        
        if current_elapsed < 30.0:
            # 【OK状態】15BPMのきれいな呼吸のサイン波
            bpm = 15.0
            frequency = bpm / 60.0
            wave = math.sin(2 * math.pi * frequency * t_sec)
        else:
            # 【息止め状態】波がフラット（直線）になる
            wave = 0.0
            
        i0, q0 = 800, -340
        
        # 距離2（37.5cm）に人間がいる想定
        radius = 100
        # 呼吸の波に合わせてIとQが円を描いて動く（GitHubの標準シミュレーション）
        i1 = int(900 + radius * math.cos(wave))
        q1 = int(-120 + radius * math.sin(wave))
        
        i2, q2 = 70, 15
        
        # 仕様書通りのカンマ区切り（空白なし、行末\n）
        line = f"D,{seq},{t_ms},{i0},{q0},{i1},{q1},{i2},{q2}"
        
        print(line)
        sys.stdout.flush() # 👈 最重要：毎フレーム強制的にデータを送り出す
        
        seq += 1
        t_ms += interval_ms
        time.sleep(interval_ms / 1000.0)

if __name__ == "__main__":
    run_fake_board()