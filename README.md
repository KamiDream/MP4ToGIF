# MP4ToGIF

批量将 MP4 等视频文件转换为 GIF 动图的命令行工具。

## 功能特性

- **批量转换** — 遍历 `Input/` 目录中的所有视频文件，保持目录结构输出到 `Output/`
- **保持原始参数** — 默认使用源视频的原始帧率和分辨率，输出高质量 GIF
- **参数自定义** — 支持自定义帧率、分辨率、裁剪起始时间和时长
- **多种视频格式** — 支持 `.mp4`、`.mov`、`.avi`、`.mkv`、`.webm`、`.flv`
- **进度显示** — 实时显示转换进度条和统计信息

## 环境要求

- Python >= 3.14
- [ffmpeg](https://ffmpeg.org/)（系统安装）

## 安装

```bash
# 克隆项目
git clone https://github.com/your-username/MP4ToGIF.git
cd MP4ToGIF

# 安装依赖
pip install -e .
# 或
pip install moviepy
```

## 使用方法

### 目录结构

```
项目目录/
├── Input/          # 放入要转换的视频文件（含子目录）
│   ├── video1.mp4
│   └── sub/
│       └── video2.mp4
├── Output/         # 自动生成的 GIF 输出目录
│   ├── video1.gif
│   └── sub/
│       └── video2.gif
└── main.py
```

### 基础用法

```bash
# 默认转换（使用原始帧率和分辨率）
python main.py

# 指定输入输出目录
python main.py -i Input -o Output

# 自定义帧率（覆盖原始帧率）
python main.py -f 15

# 自定义分辨率（覆盖原始分辨率）
python main.py -r 480          # 宽度 480px，高度自动保持宽高比
python main.py -r 320x240      # 精确指定宽高

# 裁剪视频片段
python main.py -s 00:00:02 -t 5    # 从第 2 秒开始，截取 5 秒

# 组合使用
python main.py -i Input -o Output -f 15 -r 480 -s 00:00:05 -t 3
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

> **时间格式说明**: 支持 `SS`（秒）、`MM:SS`（分:秒）、`HH:MM:SS`（时:分:秒）三种格式。
> 例如 `90`、`01:30`、`00:01:30` 都表示 90 秒。

## 项目结构

```
├── main.py          # 主程序（MP4ToGIFConverter 类）
├── pyproject.toml   # 项目配置与依赖
├── README.md        # 本文件
└── .python-version  # Python 版本
```

## 依赖

- [moviepy](https://github.com/Zulko/moviepy) — 视频处理库（底层依赖 ffmpeg）
