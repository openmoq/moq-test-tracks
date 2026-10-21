# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# ffmpeg command lines that render a TrackSpec's content and encode it to the
# spec's codecs, shared by the source stage and ffmpeg-based adapters.

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from ..spec.fields import AUDIO_SAMPLE_RATE, TrackSpec

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LOOP_TARGET_S = 60

FORMATS = ("mpegts", "mp4")


class RecipeError(ValueError):
    pass


def _rate(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def _video_input(spec: TrackSpec) -> list[str]:
    size = f"{spec.video_width}x{spec.video_height}"
    rate = _rate(spec.fps)
    if spec.content == "ts":
        frame_s = float(1 / spec.fps)
        fontsize = max(12, spec.video_height // 18)
        graph = (
            f"testsrc2=size={size}:rate={rate},"
            f"drawtext=fontfile={FONT}:fontsize={fontsize}:fontcolor=white:box=1:boxcolor=black@0.6"
            ":x=(w-tw)/2:y=h-2*th:text='%{gmtime\\:%T} f %{n}',"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='lt(mod(t,1),{frame_s:.6f})'"
        )
    elif spec.content == "bars":
        graph = f"smptehdbars=size={size}:rate={rate}"
    elif spec.content == "noise":
        graph = f"color=c=gray:size={size}:rate={rate},noise=alls=100:allf=t+u"
    else:
        raise RecipeError(f"no recipe for content {spec.content}")
    return ["-f", "lavfi", "-i", graph]


def _audio_input(spec: TrackSpec) -> list[str]:
    layout = "mono" if spec.audio_channels == 1 else "stereo"
    rate = AUDIO_SAMPLE_RATE
    if spec.content == "ts":
        expr = "if(lt(mod(t\\,1)\\,0.1)\\,0.5*sin(2*PI*1000*t)\\,0.3*sin(2*PI*440*t))"
        graph = f"aevalsrc=exprs={expr}:s={rate}:c={layout}"
    elif spec.content == "bars":
        graph = f"sine=frequency=1000:sample_rate={rate},aformat=channel_layouts={layout}"
    elif spec.content == "noise":
        graph = f"anoisesrc=sample_rate={rate}:color=white:amplitude=0.5,aformat=channel_layouts={layout}"
    else:
        raise RecipeError(f"no recipe for content {spec.content}")
    return ["-f", "lavfi", "-i", graph]


def _video_encoder(spec: TrackSpec, fmt: str) -> list[str]:
    gop = int(spec.frames_per_group)
    kbps = f"{spec.video_kbps}k"
    rate = ["-b:v", kbps, "-maxrate", kbps, "-bufsize", kbps]
    common = ["-r", _rate(spec.fps), "-pix_fmt", "yuv420p", "-g", str(gop), "-keyint_min", str(gop)]
    if spec.video_codec == "h264":
        return [
            "-c:v", "libx264", "-profile:v", "baseline", "-preset", "veryfast",
            "-tune", "zerolatency", "-bf", "0", "-sc_threshold", "0",
            "-x264-params", "repeat-headers=1", *common, *rate,
        ]  # fmt: skip
    if spec.video_codec == "h265":
        params = f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:repeat-headers=1"
        tag = ["-tag:v", "hvc1"] if fmt == "mp4" else []
        return [
            "-c:v", "libx265", "-preset", "veryfast", "-tune", "zerolatency",
            "-x265-params", params, *tag, *common, *rate,
        ]  # fmt: skip
    if spec.video_codec == "av1":
        return [
            "-c:v", "libsvtav1", "-preset", "10",
            "-svtav1-params", f"keyint={gop}:scd=0:pred-struct=1", *common, *rate,
        ]  # fmt: skip
    raise RecipeError(f"no encoder for video codec {spec.video_codec}")


def _audio_encoder(spec: TrackSpec) -> list[str]:
    common = ["-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(spec.audio_channels)]
    if spec.audio_codec == "aac":
        return ["-c:a", "aac", "-b:a", f"{spec.audio_kbps}k", *common]
    if spec.audio_codec == "opus":
        return [
            "-c:a", "libopus", "-b:a", f"{spec.audio_kbps}k", "-frame_duration", "20",
            "-application", "lowdelay", *common,
        ]  # fmt: skip
    raise RecipeError(f"no encoder for audio codec {spec.audio_codec}")


def loop_seconds(spec: TrackSpec) -> Fraction:
    """Loop length near LOOP_TARGET_S that is a whole number of video groups."""
    if spec.group_ms is None:
        return Fraction(LOOP_TARGET_S)
    groups = math.ceil(LOOP_TARGET_S * 1000 / spec.group_ms)
    return Fraction(groups * spec.group_ms, 1000)


def source_command(spec: TrackSpec, fmt: str, output: str | Path) -> list[str]:
    """ffmpeg argv producing the spec's media: a live MPEG-TS stream, or a
    loopable non-fragmented mp4 of loop_seconds() length."""
    if fmt not in FORMATS:
        raise RecipeError(f"unknown source format {fmt}")
    if spec.content == "clip":
        raise RecipeError("clip sources need a fetched mezzanine")
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
    live = fmt == "mpegts"
    inputs, maps, encoders = [], [], []
    if spec.has_video:
        inputs += (["-re"] if live else []) + _video_input(spec)
        encoders += _video_encoder(spec, fmt)
    if spec.has_audio:
        inputs += (["-re"] if live else []) + _audio_input(spec)
        encoders += _audio_encoder(spec)
    for i in range(int(spec.has_video) + int(spec.has_audio)):
        maps += ["-map", str(i)]
    argv += inputs + maps + encoders
    if live:
        argv += ["-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", str(output)]
    else:
        seconds = loop_seconds(spec)
        argv += ["-t", f"{float(seconds):.3f}", "-movflags", "+faststart", "-f", "mp4", str(output)]
    return argv
