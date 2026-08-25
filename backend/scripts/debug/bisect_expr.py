"""二分定位 animate overlay 表达式哪个部分非法"""

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
    err = next(
        (
            ln.strip()
            for ln in (p.stderr or "").splitlines()
            if "No option" in ln or "Error parsing" in ln or "Invalid" in ln
        ),
        "",
    )
    print(f"[{label}] rc={p.returncode} {'OK' if p.returncode == 0 else 'FAIL ' + err}")


S = "x=(W-w)/2:y=H-h-86"
check(f"{S}+90*sin((t-26.2)*13):enable=gte(t\\,26.2)", "sin only")
check(f"{S}+90*exp(-1.6*(t-26.2)):enable=gte(t\\,26.2)", "exp only")
check(f"{S}+90*if(lt(t\\,26.2),0,50):enable=gte(t\\,26.2)", "if only")
check(f"{S}+90*if(lt(t\\,26.2),0,sin((t-26.2)*13)):enable=gte(t\\,26.2)", "if sin")
check(f"{S}+90*if(lt(t\\,26.2),0,exp(-1.6*(t-26.2))*sin((t-26.2)*13)):enable=gte(t\\,26.2)", "full")
