# -*- coding: utf-8 -*-
"""
4K 点名超分——自切块 GGUF 管线模板（云缨4K/妲己4K 实跑换参版）
================================================================
触发：用户点名某张成图要 4K 才跑（管线默认交付止步 2K，禁止自动超分）。
用法：复制本模板 → 只改【配置块】7 项 → ComfyUI 自带 python 跑：
  D:/AI/ComfyUI_windows_portable/python_embeded/python.exe <本脚本>.py

实机事实：
  - GGUF Q8 三参数防黑图：dequant_dtype=float32 + patch_dtype=float16 + patch_on_device=True
  - restore LoRA 1.0 + 16 步 + 英文 restore 提示词，resolution=0 保持块原尺寸
  - 块 1792² / 重叠 128；先 POST /free 清显存；ledger 断点续跑；单块 error 自动重提一次
  - 4060Ti 8G 约 20min/块，9 块 ≈ 2-3 小时（长活，让用户排期）
  - 8K Ray tiled = 8G 结构性 OOM 勿再用；外部超分模型（4x-UltraSharp/RealESRGAN）毁脸永禁
"""
import json, os, time, shutil, urllib.request, io
from PIL import Image
import numpy as np

SERVER = "http://127.0.0.1:8188"
COMFY_OUT = r"D:\AI\ComfyUI_windows_portable\ComfyUI\output"

# ────────── 【配置块】换单只改这 7 项 ──────────
SRC    = r"C:\Users\user\Desktop\罗莱短剧\coser测试\王者荣耀-妲己原皮_cos后期_02.png"  # 2K 源图
TARGET = r"C:\Users\user\Desktop\罗莱短剧\coser测试\王者荣耀-妲己原皮_cos后期_02_4K.png" # 输出（勿与 SRC 同名）
LEDGER = r"C:\Users\user\.hermes\workspace\daji_4k_ledger.json"
PREFIX = "d4k"            # 上传/输出文件前缀，换单必改（防撞上单遗留文件）
SEED   = 20261026
TARGET_W, TARGET_H = 3520, 4736   # = 源图 x2（先用 PIL 量源图实际尺寸再乘）
# ──────────────────────────────────────────────

TILE = 1792
OVERLAP = 128
STEPS = 16

RESTORE_PROMPT = ("Restore the photo: remove blur, noise and compression artifacts and "
                 "recover sharp, natural detail, keeping the same composition, people and colors.")


def post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=60).read())


def upload(data, remote_name):
    b = "----HermesBoundary7d042"
    body = ("--" + b + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\""
            + remote_name + "\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + data + ("\r\n--" + b + "--\r\n").encode()
    req = urllib.request.Request(SERVER + "/upload/image", body,
                                 {"Content-Type": "multipart/form-data; boundary=" + b})
    return json.loads(urllib.request.urlopen(req, timeout=300).read())["name"]


def wf_tile(remote, prefix, seed=SEED):
    return {
        "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {
            "unet_name": "qwen-image-2.1-UC-Q8_0.gguf",
            "dequant_dtype": "float32", "patch_dtype": "float16", "patch_on_device": True}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["2", 0],
            "lora_name": "qwen-image-2.1-restore.safetensors",
            "strength_model": 1.0, "strength_clip": 1.0}},
        "5": {"class_type": "LoadImage", "inputs": {"image": remote, "upload": "image"}},
        "6": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["4", 1], "prompt": RESTORE_PROMPT, "negative_prompt": "",
            "resolution": 0, "vae": ["3", 0], "images.image_1": ["5", 0]}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "seed": seed, "steps": STEPS, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "positive": ["6", 0], "negative": ["6", 1], "latent_image": ["6", 2]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }


def grid(W, H):
    stride = TILE - OVERLAP
    xs = list(range(0, W - TILE + 1, stride))
    if xs[-1] != W - TILE:
        xs.append(W - TILE)
    ys = list(range(0, H - TILE + 1, stride))
    if ys[-1] != H - TILE:
        ys.append(H - TILE)
    return xs, ys


def ramp(w, h, x, y, W, H):
    a = np.ones((h, w), np.float32)
    if x > 0:
        a *= np.clip(np.arange(w, dtype=np.float32) / OVERLAP, 0, 1)[None, :]
    if x + w < W:
        a *= np.clip((w - 1 - np.arange(w, dtype=np.float32)) / OVERLAP, 0, 1)[None, :]
    if y > 0:
        a *= np.clip(np.arange(h, dtype=np.float32) / OVERLAP, 0, 1)[:, None]
    if y + h < H:
        a *= np.clip((h - 1 - np.arange(h, dtype=np.float32)) / OVERLAP, 0, 1)[:, None]
    return a


def color_match(tile, src):
    out = tile.copy()
    for c in range(3):
        t, s = tile[..., c], src[..., c]
        out[..., c] = (t - t.mean()) / (t.std() + 1e-6) * (s.std() + 1e-6) + s.mean()
    return np.clip(out, 0, 255)


def main():
    if os.path.exists(TARGET):
        print("TARGET exists, skip", flush=True)
        return
    try:
        req = urllib.request.Request(SERVER + "/free",
            json.dumps({"unload_models": True, "free_memory": True}).encode(),
            {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        print("freed", flush=True)
    except Exception as e:
        print("free skipped:", e, flush=True)

    src = Image.open(SRC).convert("RGB")
    big = src.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    arr = np.asarray(big, dtype=np.float32)
    xs, ys = grid(TARGET_W, TARGET_H)
    tiles = [(x, y, min(TILE, TARGET_W - x), min(TILE, TARGET_H - y)) for y in ys for x in xs]
    n = len(tiles)
    print("GRID: %d cols x %d rows = %d tiles" % (len(xs), len(ys), n), flush=True)

    if os.path.exists(LEDGER):
        led = json.load(open(LEDGER, encoding="utf-8"))
        pids = led["pids"]
        tiles = [tuple(t) for t in led["tiles"]]
        print("RESUME with %d pids" % len(pids), flush=True)
    else:
        pids = []
        for i, (x, y, w, h) in enumerate(tiles):
            im = Image.fromarray(arr[y:y + h, x:x + w].astype(np.uint8))
            bio = io.BytesIO(); im.save(bio, "PNG")
            remote = upload(bio.getvalue(), "%s_t%02d.png" % (PREFIX, i))
            pid = post(SERVER + "/prompt", {
                "prompt": wf_tile(remote, "%s_out_t%02d" % (PREFIX, i)),
                "client_id": "%s-tiled" % PREFIX})["prompt_id"]
            pids.append(pid)
            print("SUBMITTED t%02d %s" % (i, pid), flush=True)
        json.dump({"pids": pids, "tiles": [list(t) for t in tiles]},
                  open(LEDGER, "w", encoding="utf-8"))

    t0 = time.time()
    done_files = {}
    retried = set()
    while len(done_files) < n and time.time() - t0 < 14400:
        time.sleep(30)
        for i, pid in enumerate(pids):
            if i in done_files:
                continue
            try:
                h = get(SERVER + "/history/" + pid)
            except Exception:
                continue
            if pid not in h:
                continue
            st = h[pid]["status"]
            if st.get("status_str") == "error":
                msg = ""
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        msg = m[1].get("exception_message", "")[:120]
                if i not in retried:
                    print("t%02d ERROR (%s) -> retry once" % (i, msg), flush=True)
                    retried.add(i)
                    x, y, w, h_ = tiles[i]
                    im = Image.fromarray(arr[y:y + h_, x:x + w].astype(np.uint8))
                    bio = io.BytesIO(); im.save(bio, "PNG")
                    remote = upload(bio.getvalue(), "%s_t%02d.png" % (PREFIX, i))
                    pids[i] = post(SERVER + "/prompt", {
                        "prompt": wf_tile(remote, "%s_out_t%02d" % (PREFIX, i)),
                        "client_id": "%s-tiled" % PREFIX})["prompt_id"]
                    json.dump({"pids": pids, "tiles": [list(t) for t in tiles]},
                              open(LEDGER, "w", encoding="utf-8"))
                else:
                    print("t%02d FAILED twice: %s" % (i, msg), flush=True)
                    return
                continue
            if st.get("completed"):
                for _, o in h[pid].get("outputs", {}).items():
                    for im in o.get("images", []):
                        sub = im.get("subfolder", "")
                        done_files[i] = os.path.join(COMFY_OUT, sub, im["filename"]) if sub \
                            else os.path.join(COMFY_OUT, im["filename"])
                print("t%02d done (%d/%d) elapsed %dmin" %
                      (i, len(done_files), n, int((time.time() - t0) / 60)), flush=True)
    if len(done_files) < n:
        print("TIMEOUT: %d/%d" % (len(done_files), n), flush=True)
        return

    print("STITCHING", flush=True)
    canvas = np.zeros((TARGET_H, TARGET_W, 3), np.float32)
    weight = np.zeros((TARGET_H, TARGET_W, 1), np.float32)
    for i, (x, y, w, h) in enumerate(tiles):
        tile = np.asarray(Image.open(done_files[i]).convert("RGB"), dtype=np.float32)
        if tile.shape[0] != h or tile.shape[1] != w:
            tile = np.asarray(Image.open(done_files[i]).convert("RGB").resize((w, h)), np.float32)
        srct = arr[y:y + h, x:x + w]
        tile = color_match(tile, srct)
        a = ramp(w, h, x, y, TARGET_W, TARGET_H)[..., None]
        canvas[y:y + h, x:x + w] += tile * a
        weight[y:y + h, x:x + w] += a
    out = np.clip(canvas / np.maximum(weight, 1e-6), 0, 255).astype(np.uint8)
    Image.fromarray(out).save(TARGET)
    os.remove(LEDGER)
    print("SAVED", TARGET, flush=True)
    print("DONE total %dmin" % int((time.time() - t0) / 60), flush=True)


if __name__ == "__main__":
    main()
