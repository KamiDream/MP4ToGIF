"""
MP4 转 GIF 工具

批量将 Input/ 文件夹中的视频文件转换为 GIF 动图,输出到 Output/ 文件夹.

用法:
    python main.py
    python main.py -i Input -o Output -f 15 -r 480
    python main.py -i Input -o Output -f 10 -r 320x240 -s 00:00:02 -t 5
"""

import argparse
import os
import sys

try:
    from moviepy import VideoFileClip
except ImportError:
    print("错误: 缺少 moviepy 库,请运行: pip install moviepy", file=sys.stderr)
    sys.exit(1)

# 支持的视频文件扩展名
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}


class MP4ToGIFConverter:
    """MP4 视频批量转 GIF 动图转换器.

    遍历输入目录中的所有视频文件,将其转换为 GIF 动图,
    并保持目录结构输出到指定输出目录.

    属性:
        input_dir:  输入目录路径.
        output_dir: 输出目录路径.
        fps:        GIF 帧率(帧/秒).
        start_time: 裁剪起始时间(秒).
        duration:   裁剪持续时间(秒),None 表示使用视频全长.
        resolution: 输出分辨率.None 表示原尺寸,int 表示宽度(自动保持宽高比),
                    二元组表示 (宽度, 高度).
    """

    def __init__(
        self,
        input_dir: str = "Input",
        output_dir: str = "Output",
        fps: int | None = None,
        start_time: float = 0.0,
        duration: float | None = None,
        resolution: tuple[int, int] | int | None = None,
    ):
        """初始化转换器.

        参数:
            input_dir:  输入目录路径(默认: "Input").
            output_dir: 输出目录路径(默认: "Output").
            fps:        GIF 帧率,None 表示使用源视频原始帧率(默认: None).
            start_time: 裁剪起始时间,单位秒(默认: 0).
            duration:   裁剪持续时间,单位秒.None 表示使用视频全长(默认: None).
            resolution: 输出分辨率.None 表示原尺寸,int 表示宽度(自动保持宽高比),
                        (宽, 高) 元组指定精确尺寸(默认: None).
        """
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.fps = fps
        self.start_time = start_time
        self.duration = duration
        self.resolution = resolution

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def convert_all(self) -> int:
        """执行批量转换.

        扫描输入目录中的所有视频文件,逐个转换为 GIF,
        并保持目录结构输出到输出目录.

        返回:
            成功转换的文件数量.
        """
        video_files = self._find_video_files()

        if not video_files:
            print(f"在 '{self.input_dir}' 中未找到视频文件")
            return 0

        self._print_summary(len(video_files))

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

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _find_video_files(self) -> list[str]:
        """递归遍历输入目录,查找所有支持的视频文件.

        返回:
            视频文件路径列表,按文件名排序.
        """
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

    def _convert_single(self, input_path: str, output_path: str) -> str:
        """将单个视频文件转换为 GIF.

        参数:
            input_path:  输入视频文件路径.
            output_path: 输出 GIF 文件路径.

        返回:
            生成的 GIF 文件路径.

        异常:
            FileNotFoundError: 如果输入文件不存在.
            ValueError:       如果文件格式不支持.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        ext = os.path.splitext(input_path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            raise ValueError(f"不支持的输入文件格式: {ext}")

        # 确保输出目录存在
        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        print(f"  正在加载视频: {input_path}")
        with VideoFileClip(input_path) as clip:
            # 使用源视频原始帧率(如果用户未指定)
            output_fps = self.fps if self.fps is not None else clip.fps

            # 裁剪时间
            if self.start_time > 0 or self.duration is not None:
                end_time = None
                if self.duration is not None:
                    end_time = self.start_time + self.duration
                clip = clip.subclipped(self.start_time, end_time)

            # 缩放分辨率
            if self.resolution is not None:
                if isinstance(self.resolution, int):
                    clip = clip.resized(width=self.resolution)
                else:
                    clip = clip.resized(self.resolution)

            total_frames = int(clip.duration * output_fps)
            print(f"  视频时长: {clip.duration:.2f} 秒")
            print(f"  帧率: {output_fps} FPS{'(原始)' if self.fps is None else ''}")
            print(f"  总帧数: {total_frames}")
            print(f"  正在生成 GIF: {output_path}")

            clip.write_gif(
                output_path,
                fps=output_fps,
                logger="bar",
            )

        print(f"  ✅ 转换完成: {output_path}")
        return output_path

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
        print(f"MP4 → GIF 批量转换")
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
        description="批量将视频文件转换为 GIF 动图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                  # 使用默认 Input/ → Output/
  %(prog)s -i Input -o Output               # 指定输入输出目录
  %(prog)s -i Input -o Output -f 15 -r 480  # 指定帧率和宽度
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

    converter = MP4ToGIFConverter(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        fps=args.fps,
        start_time=start_time,
        duration=duration,
        resolution=resolution,
    )

    success = converter.convert_all()
    sys.exit(0 if success > 0 else 1)

