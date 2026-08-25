"""验证最终 filter 表达式在 ffmpeg 中合法（动画 + 静态）"""

import subprocess

FFMPEG = r"D:\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"


def check(expr, label):
    p = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=480x864:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=432x130:d=1",
            "-filter_complex",
            f"[0:v][1:v]overlay={expr}",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    ok = p.returncode == 0
    err = ""
    for ln in (p.stderr or "").splitlines():
        if "No option" in ln or "Error parsing" in ln or "Invalid" in ln:
            err = ln.strip()
    print(f"[{label}] rc={p.returncode} {'OK' if ok else 'FAIL ' + err}")


# 动画：弹跳入场 + enable（_build_overlay_xy 动画返回，含 enable）
chk_anim = "x=(W-w)/2:y=H-h-86+90*if(lt(t\\,26.2),0,exp(-1.6*(t-26.2))*sin((t-26.2)*13)):enable=gte(t\\,26.20)"  # noqa: E501
# 静态：x y + enable
chk_static = "x=(W-w)/2:y=H-h-86:enable=gte(t\\,26.20)"

check(chk_anim, "动画")
check(chk_static, "静态")
