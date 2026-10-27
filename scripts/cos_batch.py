# -*- coding: utf-8 -*-
"""
COS 修图批量脚本（Qwen-Image-2.1 · 2K 编辑 · 单阶段 · 每张卸载显存）
=====================================================================
逐张 2K 编辑，每张之间卸载模型+清显存碎片（防 offload 死锁），串行。

用法（用 ComfyUI 自带 python 跑）：
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe cos_batch.py edit

v2.2.0（2026-10-26）：新增「每张之间释放显存」——每张完成后调 ComfyUI
  `POST /free {"unload_models": true, "free_memory": true}`（服务端
  unload_all_models() + GC），下张重新加载。目的：防长时间运行显存碎片化
  导致的 offload 死锁（功耗掉到 50-65W / 0 步 / 日志 "N MB offloaded"）。
  代价：每张多花 ~1-2 分钟重新加载模型，但对比死锁一卡 19 分钟是净赚。

v2.1.0（2026-10-26）：超分阶段已按用户拍板移除——不做 8K，交付止步 2K。
旧 tiled 8K 工作流文档见 git 历史（references/upscale-8k.md）。

实机验证事实（改参数前先读，都是踩过的坑）：
  - UnetLoaderGGUFAdvanced 三参数：dequant_dtype=float32 + patch_dtype=float16
    防黑图（Q8 GGUF 反量化 NaN）；patch_on_device 是 BOOLEAN（True=补丁驻 GPU），
    历史成功出图均为 True。字符串"cpu"会被服务端 coerce 成 True——但别再传字符串。
  - TextEncodeQwenImage21 图连法（API 形状）：inputs 里 key 写
    "images.image_1": ["LoadImage节点号", 0]，vae 必须连（节点出 latent 直连 KSampler.latent_image）
  - cfg=1.0：负词无效，一切约束写进正向；euler/simple；编辑 40 步 denoise=1.0；resolution=2048
  - 8G 显存串行：逐张提交，绝不并发（别开多进程）
  - 断点续跑：已有成图（OUTDIR 里同名文件）自动跳过；脚本崩了重启重跑即可
  - 2026-10-25 踩坑：漏 import shutil，批量第 1 张后崩（已写死在文件头）

耗时基准（RTX 4060Ti 8G 实测）：2K/40 步 ≈ 19 分钟/张（满载；
后台占显存软件如剪映/抖音会拖慢 5 倍，渲染前能关则关）
"""
import json, time, shutil, os, sys, urllib.request

SERVER = "http://127.0.0.1:8188"
COMFY = "D:/AI/ComfyUI_windows_portable/ComfyUI/"
OUTDIR = r"C:\Users\user\Desktop\罗莱短剧\coser测试"      # ← 输出夹（默认=图所在夹）
JOBS_FILE = r"C:\Users\user\.hermes\workspace\cos_jobs.json"  # ← 任务清单
SEED = 20261025

# ───────────────────────── HTTP ─────────────────────────
def post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=60).read())

def upload(path, remote_name):
    b = "----HermesBoundary7d042"
    with open(path, "rb") as f:
        img = f.read()
    body = ("--" + b + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\""
            + remote_name + "\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + img + ("\r\n--" + b + "--\r\n").encode()
    req = urllib.request.Request(SERVER + "/upload/image", body,
                                 {"Content-Type": "multipart/form-data; boundary=" + b})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())["name"]

# ───────────────────────── 显存释放 ─────────────────────────
def free_vram():
    """卸载全部模型 + 清 CUDA 碎片。服务端：unload_all_models() + 触发 GC。
    实测依据：server.py /free 路由 + main.py set_flag 处理。"""
    try:
        post(SERVER + "/free", {"unload_models": True, "free_memory": True})
        print("  ♻ 已卸载模型+清显存碎片", flush=True)
    except Exception as e:
        print("  ⚠ 释放显存失败（不阻断）: " + repr(e)[:120], flush=True)

# ───────────────────────── 工作流 ─────────────────────────
def wf_edit(prompt, image_name, prefix, seed=SEED, resolution=2048, steps=40, denoise=1.0):
    """2K 图生图编辑（GGUF Q8 模型，三参数防黑图）"""
    return {
        "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {
            "unet_name": "qwen-image-2.1-UC-Q8_0.gguf",
            "dequant_dtype": "float32", "patch_dtype": "float16",
            "patch_on_device": True}},                    # BOOLEAN！不是字符串 "cpu"
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "5": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
            "resolution": resolution, "vae": ["3", 0], "images.image_1": ["4", 0]}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
            "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["5", 2]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }

# ───────────────────────── 队列 ─────────────────────────
def submit(wf, tag):
    pid = post(SERVER + "/prompt", {"prompt": wf})["prompt_id"]
    print("  [" + tag + "] queued " + pid, flush=True)
    return pid

def wait_one(pid, tag, timeout_min=120):
    """等单张完成。返回 (成功?, 状态串)。"""
    start = time.time()
    while True:
        if (time.time() - start) / 60 > timeout_min:
            return False, "timeout"
        time.sleep(15)
        try:
            h = get(SERVER + "/history/" + pid)
        except Exception:
            continue
        if pid in h:
            st = h[pid]["status"]
            if st["status_str"] == "success" and st.get("completed"):
                print("  ✓ [" + tag + "] 完成", flush=True)
                return True, "success"
            if st["status_str"] == "error":
                print("  ✗ [" + tag + "] 失败: " + str(st.get("messages", [])[-1])[:200], flush=True)
                return False, "error"

def out_path(pid):
    h = get(SERVER + "/history/" + pid)
    for _, o in h[pid]["outputs"].items():
        for im in o.get("images", []):
            if im.get("type") == "temp":
                continue
            sub = im.get("subfolder")
            return COMFY + (sub + "/" if sub else "output/") + im["filename"]
    return None

# ───────────────────────── 编辑（唯一阶段）─────────────────────────
def stage_edit():
    jobs = json.load(open(JOBS_FILE, encoding="utf-8"))
    print("=== 2K 编辑，共 " + str(len(jobs)) + " 张（每张之间释放显存）===", flush=True)
    ok = fail = skip = 0
    failed_list = []
    for i, j in enumerate(jobs):
        name = j["out_name"]
        target = os.path.join(OUTDIR, name + ".png")
        if os.path.exists(target):
            print("  - [" + name + "] 已存在，跳过", flush=True)
            skip += 1
            continue
        print("  [" + str(i + 1) + "/" + str(len(jobs)) + "] " + name, flush=True)
        remote = upload(j["input"], "cos_in_" + name + ".png")
        wf = wf_edit(j["prompt"], remote, name, seed=j.get("seed", SEED))
        pid = submit(wf, name)
        good, status = wait_one(pid, name)
        if good:
            src = out_path(pid)
            if src:
                shutil.copy(src, target)
                print("  落盘: " + target, flush=True)
                ok += 1
            else:
                fail += 1; failed_list.append(name + "(成图缺失)")
        else:
            fail += 1; failed_list.append(name + "(" + status + ")")
        # 每张之后卸载模型+清碎片（最后一张也卸，保持干净）
        free_vram()
    print("=== 编辑完成：成功 " + str(ok) + " / 失败 " + str(fail) + " / 跳过 " + str(skip) + " ===", flush=True)
    if failed_list:
        print("失败清单: " + "; ".join(failed_list), flush=True)
    print("交付：编辑版全在输出夹，不要的自己删（无超分阶段）", flush=True)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "edit":
        stage_edit()
    elif mode == "free":
        free_vram()
    elif mode == "upscale":
        print("超分已移除（2026-10-26 用户拍板）：交付止步 2K，无此阶段", flush=True)
    else:
        print("用法: cos_batch.py edit | free", flush=True)