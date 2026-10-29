# -*- coding: utf-8 -*-
"""
COS 修图批量脚本（Qwen-Image-2.1 · 2K 编辑 · 单阶段 · 每张卸载显存 · 默认 turbo）
==============================================================================
逐张 2K 编辑，每张之间卸载模型+清显存碎片（防 offload 死锁），串行。

用法（用 ComfyUI 自带 python 跑）：
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe cos_batch.py edit
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe cos_batch.py free   # 手动清一次显存

v2.3.1（2026-10-27）：**默认恢复 Q8_0**（40 步）——v2.3.0 曾擅自把默认切成 turbo，
  用户纠正「我啥时候让你切换了」，已回滚；turbo 仅在用户点名时 per-job 写
  "model": "turbo" 启用（`rayQwen21_turboV13Int8convrot`，UNETLoader，16 步 ≈7 分钟/张）。
  实测依据（妲己同提示词同 seed 866176）：turbo 8步=3.7min 细节丢太多；
  16步≈7min 结构清晰但皮肤塑料感/毛发毛刺/特效贴纸感仍在（蒸馏上限，加步数治不了）；
  Q8 40步≈19min 细节最全，仍为默认主力。

v2.2.0（2026-10-26）：每张之间 POST /free 卸载模型+清碎片（防 offload 死锁）。
v2.1.0（2026-10-26）：超分阶段已移除——不做 8K，交付止步 2K。

实机验证事实（改参数前先读，都是踩过的坑）：
  - turbo 模型用普通 `UNETLoader`（safetensors），unet_name 带 `qwen\` 前缀；
    **不是** UnetLoaderGGUFAdvanced——GGUF 三参数（float32/f16/true）只适用 Q8 档
  - Q8 GGUF 三参数：dequant_dtype=float32 + patch_dtype=float16 防黑图（Q8 反量化 NaN）；
    patch_on_device 是 BOOLEAN（True=补丁驻 GPU），别传字符串
  - TextEncodeQwenImage21 图连法（API 形状）：inputs 里 key 写
    "images.image_1": ["LoadImage节点号", 0]，vae 必须连（节点出 latent 直连 KSampler.latent_image）
  - cfg=1.0：负词无效，一切约束写进正向；euler/simple；denoise=1.0；resolution=2048
  - 8G 显存串行：逐张提交，绝不并发；每张后 /free（下张重载 ~1-2 分钟，对比死锁净赚）
  - 断点续跑：已有成图（OUTDIR 里同名文件）自动跳过；脚本崩了重启重跑即可
  - 2026-10-25 踩坑：漏 import shutil，批量第 1 张后崩（已写死在文件头）

耗时基准（RTX 4060Ti 8G 实测）：turbo 16步 ≈7 分钟/张；Q8 40步 ≈19 分钟/张（干净显存满载；
后台占显存软件如剪映/抖音会拖慢 5 倍，渲染前能关则关）
"""
import json, time, shutil, os, sys, urllib.request

SERVER = "http://127.0.0.1:8188"
COMFY = "D:/AI/ComfyUI_windows_portable/ComfyUI/"
OUTDIR = r"C:\Users\user\Desktop\罗莱短剧\coser测试3"      # ← 输出夹（默认=图所在夹）
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
def wf_edit(prompt, image_name, prefix, seed=SEED, resolution=2048, denoise=1.0,
            model="q8", steps=None):
    """2K 图生图编辑。model='turbo'（默认，16步≈7分）或 'q8'（质量档，40步≈19分）。
    turbo 走 UNETLoader（safetensors），Q8 走 UnetLoaderGGUFAdvanced（三参数防黑图）。"""
    lora_chain = None
    if model == "q8":
        node1 = {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {
            "unet_name": "qwen-image-2.1-UC-Q8_0.gguf",
            "dequant_dtype": "float32", "patch_dtype": "float16",
            "patch_on_device": True}}                    # BOOLEAN！不是字符串 "cpu"
        steps = steps if steps else 40
    else:  # turbo 默认
        node1 = {"class_type": "UNETLoader", "inputs": {
            "unet_name": "qwen\\rayQwen21_turboV13Int8convrot.safetensors",
            "weight_dtype": "default"}}
        steps = steps if steps else 16
    ks_model = ["lora", 0] if lora_chain else ["1", 0]
    wf = {
        "1": node1,
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "5": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
            "resolution": resolution, "vae": ["3", 0], "images.image_1": ["4", 0]}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ks_model, "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": denoise,
            "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["5", 2]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }
    if lora_chain:
        wf["lora"] = lora_chain["lora"]
    return wf

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
        model = j.get("model", "q8")
        steps = j.get("steps") or (40 if model == "q8" else 16)
        tag_model = "q8质量档" if model == "q8" else "turbo"
        print("  [" + str(i + 1) + "/" + str(len(jobs)) + "] " + name
              + " | " + tag_model + " " + str(steps) + "步", flush=True)
        remote = upload(j["input"], "cos_in_" + name + ".png")
        wf = wf_edit(j["prompt"], remote, name, seed=j.get("seed", SEED),
                     model=model, steps=j.get("steps"))
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
            # 2026-10-28 大乔单实测：copy失败也从output兜底捞一次
            src = out_path(pid)
            if src:
                try:
                    shutil.copy(src, target); print("  落盘(兜底): " + target, flush=True)
                    fail -= 1; failed_list.pop(); ok += 1
                except Exception as e:
                    print("  兜底拷贝失败: " + repr(e)[:120], flush=True)
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
