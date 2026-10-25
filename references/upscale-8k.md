# 8K 超分（Qwen2.1 Tiled）

## 用什么

现成工作流：`D:\AI\ComfyUI_windows_portable\工作流\放大2_Ray版.json`（AusBoss Tiled Upscale for Qwen Image 2.1）

**为什么必须 tiled**：整图重跑 8K 会爆 8G 显存，实测被 `execution_interrupted` 打断（node KSampler）。tiled 把图切成 2MP 小块逐块重绘再拼回，显存恒定。

**禁止外部超分模型**：4x-UltraSharp / RealESRGAN_x4plus 放大人脸必形变损坏（实测）。

## 原理链

```
LoadImage
  → TiledUpscale(切块，目标45MP，块2MP，overlap128)
  → 每块: TextEncodeQwenImage21(块作参考图 + restore提示词) → KSampler → VAEDecode
  → TiledUpscaleStitch(拼回，keep_colors=1 保原色)
  → SaveImage
```

## 参数表（8K）

| 节点 | 参数 | 值 |
|---|---|---|
| UnetLoaderGGUFAdvanced | unet_name | `qwen\rayQwen21_turboV13Int8convrot.safetensors` |
| | dequant_dtype / patch_dtype | `default` / `default`（该模型是 int8 转换版，非 GGUF quant，无需显式指定） |
| LoRA | qwen-image-2.1-restore | 1.0 |
| LoRA | Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128 | 1.0 |
| CLIPLoader | clip_name / type | `qwen3vl_8b_int8_convrot.safetensors` / `qwen_image` |
| VAELoader | vae_name | `qwen_image_2.1_vae_bf16.safetensors` |
| **AUSBOSS_NODES_TiledUpscale** | megapixels | **45.0**（≈8K；节点上限 64；竖图 2:3 → 5461×8192） |
| | tile_megapixels | 2 |
| | overlap | 128 |
| | multiple | 32 |
| | max_growth | 8 |
| TextEncodeQwenImage21 | prompt | `Restore the photo: remove blur, noise and compression artifacts and recover sharp, natural detail, keeping the same composition, people and colors.` |
| | resolution | 0（每块保持自身尺寸，切勿设 2048——会把块放大） |
| KSampler | steps | 8 |
| | cfg | 1 |
| | sampler / scheduler | `euler` / `simple` |
| | denoise | 1.0 |
| QwenImage21Cache | device / dtype | `auto` / `default` |
| AUSBOSS_NODES_TiledUpscaleStitch | keep_colors | **1.0**（FLOAT 0-1，1=完全保原色） |

## API 提交结构（照此写脚本，别从 UI json 硬转）

```python
wf = {
    # 模型 / 编码器 / VAE
    "1": {"class_type": "UNETLoader", "inputs": {
        "unet_name": "qwen\\rayQwen21_turboV13Int8convrot.safetensors",
        "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
        "type": "qwen_image", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
    # LoRA 链
    "4": {"class_type": "AUSBOSS_NODES_LoraLoader", "inputs": {
        "model": ["1", 0], "clip": ["2", 0],
        "loras": "[{\"name\": \"qwen-image-2.1-restore.safetensors\", \"strength\": 1.0, \"enabled\": true, \"triggers\": \"\"}, "
                 "{\"name\": \"Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors\", \"strength\": 1.0, \"enabled\": true, \"triggers\": \"\"}]",
        "trigger_separator": ", ", "on_missing": "error"}},
    # 输入图
    "5": {"class_type": "LoadImage", "inputs": {"image": IMAGE_NAME, "upload": "image"}},
    # 切块：输出去 tiles(IMAGE批) 与 stitcher
    "6": {"class_type": "AUSBOSS_NODES_TiledUpscale", "inputs": {
        "image": ["5", 0], "megapixels": 45.0, "tile_megapixels": 2.0,
        "overlap": 128, "multiple": 32, "max_growth": 8}},
    # 块级编码：resolution=0 保块原尺寸
    "7": {"class_type": "TextEncodeQwenImage21", "inputs": {
        "clip": ["4", 1], "prompt": RESTORE_PROMPT, "negative_prompt": "",
        "resolution": 0, "vae": ["3", 0], "images.image_1": ["6", 2]}},  # 6.2 = before 单图
    "8": {"class_type": "QwenImage21Cache", "inputs": {
        "model": ["4", 0], "device": "auto", "dtype": "default"}},
    "9": {"class_type": "KSampler", "inputs": {
        "model": ["8", 0], "seed": SEED, "steps": 8, "cfg": 1.0,
        "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["7", 2]}},
    "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
    # 拼接（tiles + stitcher + keep_colors=1）
    "11": {"class_type": "AUSBOSS_NODES_TiledUpscaleStitch", "inputs": {
        "tiles": ["10", 0], "stitcher": ["6", 1], "keep_colors": 1.0}},
    "12": {"class_type": "SaveImage", "inputs": {
        "images": ["11", 0], "filename_prefix": PREFIX}},
}
```

## 三个开关的含义

- `megapixels` — 输出总像素（百万）。8K≈45，4K≈8。**这个决定超到多大**
- `tile_megapixels` — 每块像素，显存开关。8G 卡固定 2，调大爆显存
- `keep_colors` — 1=拼回时匹配原块色彩（保原图配色）；0=让模型自由重绘色彩

## 落盘命名

`原名_cos后期_序号_8K.png`

## 耗时基准（RTX 4060Ti 8G）

| 目标 | 块数（2:3 竖图） | 耗时 |
|---|---|---|
| 4K / 8MP | ~5 | 2-3 分钟 |
| 8K / 45MP | ~23 | 10-15 分钟 |

## 坑

1. **resolution 别设 2048** —— 那是「参考图缩放到 2048」，超分时会把小块放大后重绘，宁设 0。
2. **模型名带子目录** —— `qwen\rayQwen21...`，路径分隔符用反斜杠，缺 `qwen\` 前缀报 not found。
3. **这是 int8 转换模型不是 GGUF** —— 用普通 `UNETLoader`，别套 `UnetLoaderGGUFAdvanced` 那套三参数。
4. **中断排查** —— history 里 `execution_interrupted` + node KSampler = 被手动取消或显存不足，不是代码错。
