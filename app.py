from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from streamlit_drawable_canvas import st_canvas

from flightframe.ODL_2_AD import convert_odl_to_airdata
from flightframe.cli import render as cli_render
from flightframe.csv_parser import TelemetryData, load_telemetry
from flightframe.dji_import import convert_dji_txt_to_odl_csv_via_djirecord
from opendronelog_overlay.config import (
    ComponentRect,
    OverlayComponent,
    OverlayConfig,
    dump_config_yaml,
    load_config,
)
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


tab_render, tab_build, tab_convert = st.tabs(["Render overlay", "Build config", "Convert ODL → AirData"])

with tab_render:
    st.subheader("1) Upload inputs")
    col_a, col_b = st.columns(2)
    with col_a:
        video_file = st.file_uploader("Video file", type=["mp4", "mov", "mkv", "avi"])
    with col_b:
        telemetry_source = st.radio("Telemetry source", ["CSV (time_s)", "DJI FlightRecord .txt"], horizontal=True)

        csv_file = None
        txt_file = None
        if telemetry_source.startswith("CSV"):
            csv_file = st.file_uploader("Telemetry CSV (must include `time_s`)", type=["csv"])
        else:
            txt_file = st.file_uploader("DJI FlightRecord .txt (binary)", type=["txt"])

    config_file = st.file_uploader("Optional YAML config", type=["yaml", "yml"])

    resolved_csv_path: Path | None = None
    generated_airdata_path: Path | None = None

    if "dji_import_csv_path" in st.session_state:
        try:
            p = Path(st.session_state["dji_import_csv_path"])
            if p.exists():
                resolved_csv_path = p
        except Exception:
            pass

    if video_file and telemetry_source.startswith("CSV") and csv_file:
        video_path = _save_upload_to_temp(video_file, suffix=Path(video_file.name).suffix or ".mp4")
        resolved_csv_path = _save_upload_to_temp(csv_file, suffix=".csv")
        cfg_path = None
        if config_file is not None:
            cfg_path = _save_upload_to_temp(config_file, suffix=Path(config_file.name).suffix or ".yaml")

    if video_file and telemetry_source.startswith("DJI") and txt_file:
        video_path = _save_upload_to_temp(video_file, suffix=Path(video_file.name).suffix or ".mp4")
        cfg_path = None
        if config_file is not None:
            cfg_path = _save_upload_to_temp(config_file, suffix=Path(config_file.name).suffix or ".yaml")

        st.markdown("**DJI .txt import** uses the external `djirecord` command (install via `pipx install pydjirecord`).")
        dji_api_key = st.text_input("DJI API key (only needed for v13+ encrypted logs)", type="password")
        dji_no_verify = st.checkbox("Disable TLS verification (djirecord --no-verify)", value=False)
        make_airdata = st.checkbox("Also generate AirData CSV", value=True)

        if st.button("Import DJI .txt → CSV"):
            with st.spinner("Decoding DJI FlightRecord..."):
                txt_path = _save_upload_to_temp(txt_file, suffix=".txt")
                out_dir = Path(tempfile.mkdtemp(prefix="odl-dji-ui-"))
                out_csv = out_dir / "flight.csv"
                res = convert_dji_txt_to_odl_csv_via_djirecord(
                    input_txt=txt_path,
                    output_csv=out_csv,
                    api_key=(dji_api_key or None),
                    no_verify=dji_no_verify,
                )
                resolved_csv_path = res.odl_csv_path
                st.session_state["dji_import_csv_path"] = str(resolved_csv_path)

                if make_airdata:
                    airdata_csv = out_dir / "flight.airdata.csv"
                    convert_odl_to_airdata(resolved_csv_path, airdata_csv)
                    generated_airdata_path = airdata_csv
                    st.session_state["dji_import_airdata_path"] = str(airdata_csv)

            st.success("Import complete")

        if "dji_import_airdata_path" in st.session_state and generated_airdata_path is None:
            try:
                p = Path(st.session_state["dji_import_airdata_path"])
                if p.exists():
                    generated_airdata_path = p
            except Exception:
                pass

        if resolved_csv_path is not None and resolved_csv_path.exists():
            st.download_button(
                "Download imported overlay-ready CSV",
                data=resolved_csv_path.read_bytes(),
                file_name="flight.csv",
            )
        if generated_airdata_path is not None and generated_airdata_path.exists():
            st.download_button(
                "Download imported AirData CSV",
                data=generated_airdata_path.read_bytes(),
                file_name="flight.airdata.csv",
            )

    if video_file and resolved_csv_path is not None:
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
                    frame = _render_preview_frame(video_path, resolved_csv_path, cfg_path, event_time_s, telemetry_offset_s)
                st.image(frame, caption=f"Preview frame @ t={event_time_s:.2f}s (offset={telemetry_offset_s:+.2f}s)", channels="BGR")
        with col_p2:
            if st.button("Generate preview clip"):
                with st.spinner("Rendering preview clip..."):
                    preview_path = _render_preview_clip(
                        video_path, resolved_csv_path, cfg_path, event_time_s, telemetry_offset_s, clip_len_s=clip_len_s
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
                    input_csv=resolved_csv_path,
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
        st.info("Upload a video and pick a telemetry source to begin.")

with tab_build:
    st.subheader("Build overlay config (drag, resize, export YAML)")
    st.caption("Drag/resize components on a canvas. Download the resulting `overlay.config.yaml`.")

    # Start from defaults; user can optionally load an existing YAML and then edit.
    base_config_file = st.file_uploader("Optional starting YAML config", type=["yaml", "yml"], key="build_yaml")
    if base_config_file is not None:
        base_cfg_path = _save_upload_to_temp(base_config_file, suffix=Path(base_config_file.name).suffix or ".yaml")
        cfg = load_config(base_cfg_path)
    else:
        cfg = load_config(None)

    # Canvas size controls (pixel-accurate to transparent output).
    st.markdown("**Canvas size (matches `transparent_output`)**")
    c1, c2, c3 = st.columns(3)
    with c1:
        cfg.transparent_output.width = int(st.number_input("Width", min_value=320, max_value=7680, value=int(cfg.transparent_output.width), step=10))
    with c2:
        cfg.transparent_output.height = int(st.number_input("Height", min_value=240, max_value=4320, value=int(cfg.transparent_output.height), step=10))
    with c3:
        cfg.transparent_output.fps = float(st.number_input("FPS", min_value=1.0, max_value=240.0, value=float(cfg.transparent_output.fps), step=1.0))

    st.markdown("**Theme**")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        cfg.theme.panel_bg_hex = st.color_picker("Panel BG", value=cfg.theme.panel_bg_hex)
        cfg.theme.accent_hex = st.color_picker("Accent", value=cfg.theme.accent_hex)
    with t2:
        cfg.theme.label_text_hex = st.color_picker("Label text", value=cfg.theme.label_text_hex)
        cfg.theme.value_text_hex = st.color_picker("Value text", value=cfg.theme.value_text_hex)
    with t3:
        cfg.theme.muted_text_hex = st.color_picker("Muted text", value=cfg.theme.muted_text_hex)
        cfg.theme.arc_hex = st.color_picker("Gauge arc", value=cfg.theme.arc_hex)
    with t4:
        cfg.theme.tick_hex = st.color_picker("Tick", value=cfg.theme.tick_hex)

    # Palette (add components).
    st.markdown("**Add components**")
    pcol1, pcol2 = st.columns([2, 3])
    with pcol1:
        new_type = st.selectbox("Component type", ["value_card", "rc_sticks", "dial_gauge", "sparkline", "compass"])
    with pcol2:
        new_id = st.text_input("Component id", value=f"{new_type}_{len(cfg.components)+1}")

    if st.button("Add component"):
        cfg.components.append(
            OverlayComponent(
                id=new_id,
                type=new_type,
                rect=ComponentRect(x=40, y=40, w=260, h=180),
                config={},
                style={},
            )
        )

    if not cfg.components:
        st.info("Add at least one component to start editing. (Existing legacy configs won’t have `components:`.)")
    else:
        # Build initial Fabric objects from components.
        initial_objects = []
        for comp in cfg.components:
            r = comp.rect
            initial_objects.append(
                {
                    "type": "rect",
                    "left": r.x,
                    "top": r.y,
                    "width": r.w,
                    "height": r.h,
                    "fill": "rgba(0,0,0,0.08)",
                    "stroke": cfg.theme.accent_hex,
                    "strokeWidth": 2,
                    "name": comp.id,
                }
            )

        st.markdown("**Layout editor**")
        canvas = st_canvas(
            fill_color="rgba(0, 0, 0, 0.02)",
            stroke_width=2,
            stroke_color=cfg.theme.accent_hex,
            background_color="rgba(0, 0, 0, 0)",
            height=int(cfg.transparent_output.height),
            width=int(cfg.transparent_output.width),
            drawing_mode="transform",
            initial_drawing={"version": "4.4.0", "objects": initial_objects},
            update_streamlit=True,
            key="overlay_builder_canvas",
        )

        # Apply canvas object transforms back to component rects (by object.name == id).
        if canvas.json_data and isinstance(canvas.json_data, dict):
            objs = canvas.json_data.get("objects") or []
            by_name = {}
            for o in objs:
                if isinstance(o, dict) and isinstance(o.get("name"), str):
                    by_name[o["name"]] = o

            for comp in cfg.components:
                o = by_name.get(comp.id)
                if not o:
                    continue
                left = float(o.get("left", comp.rect.x))
                top = float(o.get("top", comp.rect.y))
                ow = float(o.get("width", comp.rect.w))
                oh = float(o.get("height", comp.rect.h))
                sx = float(o.get("scaleX", 1.0) or 1.0)
                sy = float(o.get("scaleY", 1.0) or 1.0)
                comp.rect.x = int(round(left))
                comp.rect.y = int(round(top))
                comp.rect.w = max(1, int(round(ow * sx)))
                comp.rect.h = max(1, int(round(oh * sy)))

        st.markdown("**Components**")
        ids = [c.id for c in cfg.components]
        selected = st.selectbox("Select component", ids)
        comp = next(c for c in cfg.components if c.id == selected)

        # Fine-tune rect and per-component config.
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            comp.rect.x = int(st.number_input("x", value=int(comp.rect.x), step=1))
        with r2:
            comp.rect.y = int(st.number_input("y", value=int(comp.rect.y), step=1))
        with r3:
            comp.rect.w = int(st.number_input("w", min_value=1, value=int(comp.rect.w), step=1))
        with r4:
            comp.rect.h = int(st.number_input("h", min_value=1, value=int(comp.rect.h), step=1))

        # Type-specific configuration.
        if comp.type == "value_card":
            st.markdown("**Value card config**")
            fields_txt = st.text_input("fields (comma-separated)", value=",".join(comp.config.get("fields", cfg.telemetry.include)))
            comp.config["fields"] = [s.strip() for s in fields_txt.split(",") if s.strip()]
        elif comp.type == "dial_gauge":
            st.markdown("**Dial gauge config**")
            comp.config["field"] = st.text_input("field", value=str(comp.config.get("field", "speed")))
            comp.config["label"] = st.text_input("label", value=str(comp.config.get("label", "")))
        elif comp.type == "sparkline":
            st.markdown("**Sparkline config**")
            comp.config["field"] = st.text_input("field", value=str(comp.config.get("field", "speed")))
            comp.config["window_s"] = float(st.number_input("window_s", min_value=0.5, max_value=60.0, value=float(comp.config.get("window_s", 5.0)), step=0.5))
        elif comp.type == "compass":
            st.markdown("**Compass config**")
            comp.config["field"] = st.text_input("field", value=str(comp.config.get("field", "heading_deg")))
            comp.config["label"] = st.text_input("label", value=str(comp.config.get("label", "Heading")))

        st.markdown("**Layering**")
        b1, b2, b3 = st.columns(3)
        idx = ids.index(comp.id)
        with b1:
            if st.button("Move up") and idx > 0:
                cfg.components[idx - 1], cfg.components[idx] = cfg.components[idx], cfg.components[idx - 1]
        with b2:
            if st.button("Move down") and idx < len(cfg.components) - 1:
                cfg.components[idx + 1], cfg.components[idx] = cfg.components[idx], cfg.components[idx + 1]
        with b3:
            if st.button("Delete component"):
                cfg.components = [c for c in cfg.components if c.id != comp.id]

    # YAML export
    yaml_text = dump_config_yaml(cfg)
    st.download_button(
        "Download overlay config YAML",
        data=yaml_text.encode("utf-8"),
        file_name="overlay.config.yaml",
        mime="text/yaml",
    )

    # Lightweight preview image (no video needed): draw overlay at t=0 with empty telemetry -> renders mostly n/a,
    # but is useful for layout.
    if st.button("Preview overlay frame (layout only)"):
        dummy = np.zeros((cfg.transparent_output.height, cfg.transparent_output.width, 4), dtype=np.uint8)
        # Create minimal telemetry arrays to satisfy renderer; values will be zeros.
        telem = TelemetryData(
            time_s=np.array([0.0, 1.0], dtype=np.float64),
            numeric={},
            text={},
            units={},
        )
        out = _draw_overlay_rgba(dummy, 0.0, telem, cfg)
        st.image(out, caption="Overlay preview (transparent BG)", channels="BGRA")

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