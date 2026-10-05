"""Create owned deterministic 24-second media, without modifying user projects."""
import subprocess
import wave
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'qa' / 'private' / 'fixture'
FFMPEG = Path(r'C:\Program Files (x86)\Common Files\AutoPod\ffmpeg\bin\ffmpeg.exe')

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, color in [('CA', 'red'), ('CB', 'green'), ('CC', 'blue')]:
        subprocess.run([str(FFMPEG), '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=640x360:r=30:d=24', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(OUT / f'{name}.mp4')], check=True)
    sample_rate = 48000
    t = np.arange(24 * sample_rate) / sample_rate
    signal = np.sin(2 * np.pi * (180 * t + 20 * t * t)) * .1
    with wave.open(str(OUT / 'audio.wav'), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sample_rate); f.writeframes((signal * 32767).astype('<i2').tobytes())
    print(OUT)

if __name__ == '__main__':
    main()
