"""
MP4 转 GIF 工具 (零内存占用版)

批量将 Input/ 文件夹中的视频文件转换为 GIF 动图,输出到 Output/ 文件夹.

核心设计:
  - 全程委托 ffmpeg 处理,Python 进程零帧数据驻留,
    不使用 filter_complex 单通道(大视频会 OOM),始终两阶段 + GPU 加速
  - 支持并行转换(-p N),充分利用多核 CPU
  - 支持 GPU 硬件加速(--hwaccel),运行时验证,不可用时自动回退 CPU
  - 从 ffmpeg 标准错误流解析进度,实时显示
  - 无任何第三方 Python 依赖(仅需系统安装 ffmpeg)

用法:
    python main.py
    python main.py -i Input -o Output -f 15 -r 480
    python main.py -i Input -o Output -f 10 -r 320x240 -s 00:00:02 -t 5
    python main.py -p 4                              # 4 个视频并行转换
    python main.py --hwaccel cuda                    # 启用 NVIDIA GPU 解码加速
    python main.py --hwaccel auto                    # 自动选择最佳硬件加速
"""

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple


# 支持的视频文件扩展名
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}


class MP4ToGIFConverter:
    """MP4 视频批量转 GIF 动图转换器 (零内存占用版).

    核心思想: 完全委托 ffmpeg 处理视频帧,Python 进程
    不保存任何帧数据.使用 ffmpeg 的 palettegen + paletteuse
    算法生成高质量 GIF.

    优化特性:
      - filter_complex 单通道模式: palettegen + paletteuse 合并为一条
        ffmpeg 命令,视频只需解码一次(速度提升 ~2 倍)
      - 支持并行转换: 多个视频同时处理,充分利用多核 CPU
      - 支持 GPU 硬件加速: 使用 CUDA/VAAPI/QSV 加速视频解码,
        大幅降低 CPU 占用 (用内存/显存换速度)

    属性:
        input_dir:       输入目录路径.
        output_dir:      输出目录路径.
        fps:             GIF 帧率(帧/秒).
        start_time:      裁剪起始时间(秒).
        duration:        裁剪持续时间(秒),None 表示使用视频全长.
        resolution:      输出分辨率.None 表示原尺寸,int 表示宽度(自动保持宽高比),
                         二元组表示 (宽度, 高度).
        parallel_workers: 并行 Worker 数(0 = 自动使用所有 CPU 核心).
        hwaccel:         硬件加速方法(None=自动检测,"cuda","vaapi","qsv","vdpau").
    """

    def __init__(
        self,
        input_dir: str = "Input",
        output_dir: str = "Output",
        fps: int | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
        resolution: tuple[int, int] | int | None = None,
        parallel_workers: int = 0,
        hwaccel: str | None = None,
    ):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.fps = fps
        self.start_time = start_time
        self.duration = duration
        self.resolution = resolution
        # 0 或负值 = 自动检测 CPU 核心数
        self.parallel_workers = (
            parallel_workers if parallel_workers > 0
            else (os.cpu_count() or 1)
        )
        # 硬件加速: None = 自动检测; "none" = 禁用
        self.hwaccel = self._resolve_hwaccel(hwaccel)

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def convert_all(self) -> int:
        """执行批量转换."""
        video_files = self._find_video_files()

        if not video_files:
            print(f"在 '{self.input_dir}' 中未找到视频文件")
            return 0

        self._print_summary(len(video_files))

        # 并行模式: 多个视频同时转换
        if self.parallel_workers > 1 and len(video_files) > 1:
            return self._convert_all_parallel(video_files)

        # 串行模式: 逐个转换
        success_count = 0
        for i, video_path in enumerate(video_files, 1):
            rel_path = os.path.relpath(video_path, self.input_dir)
            gif_path = os.path.join(
                self.output_dir,
                os.path.splitext(rel_path)[0] + ".gif",
            )

            file_label = f"[{i}/{len(video_files)}]"
            print(f"\n{file_label} 处理: {rel_path}")
            print(f"{'─' * (len(file_label) + len(rel_path) + 4)}")

            try:
                self._convert_single(video_path, gif_path)
                success_count += 1
            except Exception as e:
                print(f"  ❌ 转换失败: {e}")

        self._print_footer(success_count, len(video_files))
        return success_count

    def _convert_all_parallel(self, video_files: list[str]) -> int:
        """使用线程池并行转换多个视频."""
        success_count = 0
        total = len(video_files)

        print(f"  ⚡ 并行模式: {self.parallel_workers} 个 worker\n")

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.parallel_workers
        ) as executor:
            futures: dict[concurrent.futures.Future, str] = {}
            for video_path in video_files:
                rel_path = os.path.relpath(video_path, self.input_dir)
                gif_path = os.path.join(
                    self.output_dir,
                    os.path.splitext(rel_path)[0] + ".gif",
                )
                future = executor.submit(
                    self._convert_single, video_path, gif_path
                )
                futures[future] = rel_path

            for i, future in enumerate(
                concurrent.futures.as_completed(futures), 1
            ):
                rel_path = futures[future]
                try:
                    future.result()
                    success_count += 1
                    print(f"  [{i}/{total}] ✅ {rel_path}")
                except Exception as e:
                    print(f"  [{i}/{total}] ❌ {rel_path} — {e}")

        self._print_footer(success_count, total)
        return success_count

    # ------------------------------------------------------------------
    # 硬件加速检测
    # ------------------------------------------------------------------

    def _resolve_hwaccel(self, preferred: str | None) -> str | None:
        """解析并检测可用的硬件加速方法.

        参数:
            preferred: 用户指定的加速方法. None 表示自动检测,
                       "none" 表示禁用,"auto" 表示自动检测,
                       其他字符串为具体方法名.

        返回:
            检测到的硬件加速方法名,或 None(表示使用 CPU 解码).
        """
        if preferred and preferred.lower() == "none":
            return None

        if preferred and preferred.lower() not in ("auto", "", "none"):
            method = preferred.lower()
            if self._check_hwaccel_available(method):
                print(f"  🖥️ 硬件加速: {method.upper()} (用户指定)")
                return method
            print(f"  ⚠ 指定的硬件加速 '{method}' 不可用, 回退 CPU", file=sys.stderr)
            return None

        # 自动检测
        try:
            result = subprocess.run(
                ["ffmpeg", "-hwaccels"],
                capture_output=True, text=True, timeout=5,
            )
            available: set[str] = set()
            for line in result.stdout.splitlines():
                line = line.strip().lower()
                if line and not any(
                    line.startswith(p) for p in ("hardware", "ffmpeg", "built", "libav")
                ):
                    available.add(line)

            # 按性能优先级选择
            for method in ("cuda", "vaapi", "qsv", "vdpau", "drm"):
                if method in available:
                    print(f"  🖥️ 硬件加速: {method.upper()} (自动检测)")
                    return method
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            pass

        return None

    @staticmethod
    def _check_hwaccel_available(method: str) -> bool:
        """检查指定的硬件加速方法是否可用 (实际解码测试).

        流程:
          1. 检查 ffmpeg -hwaccels 是否列出该方法(快速过滤)
          2. 编码一个极小的 h264 测试视频
          3. 用指定 hwaccel 解码该视频 — 这才是真正的"硬件可用"验证

        参数:
            method: 硬件加速方法名 (cuda, vaapi, qsv 等).

        返回:
            True 表示可用, False 表示不可用.
        """
        try:
            # 第一步: 快速检查 ffmpeg 是否列出了该方法
            result = subprocess.run(
                ["ffmpeg", "-hwaccels"],
                capture_output=True, text=True, timeout=5,
            )
            listed = method.lower() in (
                line.strip().lower() for line in result.stdout.splitlines()
            )
            if not listed:
                return False

            # 第二步: 实际解码测试
            # 编码一个极小 h264 视频,再用 hwaccel 解码
            # 这样才能真正验证 libcuda.so/nvidia driver/vaapi driver 是否可用
            with tempfile.TemporaryDirectory() as tmpdir:
                test_video = os.path.join(tmpdir, "test_hw.mp4")

                # 编码 0.1秒 2x2 的 h264 测试视频
                enc_result = subprocess.run(
                    [
                        "ffmpeg",
                        "-f", "lavfi", "-i",
                        "color=c=black:s=2x2:d=0.1",
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-f", "mp4", test_video,
                        "-y",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                if enc_result.returncode != 0 or not os.path.exists(test_video):
                    return False

                # 用指定 hwaccel 解码测试视频
                dec_result = subprocess.run(
                    [
                        "ffmpeg",
                        "-hwaccel", method,
                        "-i", test_video,
                        "-f", "null", "-",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                return dec_result.returncode == 0

        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _find_video_files(self) -> list[str]:
        """递归遍历输入目录,查找所有支持的视频文件."""
        if not os.path.isdir(self.input_dir):
            print(f"目录不存在: {self.input_dir}", file=sys.stderr)
            return []

        video_files: list[str] = []
        for root, _, files in os.walk(self.input_dir):
            for file in sorted(files):
                ext = os.path.splitext(file)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    video_files.append(os.path.join(root, file))

        return video_files

    def _get_video_info(self, input_path: str) -> dict:
        """使用 ffprobe 获取视频信息."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) < 4:
            raise ValueError(f"无法获取视频信息: {input_path}")

        width = int(lines[0].strip())
        height = int(lines[1].strip())

        # r_frame_rate 可能是 "30000/1001" 分数形式
        fps_str = lines[2].strip()
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den)
        else:
            fps = float(fps_str)

        duration_str = lines[3].strip()
        duration = float(duration_str) if duration_str else 0.0

        return {
            "width": width,
            "height": height,
            "fps": fps,
            "duration": duration,
        }

    def _convert_single(self, input_path: str, output_path: str) -> str:
        """将单个视频文件转换为 GIF (零内存占用, 两阶段 + GPU 加速).

        全程委托 ffmpeg 处理,Python 进程不保存任何帧数据.
        使用两阶段算法(先 palettegen 再 paletteuse),内存占用极低.
        每阶段均可选 GPU 硬件加速.

        两阶段 vs filter_complex 单阶段:
          - filter_complex 单阶段虽只需解码一次,但 paletteuse 需缓冲
            所有帧等待调色板,对大视频(如 1080p60@60s)需 >10GB 内存
          - 两阶段解码两次但内存恒定(仅 1 帧 + 调色板 ~1KB)

        流程:
          1. ffprobe 读取视频元数据(仅 KB 级内存)
          2. ffmpeg palettegen: GPU/CPU 解码 → 生成调色板 → 临时文件
          3. ffmpeg paletteuse: GPU/CPU 解码 → 使用调色板 → GIF
          4. 从 ffmpeg stderr 解析进度并显示

        参数:
            input_path:  输入视频文件路径.
            output_path: 输出 GIF 文件路径.

        返回:
            生成的 GIF 文件路径.

        异常:
            FileNotFoundError: ffmpeg/ffprobe 未安装或输入文件不存在.
            RuntimeError: ffmpeg 处理失败.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        ext = os.path.splitext(input_path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            raise ValueError(f"不支持的输入文件格式: {ext}")

        # 检查 ffmpeg
        if not shutil.which("ffmpeg"):
            raise FileNotFoundError(
                "未找到 ffmpeg,请先安装:\n"
                "  Ubuntu/Debian: sudo apt install ffmpeg\n"
                "  macOS: brew install ffmpeg\n"
                "  Windows: choco install ffmpeg"
            )

        if not shutil.which("ffprobe"):
            raise FileNotFoundError(
                "未找到 ffprobe,请先安装 ffmpeg"
            )

        # 确保输出目录存在
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # ── 获取视频信息 ──
        info = self._get_video_info(input_path)

        src_width = info["width"]
        src_height = info["height"]
        src_fps = info["fps"]
        src_duration = info["duration"]

        # 输出帧率
        output_fps = self.fps if self.fps is not None else src_fps

        # 输出分辨率
        out_width, out_height = self._compute_output_size(src_width, src_height)

        # 有效时长
        effective_duration = self.duration
        if effective_duration is None:
            effective_duration = src_duration - self.start_time
            if effective_duration <= 0:
                raise ValueError("裁剪起始时间超出视频时长")

        total_frames = int(effective_duration * output_fps)

        print(f"  视频: {src_width}x{src_height} @ {src_fps:.2f}fps, "
              f"{src_duration:.1f}s")
        print(f"  输出: {out_width}x{out_height} @ {output_fps}fps, "
              f"{total_frames} 帧")

        # ── 构建通用 filter chain ──
        filter_parts = [f"fps={output_fps}"]
        filter_parts.append(
            f"scale={out_width}:{out_height}:flags=lanczos"
        )
        filter_str = ",".join(filter_parts)

        # ── 第一阶段: 生成调色板 ──
        # palettegen 分析视频帧并生成最优 256 色调色板(仅 ~1KB)
        # 写入临时文件而非内存管道,避免 buffering 问题
        with tempfile.TemporaryDirectory() as tmpdir:
            palette_path = os.path.join(tmpdir, "palette.png")

            palette_cmd = self._build_ffmpeg_base(input_path) + [
                "-vf", f"{filter_str},palettegen=stats_mode=diff",
                "-y", palette_path,
            ]

            print(f"  🎨 生成调色板...")
            try:
                self._run_ffmpeg_with_progress(palette_cmd, total_frames)
            except RuntimeError:
                if self.hwaccel:
                    print(f"  ⚠ 硬件加速({self.hwaccel.upper()})失败, "
                          f"回退到 CPU 解码重试...")
                    self.hwaccel = None
                    palette_cmd = self._build_ffmpeg_base(input_path) + [
                        "-vf", f"{filter_str},palettegen=stats_mode=diff",
                        "-y", palette_path,
                    ]
                    self._run_ffmpeg_with_progress(palette_cmd, total_frames)
                else:
                    raise

            if not os.path.exists(palette_path):
                raise RuntimeError("调色板文件未生成")

            # ── 第二阶段: 使用调色板生成 GIF ──
            # paletteuse 将视频帧映射到调色板,生成最终 GIF
            gif_cmd = self._build_ffmpeg_base(input_path) + [
                "-i", palette_path,
                "-lavfi",
                f"{filter_str} [x]; [x][1:v] "
                f"paletteuse=dither=bayer:bayer_scale=5",
                "-y", output_path,
            ]

            print(f"  🎬 生成 GIF...")
            try:
                self._run_ffmpeg_with_progress(gif_cmd, total_frames)
            except RuntimeError:
                if self.hwaccel:
                    print(f"  ⚠ 硬件加速({self.hwaccel.upper()})失败, "
                          f"回退到 CPU 解码重试...")
                    self.hwaccel = None
                    gif_cmd = self._build_ffmpeg_base(input_path) + [
                        "-i", palette_path,
                        "-lavfi",
                        f"{filter_str} [x]; [x][1:v] "
                        f"paletteuse=dither=bayer:bayer_scale=5",
                        "-y", output_path,
                    ]
                    self._run_ffmpeg_with_progress(gif_cmd, total_frames)
                else:
                    raise

        # 验证输出
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("生成的 GIF 文件为空")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  ✅ 转换完成: {output_path} ({file_size_mb:.1f} MB)")
        return output_path

    def _build_ffmpeg_base(self, input_path: str) -> list[str]:
        """构建 ffmpeg 命令基础部分(输入参数).

        使用 -hwaccel 加速解码,但输出到 CPU 内存(无 -hwaccel_output_format),
        确保后续 -vf/-lavfi 滤镜可直接处理,无需 hwdownload.

        参数:
            input_path: 输入视频路径.

        返回:
            ffmpeg 命令列表前缀.
        """
        cmd = ["ffmpeg"]

        # 硬件加速解码(输出到 CPU 内存,不指定 _output_format)
        if self.hwaccel:
            cmd.extend(["-hwaccel", self.hwaccel])

        # -ss 在 -i 之前: 快速 seek(关键帧对齐)
        if self.start_time > 0:
            cmd.extend(["-ss", str(self.start_time)])
        if self.duration is not None:
            cmd.extend(["-t", str(self.duration)])
        cmd.extend(["-i", input_path])

        return cmd

    def _run_ffmpeg_with_progress(
        self, cmd: list[str], total_frames: int
    ) -> None:
        """执行 ffmpeg 命令并解析进度.

        从 ffmpeg 的 stderr 输出中提取 frame 和时间信息,
        实时显示处理进度. 并行模式下自动禁用进度条(避免终端混乱).

        参数:
            cmd:          ffmpeg 命令列表.
            total_frames: 总帧数(用于进度百分比计算).

        异常:
            RuntimeError: ffmpeg 返回非零退出码.
        """
        # 并行模式下不显示实时进度条(多个线程同时输出会混乱)
        show_bar = self.parallel_workers <= 1

        # ffmpeg 进度信息输出到 stderr
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        assert process.stderr is not None

        last_frame = 0
        progress_pattern = re.compile(
            r"frame=\s*(\d+)\s+.*?time=\s*(\d+):(\d+):(\d+\.\d+)"
        )
        # 收集 stderr 用于错误诊断
        stderr_lines: list[str] = []

        try:
            for line in process.stderr:
                line_stripped = line.strip()
                stderr_lines.append(line_stripped)

                # 解析进度行
                match = progress_pattern.search(line_stripped)
                if match and show_bar:
                    frame_num = int(match.group(1))
                    # 只在帧数变化时更新进度(减少输出刷新)
                    if frame_num != last_frame:
                        last_frame = frame_num
                        self._print_progress(frame_num, total_frames)

        finally:
            process.wait()

        # 进度结束
        if show_bar:
            self._print_progress(total_frames, total_frames)
            print()

        if process.returncode != 0:
            # 提取最后 20 行 stderr 用于诊断
            stderr_tail = "\n".join(stderr_lines[-20:])
            raise RuntimeError(
                f"ffmpeg 退出码: {process.returncode}\n"
                f"  stderr(最后20行):\n{stderr_tail}"
            )

    def _compute_output_size(
        self, src_width: int, src_height: int
    ) -> Tuple[int, int]:
        """计算输出分辨率."""
        if self.resolution is None:
            return src_width, src_height

        if isinstance(self.resolution, int):
            ratio = self.resolution / src_width
            return self.resolution, int(src_height * ratio)
        else:
            return self.resolution

    def _print_progress(self, current: int, total: int) -> None:
        """打印进度条."""
        if total <= 0:
            return
        pct = min(current / total * 100, 100)
        bar_len = 30
        filled = int(bar_len * current / total)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"  {bar} {current}/{total} 帧 ({pct:.1f}%)", end="\r")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def parse_time(time_str: str) -> float:
        """将时间字符串转换为秒数.

        支持格式:
            - HH:MM:SS (例如 00:01:30 = 90秒)
            - MM:SS   (例如 01:30 = 90秒)
            - SS      (例如 90 = 90秒)

        参数:
            time_str: 时间字符串.

        返回:
            对应的秒数(浮点数).

        异常:
            ValueError: 如果时间格式无法解析.
        """
        parts = time_str.strip().split(":")

        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            return float(parts[0])
        else:
            raise ValueError(f"无法解析时间格式: {time_str}")

    @staticmethod
    def parse_resolution(res_str: str | None) -> tuple[int, int] | int | None:
        """解析分辨率参数.

        支持格式:
            - "宽度x高度" (例如 "320x240")
            - 单个数字,表示目标宽度(高度自动计算) (例如 "480")

        参数:
            res_str: 分辨率字符串,或 None.

        返回:
            - 如果输入为 None 或空字符串,返回 None.
            - 如果包含 'x',返回 (宽度, 高度) 元组.
            - 否则返回单个宽度整数.
        """
        if not res_str:
            return None

        if "x" in res_str:
            parts = res_str.lower().split("x")
            return (int(parts[0]), int(parts[1]))
        else:
            return int(res_str)

    def _print_summary(self, total: int) -> None:
        """打印转换开始前的摘要信息."""
        print(f"\n{'='*60}")
        print(f"{'='*60}")
        print(f"  输入目录:  {os.path.abspath(self.input_dir)}")
        print(f"  输出目录:  {os.path.abspath(self.output_dir)}")
        print(f"  文件数量:  {total}")
        fps_str = f"{self.fps}" if self.fps is not None else "源视频原始帧率"
        print(f"  FPS:       {fps_str}")
        print(f"  起始时间:  {self.start_time:.1f}s")
        print(f"  持续时间:  {self.duration if self.duration is not None else '全长'}")

        if self.resolution is None:
            res_str = "原尺寸"
        elif isinstance(self.resolution, int):
            res_str = f"宽度 {self.resolution}px(高度自动)"
        else:
            res_str = f"{self.resolution[0]}x{self.resolution[1]}"
        print(f"  分辨率:    {res_str}")
        mode_str = f"并行 x{self.parallel_workers}" if self.parallel_workers > 1 else "串行"
        hw_str = self.hwaccel.upper() if self.hwaccel else "CPU"
        print(f"  解码:      {hw_str}  |  模式: {mode_str} (单通道)")
        print(f"{'='*60}\n")

    @staticmethod
    def _print_footer(success: int, total: int) -> None:
        """打印转换完成后的统计信息."""
        print(f"\n{'='*60}")
        print(f"批量转换完成: 成功 {success}/{total}")
        if success < total:
            print(f"失败: {total - success}")
        print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="批量将视频文件转换为 GIF 动图 (零内存占用版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                    # 使用默认 Input/ → Output/
  %(prog)s -i Input -o Output                 # 指定输入输出目录
  %(prog)s -i Input -o Output -f 15 -r 480    # 指定帧率和宽度
  %(prog)s -i Input -o Output -r 320x240 -s 00:00:02 -t 5  # 裁剪 + 分辨率
        """,
    )

    parser.add_argument(
        "-i", "--input-dir", type=str, default="Input",
        help="输入目录路径,包含视频文件(默认: Input)",
    )
    parser.add_argument(
        "-o", "--output-dir", type=str, default="Output",
        help="输出目录路径,存放生成的 GIF 文件(默认: Output)",
    )
    parser.add_argument(
        "-f", "--fps", type=int, default=None,
        help="GIF 帧率,帧/秒(默认: 使用源视频原始帧率)",
    )
    parser.add_argument(
        "-s", "--start", type=str, default="0",
        help="裁剪起始时间,支持 SS, MM:SS, HH:MM:SS(默认: 0)",
    )
    parser.add_argument(
        "-t", "--duration", type=str,
        help="裁剪持续时间,支持 SS, MM:SS, HH:MM:SS(默认: 视频全长)",
    )
    parser.add_argument(
        "-r", "--resolution", type=str,
        help="输出分辨率,支持 宽度 (如 480) 或 宽度x高度 (如 320x240),默认保持原尺寸",
    )
    parser.add_argument(
        "-p", "--parallel", type=int, default=0,
        help="并行转换数,同时处理多个视频(默认: 0 = 自动使用 CPU 全部核心)",
    )
    parser.add_argument(
        "--hwaccel", type=str, default=None,
        help="硬件加速方法: auto(自动检测), cuda, vaapi, qsv, none(禁用),"
             "默认自动检测(使用 GPU 解码加速)",
    )

    args = parser.parse_args()

    # 参数校验
    if args.fps is not None and args.fps <= 0:
        print("错误: FPS 必须大于 0", file=sys.stderr)
        sys.exit(1)

    # 解析起始时间
    try:
        start_time = MP4ToGIFConverter.parse_time(args.start)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    # 解析持续时间
    duration: float | None = None
    if args.duration:
        try:
            duration = MP4ToGIFConverter.parse_time(args.duration)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)

        if duration <= 0:
            print("错误: 持续时间必须大于 0", file=sys.stderr)
            sys.exit(1)

    # 解析分辨率
    try:
        resolution = MP4ToGIFConverter.parse_resolution(args.resolution)
    except ValueError as e:
        print(f"错误: 分辨率格式无效 - {e}", file=sys.stderr)
        sys.exit(1)

    # 并行 worker 数量 (0 = 自动)
    parallel_workers = args.parallel if args.parallel else 0

    # 硬件加速 (None = 自动检测)
    hwaccel: str | None = args.hwaccel
    if hwaccel and hwaccel.lower() == "auto":
        hwaccel = None  # None = auto-detect

    converter = MP4ToGIFConverter(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        fps=args.fps,
        start_time=start_time,
        duration=duration,
        resolution=resolution,
        parallel_workers=parallel_workers,
        hwaccel=hwaccel,
    )

    success = converter.convert_all()
    sys.exit(0 if success > 0 else 1)
