from __future__ import annotations

from datetime import datetime
from typing import Any

# Events that pair a "called" with its ack, per position
_PAIRS = {
    "standby_called": ("standby_acked", "standby"),
    "go_called": ("go_acked", "go"),
}


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def build_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Computes a post-show report from the show log. Pure — reads the
    entries, stores nothing."""
    show: dict[str, Any] = {
        "started": "",
        "ended": "",
        "duration_s": None,
        "standbys_called": 0,
        "gos_called": 0,
        "master_gos": 0,
        "cue_advances": 0,
        "attention_raised": 0,
    }
    positions: dict[str, dict[str, Any]] = {}
    # position -> {"standby": ts, "go": ts} of the latest unacked call
    pending: dict[str, dict[str, datetime]] = {}
    cue_gaps: list[dict[str, Any]] = []
    prev_master_go: tuple[datetime, str] | None = None

    def pos_stats(label: str) -> dict[str, Any]:
        if label not in positions:
            positions[label] = {
                "standbys": 0, "standby_acks": 0, "standby_ack_latencies": [],
                "gos": 0, "go_acks": 0, "go_ack_latencies": [],
                "attention": 0, "osc_fires": 0,
            }
        return positions[label]

    first_time: datetime | None = None
    last_time: datetime | None = None
    show_started: datetime | None = None

    for e in entries:
        t = _parse_time(e.get("time", ""))
        label = e.get("position", "")
        event = e.get("event", "")
        if t:
            first_time = first_time or t
            last_time = t

        if event == "show_started" and t:
            show_started = t
        elif event == "standby_called" and label:
            show["standbys_called"] += 1
            pos_stats(label)["standbys"] += 1
            if t:
                pending.setdefault(label, {})["standby"] = t
        elif event == "go_called" and label:
            show["gos_called"] += 1
            pos_stats(label)["gos"] += 1
            if t:
                pending.setdefault(label, {})["go"] = t
        elif event in ("standby_acked", "go_acked") and label:
            kind = "standby" if event == "standby_acked" else "go"
            stats = pos_stats(label)
            stats[f"{kind}_acks"] += 1
            called_at = pending.get(label, {}).pop(kind, None)
            if t and called_at:
                stats[f"{kind}_ack_latencies"].append((t - called_at).total_seconds())
        elif event == "master_go":
            show["master_gos"] += 1
            if t:
                if prev_master_go:
                    cue_gaps.append({
                        "cue": prev_master_go[1],
                        "gap_s": round((t - prev_master_go[0]).total_seconds(), 1),
                    })
                prev_master_go = (t, e.get("cue", ""))
        elif event == "cue_advanced":
            show["cue_advances"] += 1
        elif event == "attention_raised" and label:
            show["attention_raised"] += 1
            pos_stats(label)["attention"] += 1
        elif event == "osc_fired" and label:
            pos_stats(label)["osc_fires"] += 1

    start = show_started or first_time
    if start:
        show["started"] = start.isoformat(timespec="seconds")
    if last_time:
        show["ended"] = last_time.isoformat(timespec="seconds")
    if start and last_time:
        show["duration_s"] = round((last_time - start).total_seconds(), 1)

    # Collapse latency lists into avg/max
    for stats in positions.values():
        for kind in ("standby", "go"):
            lat = stats.pop(f"{kind}_ack_latencies")
            stats[f"{kind}_ack_avg_s"] = round(sum(lat) / len(lat), 2) if lat else None
            stats[f"{kind}_ack_max_s"] = round(max(lat), 2) if lat else None

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "show": show,
        "positions": positions,
        "cue_gaps": cue_gaps,
    }


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def report_to_text(report: dict[str, Any]) -> str:
    """Renders the report as a plain-text page a stage manager can save or print."""
    show = report["show"]
    lines = [
        "CUELIGHT SHOW REPORT",
        f"generated {report['generated_at']}",
        "",
        f"  started    {show['started'] or '—'}",
        f"  ended      {show['ended'] or '—'}",
        f"  duration   {_fmt_duration(show['duration_s'])}",
        f"  master GOs {show['master_gos']}   cue advances {show['cue_advances']}",
        f"  standbys   {show['standbys_called']}   GOs {show['gos_called']}"
        f"   attention {show['attention_raised']}",
        "",
        "POSITIONS",
    ]
    if not report["positions"]:
        lines.append("  (none)")
    for label, s in report["positions"].items():
        lines.append(f"  {label}")
        lines.append(
            f"    standbys {s['standbys']} (acked {s['standby_acks']}"
            f", avg {s['standby_ack_avg_s'] if s['standby_ack_avg_s'] is not None else '—'}s"
            f", max {s['standby_ack_max_s'] if s['standby_ack_max_s'] is not None else '—'}s)"
        )
        lines.append(
            f"    GOs      {s['gos']} (acked {s['go_acks']}"
            f", avg {s['go_ack_avg_s'] if s['go_ack_avg_s'] is not None else '—'}s"
            f", max {s['go_ack_max_s'] if s['go_ack_max_s'] is not None else '—'}s)"
        )
        if s["osc_fires"]:
            lines.append(f"    OSC fires {s['osc_fires']}")
        if s["attention"]:
            lines.append(f"    attention raised {s['attention']}")
    if report["cue_gaps"]:
        lines.append("")
        lines.append("TIME BETWEEN MASTER GOs")
        for g in report["cue_gaps"]:
            cue = f"after cue {g['cue']}" if g["cue"] else "(no cue)"
            lines.append(f"  {cue:<16} {_fmt_duration(g['gap_s'])}")
    lines.append("")
    return "\n".join(lines)
