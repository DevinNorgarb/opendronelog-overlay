## Learned User Preferences

- Prefer red or otherwise high-contrast gauge needles and accents over cyan on blue-sky drone footage.

## Learned Workspace Facts

- With `gauges.x: -1`, gauges auto-stack as full-width vertical rows beneath the telemetry panel; `gauges.width` applies when using manual `gauges.x` / `gauges.y` placement.
- Speed is mapped from CSV columns via aliases including AirData-style names (`speed(m/s)`, `speed(mph)`, `speed(km/h)`, `speed_kmh`) as well as `speed_ms` / `speed_mph`.
- Stacked gauges plus RC sticks need enough canvas height; raise `transparent_output.height`, reduce `gauges.height` / `gauges.gap`, or disable RC sticks if lower gauges are clipped or skipped.
- Example overlay with gauges enabled: `examples/gauges.config.yaml`; dial gauges stay off unless `gauges.enabled: true`.
