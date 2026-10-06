"""Owned 60-minute native fixture; not a diarization accuracy benchmark."""
import os,subprocess,wave
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/qa/private/fixture'
FFMPEG=Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT/app/ffmpeg/ffmpeg.exe'
OUT.mkdir(parents=True,exist_ok=True)
for name,color in [('A','red'),('B','green'),('C','blue')]:
    target=OUT/f'QA60-{name}.mp4'
    if target.exists():continue
    subprocess.run([str(FFMPEG),'-v','error','-nostdin','-f','lavfi','-i',f'color=c={color}:s=320x180:r=30:d=3600','-an','-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p',str(target)],check=True)
target=OUT/'QA60.wav'
if not target.exists():
    with wave.open(str(target),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000)
        for second in range(3600):
            t=(np.arange(48000)/48000+second);signal=np.sin(2*np.pi*(220*t+0.05*t*t))*.12
            f.writeframes((signal*32767).astype('<i2').tobytes())
print('Owned 60min fixture created')
