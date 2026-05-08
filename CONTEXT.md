# OpenDroneLog Overlay

A local-first toolchain for turning drone telemetry logs into video overlays (transparent clips) and SRT subtitles.

## Language

**Telemetry log**:
A time series of flight data exported as CSV, sampled over time.
_Avoid_: log file, flight log (too broad)

**Video timeline**:
The time axis of the exported video, measured in seconds from the first frame.
_Avoid_: playback time

**Alignment**:
Choosing how telemetry time maps onto the video timeline so displayed values match what the viewer sees.
_Avoid_: sync (too vague)

**Telemetry offset**:
A constant number of seconds added to telemetry time (or subtracted) to align it to the video timeline.
_Avoid_: delay, lag (ambiguous sign)

**Calibration event**:
A visually identifiable moment in the video used to infer a telemetry offset (e.g. takeoff, first movement).
_Avoid_: marker (too generic)

**Preview**:
A lightweight render (single frame or short clip) used to validate alignment before running a full export.
_Avoid_: test render (sounds like automated testing)

## Relationships

- A **Telemetry log** is sampled along the **Video timeline** to produce an **Overlay**
- **Alignment** is achieved by choosing a **Telemetry offset**
- A **Calibration event** can be used to infer a **Telemetry offset**
- A **Preview** is used to validate **Alignment** before a full export

## Example dialogue

> **Dev:** "If the numbers are late, should I move the **Telemetry offset** positive or negative?"
> **Domain expert:** "Make the overlay match the **Video timeline** at takeoff — that **Calibration event** should line up, then the rest will be close enough."

## Flagged ambiguities

- “sync” vs “align” — resolved: use **Alignment** for the mapping; use **Telemetry offset** for the specific constant-shift knob.
