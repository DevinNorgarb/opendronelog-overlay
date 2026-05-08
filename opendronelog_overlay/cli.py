from __future__ import annotations

import logging
from pathlib import Path

import typer

from .config import load_config
from .csv_parser import load_telemetry
from .renderer import render_overlay_transparent_video
from .srt_exporter import export_srt

app = typer.Typer(help="Drone telemetry overlay renderer")


@app.command()
def render(
    input_csv: Path = typer.Option(..., "--input-csv", exists=True, readable=True),
    output_video: Path = typer.Option(..., "--output-video"),
    config: Path | None = typer.Option(None, "--config", exists=True, readable=True),
    output_srt: Path | None = typer.Option(None, "--output-srt", help="Optional SRT output path for selected telemetry"),
    telemetry_offset_s: float = typer.Option(
        0.0,
        "--telemetry-offset-s",
        help="Seconds to shift telemetry to align with video timeline (positive means telemetry happens earlier).",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="Increase logging verbosity (-v or -vv)"),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show frame conversion progress bar"),
) -> None:
    """Render telemetry as a transparent alpha overlay clip."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG

    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)

    logger.info("Loading config and telemetry")
    cfg = load_config(config)
    telemetry = load_telemetry(input_csv, unit_system=cfg.telemetry.unit_system)
    logger.info("Telemetry loaded: %d samples", len(telemetry.time_s))

    output_video.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Rendering transparent overlay clip")
    render_overlay_transparent_video(
        output_video_path=str(output_video),
        telemetry=telemetry,
        config=cfg,
        telemetry_offset_s=telemetry_offset_s,
        show_progress=progress,
        verbose=verbose > 0,
    )

    if output_srt is not None:
        if output_srt.with_suffix("").resolve() == output_video.with_suffix("").resolve():
            logger.warning(
                "output_srt has the same basename as output_video; some players auto-load subtitles and may make previews look cluttered"
            )
        output_srt.parent.mkdir(parents=True, exist_ok=True)
        cue_count = export_srt(output_srt, telemetry, cfg, telemetry_offset_s=telemetry_offset_s)
        logger.info("SRT export complete with %d cues", cue_count)
        typer.echo(f"Wrote telemetry subtitles: {output_srt}")

    typer.echo(f"Wrote overlay video: {output_video}")


if __name__ == "__main__":
    app()
