"""测试 overlay 弹跳表达式在 ffmpeg 中是否合法（完整错误输出）"""
import subprocess

FFMPEG = r'D:\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe'

base_y = "H-h-86"
start = 26.2
bounce = f"{base_y}+90*if(lt(t\\,{start}),0,exp(-1.6*(t-{start}))*sin((t-{start})*13))"
expr = f"x=(W-w)/2:y={bounce}:enable='gte(t,{start:.2f})'"
print("EXPR:", expr)

p = subprocess.run([
    FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=480x864:d=1",
    "-f", "lavfi", "-i", "color=c=red:s=432x130:d=1",
    "-filter_complex", f"[0:v][1:v]overlay={expr}",
    "-frames:v", "1", "-f", "null", "-",
], capture_output=True, text=True)
print("rc:", p.returncode)
# 打印 stderr 中真正的错误行（跳过 banner）
err_lines = (p.stderr or "").splitlines()
for ln in err_lines:
    if "Error" in ln or "error" in ln or "Invalid" in ln or "No option" in ln or "Failed" in ln:
        print("ERR>", ln.strip())
