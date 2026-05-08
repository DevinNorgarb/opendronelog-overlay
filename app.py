from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st

from opendronelog_overlay.ODL_2_AD import convert_odl_to_airdata
from opendronelog_overlay.cli import render as cli_render
from opendronelog_overlay.config import load_config
from opendronelog_overlay.csv_parser import load_telemetry
from opendronelog_overlay.renderer import _draw_overlay_rgba


st.set_page_config(page_title="OpenDroneLog Overlay", page_icon="🚁", layout="wide")

st.title("OpenDroneLog Overlay")
st.caption("Local-first UI: upload your video + telemetry CSV, align, preview, then export overlay + SRT.")


def _save_upload_to_temp(uploaded, suffix: str) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="odl-overlay-"))
    out = tmp_dir / f"input{suffix}"
    out.write_bytes(uploaded.getbuffer())
    return out


def _video_info(video_path: Path) -> tuple[float, int, int, int]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not open video file")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        raise ValueError("Could not read video metadata (fps/frames/size)")
    return fps, frame_count, width, height


def _alpha_composite_bgra_over_bgr(frame_bgr: np.ndarray, overlay_bgra: np.ndarray) -> np.ndarray:
    if overlay_bgra.shape[:2] != frame_bgr.shape[:2]:
        overlay_bgra = cv2.resize(overlay_bgra, (frame_bgr.shape[1], frame_bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
    overlay_rgb = overlay_bgra[:, :, :3].astype(np.float32)
    alpha = (overlay_bgra[:, :, 3:4].astype(np.float32)) / 255.0
    base = frame_bgr.astype(np.float32)
    out = base * (1.0 - alpha) + overlay_rgb * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _render_preview_frame(
    video_path: Path,
    telemetry_path: Path,
    config_path: Path | None,
    event_time_s: float,
    telemetry_offset_s: float,
) -> np.ndarray:
    cfg = load_config(config_path)
    telemetry = load_telemetry(telemetry_path, unit_system=cfg.telemetry.unit_system)

    cap = cv2.VideoCapture(str(video_path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        raise ValueError("Video FPS is unavailable")
    frame_idx = max(0, int(round(event_time_s * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame_bgr = cap.read()
    cap.release()
    if not ok or frame_bgr is None:
        raise ValueError("Could not read preview frame from video")

    t_telemetry = float(event_time_s) - float(telemetry_offset_s)
    overlay = np.zeros((frame_bgr.shape[0], frame_bgr.shape[1], 4), dtype=np.uint8)
    overlay = _draw_overlay_rgba(overlay, t_telemetry, telemetry, cfg)
    out = _alpha_composite_bgra_over_bgr(frame_bgr, overlay)
    return out


def _render_preview_clip(
    video_path: Path,
    telemetry_path: Path,
    config_path: Path | None,
    event_time_s: float,
    telemetry_offset_s: float,
    clip_len_s: float = 4.0,
    out_fps: float | None = None,
) -> Path:
    cfg = load_config(config_path)
    telemetry = load_telemetry(telemetry_path, unit_system=cfg.telemetry.unit_system)

    fps, frame_count, width, height = _video_info(video_path)
    if out_fps is None:
        out_fps = fps
    clip_len_s = max(1.0, float(clip_len_s))
    start_s = max(0.0, float(event_time_s) - clip_len_s / 2.0)
    end_s = min(float(frame_count) / fps, start_s + clip_len_s)

    start_frame = int(start_s * fps)
    end_frame = int(end_s * fps)
    if end_frame <= start_frame:
        end_frame = min(frame_count, start_frame + int(max(1, clip_len_s * fps)))

    tmp_dir = Path(tempfile.mkdtemp(prefix="odl-preview-"))
    out_path = tmp_dir / "preview.mp4"

    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, out_fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise ValueError("Could not open video writer for preview")

    try:
        for frame_idx in range(start_frame, min(end_frame, frame_count)):
            ok, frame_bgr = cap.read()
            if not ok or frame_bgr is None:
                break
            t_video = frame_idx / fps
            t_telemetry = float(t_video) - float(telemetry_offset_s)

            overlay = np.zeros((height, width, 4), dtype=np.uint8)
            overlay = _draw_overlay_rgba(overlay, t_telemetry, telemetry, cfg)
            composited = _alpha_composite_bgra_over_bgr(frame_bgr, overlay)
            writer.write(composited)
    finally:
        writer.release()
        cap.release()

    return out_path


tab_render, tab_convert = st.tabs(["Render overlay", "Convert ODL → AirData"])

with tab_render:
    st.subheader("1) Upload inputs")
    col_a, col_b = st.columns(2)
    with col_a:
        video_file = st.file_uploader("Video file", type=["mp4", "mov", "mkv", "avi"])
    with col_b:
        csv_file = st.file_uploader("Telemetry CSV (must include `time_s`)", type=["csv"])

    config_file = st.file_uploader("Optional YAML config", type=["yaml", "yml"])

    if video_file and csv_file:
        video_path = _save_upload_to_temp(video_file, suffix=Path(video_file.name).suffix or ".mp4")
        csv_path = _save_upload_to_temp(csv_file, suffix=".csv")
        cfg_path = None
        if config_file is not None:
            cfg_path = _save_upload_to_temp(config_file, suffix=Path(config_file.name).suffix or ".yaml")

        st.subheader("2) Pick calibration event + offset")
        fps, frame_count, width, height = _video_info(video_path)
        duration_s = frame_count / fps

        event_time_s = st.slider("Calibration event timestamp (seconds)", 0.0, float(duration_s), 0.0, 0.1)
        telemetry_offset_s = st.slider(
            "Telemetry offset (seconds)",
            min_value=-30.0,
            max_value=30.0,
            value=0.0,
            step=0.05,
            help="We sample telemetry at (video_time - offset). If overlay looks late, increase offset.",
        )
        clip_len_s = st.slider("Preview clip length (seconds)", 2.0, 8.0, 4.0, 0.5)

        st.subheader("3) Preview (composited on your video)")
        col_p1, col_p2 = st.columns([1, 1])
        with col_p1:
            if st.button("Generate preview frame"):
                with st.spinner("Rendering preview frame..."):
                    frame = _render_preview_frame(video_path, csv_path, cfg_path, event_time_s, telemetry_offset_s)
                st.image(frame, caption=f"Preview frame @ t={event_time_s:.2f}s (offset={telemetry_offset_s:+.2f}s)", channels="BGR")
        with col_p2:
            if st.button("Generate preview clip"):
                with st.spinner("Rendering preview clip..."):
                    preview_path = _render_preview_clip(
                        video_path, csv_path, cfg_path, event_time_s, telemetry_offset_s, clip_len_s=clip_len_s
                    )
                st.video(str(preview_path))
                st.download_button("Download preview clip", data=preview_path.read_bytes(), file_name="preview.mp4")

        st.subheader("4) Export")
        out_name = st.text_input("Output basename", value="overlay")
        if st.button("Export transparent overlay + SRT"):
            with st.spinner("Exporting..."):
                out_dir = Path(tempfile.mkdtemp(prefix="odl-export-"))
                out_overlay = out_dir / f"{out_name}.mov"
                out_srt = out_dir / f"{out_name}.srt"

                cli_render(
                    input_csv=csv_path,
                    output_video=out_overlay,
                    config=cfg_path,
                    output_srt=out_srt,
                    telemetry_offset_s=float(telemetry_offset_s),
                    verbose=0,
                    progress=False,
                )

            st.success("Export complete")
            st.download_button("Download overlay (.mov)", data=out_overlay.read_bytes(), file_name=out_overlay.name)
            st.download_button("Download subtitles (.srt)", data=out_srt.read_bytes(), file_name=out_srt.name)

    else:
        st.info("Upload a video and a telemetry CSV to begin.")

with tab_convert:
    st.subheader("Convert OpenDroneLog CSV → AirData CSV")
    uploaded_file = st.file_uploader("Upload an OpenDroneLog CSV", type="csv", key="odl_convert")

    if uploaded_file:
        input_path = Path("temp_input.csv")
        output_path = Path("airdata_output.csv")

        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        try:
            with st.spinner("Converting..."):
                convert_odl_to_airdata(input_path, output_path)

            st.success("Successfully converted!")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="Download Airdata CSV",
                    data=f,
                    file_name=f"airdata_{uploaded_file.name}",
                    mime="text/csv",
                )

        except Exception as e:
            st.error(f"Error during conversion: {e}")
        finally:
            if input_path.exists():
                os.remove(input_path)
            if output_path.exists():
                os.remove(output_path)