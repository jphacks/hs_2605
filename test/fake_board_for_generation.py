import time
import math
import sys

def run_fake_board():
    # 🕒 本物の仕様：1秒間に16.64回データを出力 (1フレームあたり約60.1ミリ秒)
    fs = 16.64
    interval_sec = 1.0 / fs
    interval_ms = int(interval_sec * 1000) # 約60ms
    
    # 🕒 5分間（300秒）で自動停止するタイマー設定
    RECORD_DURATION_SEC = 300 
    
    # 🔢 本物の確定仕様フォーマット
    # C,15.0,37.5,24,10 -> first_cm=15.0, step_mm=37.5, bins=24, scale=10
    print("S,0,IBUKI v2 fake boot consecutive configuration")
    print("C,15.0,37.5,24,10")
    sys.stdout.flush()
    
    seq = 1000
    t_ms = 0
    start_time = time.time()
    
    print(f"本物仕様（16.64Hz/24Bin）で{RECORD_DURATION_SEC}秒間のダミーデータを生成します...", file=sys.stderr)
    
    while True:
        current_elapsed = time.time() - start_time
        
        # 指定時間（5分）が経ったら自動停止
        if current_elapsed >= RECORD_DURATION_SEC:
            print("設定時間に達したため、自動停止しました。", file=sys.stderr)
            break
            
        t_sec = t_ms / 1000.0
        
        # 60秒周期で「正常な呼吸（30秒）」と「息止め（30秒）」を交互に繰り返す学習データにする
        cycle_position = current_elapsed % 60.0
        if cycle_position < 30.0:
            # 【正常】大人の通常呼吸（15BPM = 4秒に1回呼吸）のサイン波
            bpm = 15.0
            frequency = bpm / 60.0
            wave = math.sin(2 * math.pi * frequency * t_sec)
        else:
            # 【息止め・異常】呼吸の波がフラット（直線）になる
            wave = 0.0
            
        # 24個のBin（距離）のI, Qデータを作成する
        # 仕様書より：Bin 0, 1 (25cm未満) はアンテナリーク、50cm付近（Bin 9〜10あたり）に人間
        bin_data = []
        for k in range(24):
            if k < 2:
                # Bins under 25cm: アンテナリーク（動かない大きな固定ノイズ）
                i_val, q_val = 500, -500
            elif k == 9 or k == 10:
                # 約50cm付近（15.0 + 9 * 3.75 = 48.75cm）: 【ここに人間がいる！】
                # 呼吸（wave）に合わせて、IとQが本物同様に円を描いて揺れる（scale=10なので10倍する）
                radius = 150
                i_val = int((900 + radius * math.cos(wave)) * 10)
                q_val = int((-120 + radius * math.sin(wave)) * 10)
            else:
                # その他の距離：何もない空間（ごくわずかな固定値）
                i_val, q_val = 30, -10
                
            bin_data.append(f"{i_val},{q_val}")
            
        # 24個のBinデータをカンマで連結
        bin_string = ",".join(bin_data)
        
        # 仕様書通りの D 行メッセージを作成 (D,seq,t_ms,I0,Q0,...,I23,Q23)
        line = f"D,{seq},{t_ms},{bin_string}"
        
        print(line)
        sys.stdout.flush() # データの詰まりを完全に防ぐ
        
        # 次のフレームに向けて時間を進める
        seq += 1
        t_ms += interval_ms
        time.sleep(interval_sec)

if __name__ == "__main__":
    run_fake_board()