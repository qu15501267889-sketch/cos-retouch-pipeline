# -*- coding: utf-8 -*-
"""
COS 修图两阶段批量脚本（Qwen-Image-2.1）
==========================================
阶段一 edit    ：全夹图 → 逐张 2K 编辑 → 全部入队 → 串行跑完 → 落盘
阶段二 upscale ：幸存编辑版 → tiled 8K 超分 → 全部入队 → 串行跑完 → 落盘

用法（用 ComfyUI 自带 python 跑）：
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe cos_batch.py edit
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe cos_batch.py upscale

设计要点：
  · 全部入队 = 连续 POST /prompt，ComfyUI 队列自己串行跑，中间零间隔；
    脚本崩了队列照跑，重启后对账补缺（已有成图的跳过）
  · 幸存者判定 = 输出夹里仍存在的 *_cos后期_*.png（用户删掉的自动出局）
  · 单张失败跳过记清单，不中断整批
"""
import json, os, sys, time, shutil, urllib.request

SERVER = "http://127.0.0.1:8188"
COMFY = "D:/AI/ComfyUI_windows_portable/ComfyUI/"
OUTDIR = r"C:\Users\user\Desktop\罗莱短剧\coser测试"      # ← 输出夹（默认=图所在夹）
JOBS_FILE = r"C:\Users\user\.hermes\workspace\cos_jobs.json"  # ← 阶段一任务清单
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

# ───────────────────────── 工作流 ─────────────────────────
def wf_edit(prompt, image_name, prefix, seed=SEED, resolution=2048, steps=40, denoise=1.0):
    """阶段一：2K 图生图编辑（GGUF Q8 模型，三参数防黑图）"""
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

RESTORE_PROMPT = ("Restore the photo: remove blur, noise and compression artifacts and "
                  "recover sharp, natural detail, keeping the same composition, people and colors.")

def wf_upscale_8k(image_name, prefix, seed=SEED, megapixels=45.0):
    """阶段二：tiled 8K 超分（Ray 版工作流 API 化）"""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "qwen\\rayQwen21_turboV13Int8convrot.safetensors",
            "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
            "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "AUSBOSS_NODES_LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["2", 0],
            "loras": json.dumps([
                {"name": "qwen-image-2.1-restore.safetensors", "strength": 1.0,
                 "enabled": True, "triggers": ""},
                {"name": "Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors",
                 "strength": 1.0, "enabled": True, "triggers": ""}], ensure_ascii=False),
            "trigger_separator": ", ", "on_missing": "error"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "6": {"class_type": "AUSBOSS_NODES_TiledUpscale", "inputs": {
            "image": ["5", 0], "megapixels": megapixels, "tile_megapixels": 2.0,
            "overlap": 128, "multiple": 32, "max_growth": 8}},
        "7": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["4", 1], "prompt": RESTORE_PROMPT, "negative_prompt": "",
            "resolution": 0,                     # 0 = 每块保持自身尺寸，切勿设 2048
            "vae": ["3", 0], "images.image_1": ["6", 2]}},   # 6.2 = before
        "8": {"class_type": "QwenImage21Cache", "inputs": {
            "model": ["4", 0], "device": "auto", "dtype": "default"}},
        "9": {"class_type": "KSampler", "inputs": {
            "model": ["8", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["7", 2]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "AUSBOSS_NODES_TiledUpscaleStitch", "inputs": {
            "tiles": ["10", 0], "stitcher": ["6", 1], "keep_colors": 1.0}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
    }

# ───────────────────────── 队列 ─────────────────────────
def submit(wf, tag):
    pid = post(SERVER + "/prompt", {"prompt": wf})["prompt_id"]
    print("  [" + tag + "] queued " + pid, flush=True)
    return pid

def wait_all(pids, tags, timeout_min=180):
    """全部入队后统一等：ComfyUI 队列自己串行跑，这里只收结果"""
    start = time.time()
    done, failed = {}, []
    while len(done) + len(failed) < len(pids):
        if (time.time() - start) / 60 > timeout_min:
            print("  !! 超时，剩余按失败计", flush=True)
            break
        time.sleep(15)
        for pid, tag in zip(pids, tags):
            if pid in done or pid in failed:
                continue
            try:
                h = get(SERVER + "/history/" + pid)
            except Exception:
                continue
            if pid in h:
                st = h[pid]["status"]
                if st["status_str"] == "success" and st.get("completed"):
                    done[pid] = tag
                    print("  ✓ [" + tag + "] 完成", flush=True)
                elif st["status_str"] == "error":
                    failed.append(pid)
                    print("  ✗ [" + tag + "] 失败: " + str(st.get("messages", [])[-1])[:200], flush=True)
    return done, failed

def out_path(pid):
    h = get(SERVER + "/history/" + pid)
    for _, o in h[pid]["outputs"].items():
        for im in o.get("images", []):
            if im.get("type") == "temp":
                continue
            sub = im.get("subfolder")
            return COMFY + (sub + "/" if sub else "output/") + im["filename"]
    return None

# ───────────────────────── 阶段一 ─────────────────────────
def stage_edit():
    jobs = json.load(open(JOBS_FILE, encoding="utf-8"))
    print("=== 阶段一：2K 编辑，共 " + str(len(jobs)) + " 张 ===", flush=True)
    pids, tags, meta = [], [], []
    for j in jobs:
        name = j["out_name"]
        target = os.path.join(OUTDIR, name + ".png")
        if os.path.exists(target):
            print("  - [" + name + "] 已存在，跳过", flush=True)
            continue
        remote = upload(j["input"], "cos_in_" + name + ".png")
        wf = wf_edit(j["prompt"], remote, name, seed=j.get("seed", SEED))
        pids.append(submit(wf, name)); tags.append(name); meta.append((name, target))
    if not pids:
        print("没有待处理任务", flush=True); return
    done, failed = wait_all(pids, tags)
    ok = 0
    for pid in done:
        name = done[pid]
        target = dict(meta)[name]
        src = out_path(pid)
        if src:
            shutil.copy(src, target); ok += 1
            print("  落盘: " + target, flush=True)
    print("=== 阶段一完成：成功 " + str(ok) + " / 失败 " + str(len(failed)) + " ===", flush=True)
    print("下一步：请用户在输出夹挑选，删掉不要的，然后跑 upscale", flush=True)

# ───────────────────────── 阶段二 ─────────────────────────
def stage_upscale():
    files = [f for f in os.listdir(OUTDIR)
             if f.endswith(".png") and "_cos后期_" in f and "_8K" not in f]
    print("=== 阶段二：8K 超分，幸存 " + str(len(files)) + " 张 ===", flush=True)
    if not files:
        print("没有幸存文件", flush=True); return
    pids, tags, meta = [], [], []
    for f in files:
        base = f[:-4]
        target = os.path.join(OUTDIR, base + "_8K.png")
        if os.path.exists(target):
            print("  - [" + base + "] 已有 8K，跳过", flush=True)
            continue
        remote = upload(os.path.join(OUTDIR, f), "up_in_" + base + ".png")
        wf = wf_upscale_8k(remote, base + "_8K")
        pids.append(submit(wf, base)); tags.append(base); meta.append((base, target))
    if not pids:
        print("没有待处理任务", flush=True); return
    done, failed = wait_all(pids, tags, timeout_min=300)
    ok = 0
    for pid in done:
        base = done[pid]
        target = dict(meta)[base]
        src = out_path(pid)
        if src:
            shutil.copy(src, target); ok += 1
            print("  落盘: " + target, flush=True)
    print("=== 阶段二完成：成功 " + str(ok) + " / 失败 " + str(len(failed)) + " ===", flush=True)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "edit":
        stage_edit()
    elif mode == "upscale":
        stage_upscale()
    else:
        print("用法: cos_batch.py edit | upscale", flush=True)
