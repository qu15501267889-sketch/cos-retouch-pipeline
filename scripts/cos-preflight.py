# -*- coding: utf-8 -*-
"""cos-preflight.py — COS 管线提示词组装自检（第 4.5 步的机器执行版）
用法: python cos-preflight.py <prompt.txt> [--skin <img.jpg>]
退出码: 0=全过 1=有漏项（漏项清单打在 stdout）
跨机可移植：只依赖 stdlib + PIL（--skin 时）；无 PIL 则跳过皮肤采样项。
"""
import sys, os, io, json, re

def load(p):
    return io.open(p, encoding="utf-8-sig").read()

# 八项自检（与 SKILL.md 第 4.5 步表一一对应）
def check(prompt, user_flags):
    fails = []
    has = lambda *kws: all(k in prompt for k in kws)

    if user_flags.get("retouch"):
        if not has("面部美颜", "身材塑形", "全身美白", "服装精修"):
            fails.append("1 精修四件不齐（面部美颜/身材塑形/全身美白/服装精修）")
    if user_flags.get("retouch") or "全身美白" in prompt:
        fixed = "白皙透亮肤如凝脂，通透自然，中性灰+高低频保留毛孔，不漂白不假面"
        if fixed not in prompt:
            fails.append("2 美白固定句未逐字引用")
        if any(k in prompt for k in ("金", "狐火", "宫灯", "烛光")) and not any(
                k in prompt for k in ("冷调象牙白", "莹白如玉", "去黄")):
            fails.append("2b 暖金光场景未叠冷调对抗词")
    m = re.search(r"(九|8|6|三)条?[尾翼]", prompt)
    if m and not has("根部衔接", "形态完整连贯", "不遮挡"):
        fails.append("3 数量部件五律不齐（数字/根部衔接/连贯/遮挡守卫）")
    if re.search(r"踩在|坐在|倚在", prompt) and not has("无悬空", "完全贴合", "下压"):
        fails.append("4 接触写实句缺失（完全贴合/无悬空/下压形变）")
    if user_flags.get("fx_min") and re.search(r"强特效层|魔法阵|光环", prompt):
        fails.append("5 特效应为最弱档=整段删除，仍有特效条目")
    if user_flags.get("replace_prop") and not ("清除" in prompt and "无残留" in prompt):
        fails.append("6 道具替换缺「原物清除无残留」句")
    if not has("不变成另一个人", "头身比"):
        fails.append("7 保真底线句缺失（五官保真/头身比）")
    return fails

def skin_check(img):
    """皮肤偏黄度粗判：裸露皮肤区采样 R-B 差。>25 = 偏黄（暖光拉黄信号）。"""
    try:
        from PIL import Image
        import statistics
        im = Image.open(img).convert("RGB").resize((200, 266))
        px = list(im.getdata())
        # 取中上部（脸/肩大致区域）亮度高饱和中等的像素当皮肤样本
        band = [p for p in px if p[0] > 150 and p[1] > 110 and p[2] < p[0] and abs(p[0]-p[1]) < 60]
        if len(band) < 50:
            return None, "样本不足，跳过"
        rb = statistics.mean(p[0] - p[2] for p in band)
        return rb, ("偏黄(需冷调对抗)" if rb > 25 else "可接受")
    except ImportError:
        return None, "无 PIL，跳过"

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    prompt = load(args[0])
    flags = {"retouch": False, "fx_min": False, "replace_prop": False}
    img = None
    for a in args[1:]:
        if a.startswith("--skin="): img = a.split("=", 1)[1]
        elif a == "--retouch": flags["retouch"] = True
        elif a == "--fx-min": flags["fx_min"] = True
        elif a == "--replace-prop": flags["replace_prop"] = True
    fails = check(prompt, flags)
    if img:
        rb, verdict = skin_check(img)
        if rb is not None:
            print(f"[skin] R-B={rb:.1f} -> {verdict}")
            if rb > 25 and "冷调象牙白" not in prompt and "去黄" not in prompt:
                fails.append("2c 采样显示皮肤偏黄，提示词未叠冷调对抗词")
        else:
            print(f"[skin] {verdict}")
    if fails:
        print("FAIL " + str(len(fails)) + " 项：")
        for f in fails: print("  ✗ " + f)
        sys.exit(1)
    print("PASS 八项自检全过")
