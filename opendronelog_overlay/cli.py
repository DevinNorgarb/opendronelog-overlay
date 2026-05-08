from __future__ import annotations

import logging
from pathlib import Path

import typer

from .config import load_config
from .csv_parser import load_telemetry
from .dji_import import convert_dji_txt_to_odl_csv_via_djirecord
from .ODL_2_AD import convert_odl_to_airdata
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


@app.command("import-dji")
def import_dji(
    input_txt: Path = typer.Option(..., "--input-txt", exists=True, readable=True, help="DJI FlightRecord .txt file"),
    output_csv: Path = typer.Option(..., "--output-csv", help="Output CSV with `time_s` for opendronelog-overlay"),
    output_airdata_csv: Path | None = typer.Option(
        None,
        "--output-airdata-csv",
        help="Optional AirData-style CSV output (generated from the overlay-ready CSV)",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="DJI_API_KEY",
        help="DJI API key for decrypting v13+ flight records (or set DJI_API_KEY)",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Disable TLS verification when djirecord fetches decryption keys (only needed for some environments).",
    ),
) -> None:
    """Convert DJI FlightRecord .txt (binary) into CSV(s) usable by this project."""
    res = convert_dji_txt_to_odl_csv_via_djirecord(
        input_txt=input_txt,
        output_csv=output_csv,
        api_key=api_key,
        no_verify=no_verify,
    )
    typer.echo(f"Wrote overlay-ready CSV: {res.odl_csv_path}")
    if output_airdata_csv is not None:
        output_airdata_csv.parent.mkdir(parents=True, exist_ok=True)
        convert_odl_to_airdata(res.odl_csv_path, output_airdata_csv)
        typer.echo(f"Wrote AirData CSV: {output_airdata_csv}")


@app.command()
def srt(
    input_csv: Path = typer.Option(..., "--input-csv", exists=True, readable=True),
    output_srt: Path = typer.Option(..., "--output-srt"),
    config: Path | None = typer.Option(None, "--config", exists=True, readable=True),
    telemetry_offset_s: float = typer.Option(
        0.0,
        "--telemetry-offset-s",
        help="Seconds to shift telemetry to align with video timeline (positive means telemetry happens earlier).",
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="Increase logging verbosity (-v or -vv)"),
) -> None:
    """Export telemetry subtitles (SRT) without rendering video."""
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

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    cue_count = export_srt(output_srt, telemetry, cfg, telemetry_offset_s=telemetry_offset_s)
    logger.info("SRT export complete with %d cues", cue_count)
    typer.echo(f"Wrote telemetry subtitles: {output_srt}")


if __name__ == "__main__":
    app()
