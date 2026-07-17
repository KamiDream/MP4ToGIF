# MP4ToGIF

批量将视频文件转换为 GIF 动图的命令行工具。全程委托 ffmpeg 处理，零第三方 Python 依赖。

## 功能特性

- **批量转换** — 遍历输入目录所有视频，保持目录结构输出到 `Output/`
- **高性能** — 两阶段 palettegen+paletteuse 算法，最佳 GIF 质量
- **GPU 硬件加速** — 自动检测 CUDA/VAAPI/QSV，加速视频解码（`--hwaccel`）
- **并行转换** — 默认使用全部 CPU 核心同时处理多个视频（`-p`）
- **零内存占用** — 视频帧全程在 ffmpeg 进程内处理，Python 进程不保存帧数据
- **多种视频格式** — 支持 `.mp4`、`.mov`、`.avi`、`.mkv`、`.webm`、`.flv`
- **参数自定义** — 支持自定义帧率、分辨率、裁剪起始时间和时长
- **进度显示** — 实时显示转换进度条和统计信息

## 环境要求

- Python >= 3.14
- [ffmpeg](https://ffmpeg.org/)（系统安装，含 `ffprobe`）

### 安装 ffmpeg

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows
choco install ffmpeg
```

## 快速开始

```bash
# 将视频放入 Input/ 目录，然后运行
python main.py

# 输出在 Output/ 目录中
```

## 使用方法

### 目录结构

```
项目目录/
├── Input/              # 放入要转换的视频文件（含子目录）
│   ├── video1.mp4
│   └── sub/
│       └── video2.mov
├── Output/             # 自动生成的 GIF 输出目录
│   ├── video1.gif
│   └── sub/
│       └── video2.gif
└── main.py
```

### 基础用法

```bash
# 默认转换（使用原始帧率和分辨率，自动全核并行）
python main.py

# 指定输入输出目录
python main.py -i Input -o Output

# 自定义帧率
python main.py -f 15

# 自定义分辨率
python main.py -r 480          # 宽度 480px，高度自动保持宽高比
python main.py -r 320x240      # 精确指定宽高

# 裁剪视频片段
python main.py -s 00:00:02 -t 5    # 从第 2 秒开始，截取 5 秒

# 组合使用
python main.py -i Input -o Output -f 15 -r 480
```

### GPU 硬件加速

工具会自动检测可用的 GPU 解码方案，也可以通过 `--hwaccel` 手动指定：

```bash
# 自动检测（默认行为）
python main.py

# 指定加速方法
python main.py --hwaccel cuda          # NVIDIA GPU
python main.py --hwaccel vaapi         # Intel/AMD GPU (Linux)
python main.py --hwaccel qsv           # Intel QuickSync
python main.py --hwaccel none          # 强制使用 CPU 解码
```

> **注意**：`ffmpeg -hwaccels` 列出 CUDA 不代表 CUDA 可用，还需安装 NVIDIA 驱动（`libcuda.so`）。工具会运行时验证，自动回退到 CPU。

### 并行转换

默认使用全部 CPU 核心并行处理多个视频，可通过 `-p` 调整：

```bash
python main.py                     # 默认自动检测 CPU 核心数
python main.py -p 2                # 限制 2 个并行
python main.py -p 1                # 串行模式（显示完整进度条）
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i` / `--input-dir` | 输入目录路径 | `Input` |
| `-o` / `--output-dir` | 输出目录路径 | `Output` |
| `-f` / `--fps` | GIF 帧率（帧/秒） | 源视频原始帧率 |
| `-s` / `--start` | 裁剪起始时间 | `0` |
| `-t` / `--duration` | 裁剪持续时间 | 视频全长 |
| `-r` / `--resolution` | 输出分辨率（`宽度` 或 `宽度x高度`） | 源视频原始尺寸 |
| `-p` / `--parallel` | 并行 Worker 数 | `0`（自动检测 CPU 核心数） |
| `--hwaccel` | 硬件加速方法：`cuda`, `vaapi`, `qsv`, `none`, `auto` | 自动检测 |

> **时间格式**：支持 `SS`（秒）、`MM:SS`（分:秒）、`HH:MM:SS`（时:分:秒）。
> 例如 `90`、`01:30`、`00:01:30` 都表示 90 秒。

## 性能优化

工具采用了多项优化来加速 GIF 生成：

| 优化 | 说明 | 适用场景 |
|------|------|----------|
| **两阶段 palettegen+paletteuse** | 先分析视频生成最优 256 色调色板，再映射为 GIF | 所有视频（内存安全） |
| **GPU 硬件加速解码** | 使用 CUDA/VAAPI/QSV 硬件解码器加速视频读取 | 有兼容 GPU 的系统 |
| **并行转换** | 多个视频同时转换，充分利用多核 CPU | 批量处理多文件 |
| **快速 seek** | `-ss` 放在 `-i` 之前，跳转关键帧快速定位 | 裁剪片段时 |

## 项目结构

```
├── main.py          # 主程序（MP4ToGIFConverter 类）
├── pyproject.toml   # 项目配置
├── README.md        # 本文件
├── .python-version  # Python 版本
├── Input/           # 输入目录
└── Output/          # 输出目录
```

## 技术细节

- **零第三方 Python 依赖**：仅需系统安装 `ffmpeg`，通过 `subprocess` 调用
- **palettegen + paletteuse**：ffmpeg 内置的双通道 GIF 优化算法，比直接 `ffmpeg -f gif` 质量更高
- **帧率控制**：默认使用 `fps` 滤镜精确控制输出帧率
- **缩放算法**：使用 `lanczos` 滤镜进行高质量图像缩放
