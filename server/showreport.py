from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from typing import Any

# Characters that spreadsheet apps interpret as formula prefixes
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> object:
    """Prefix formula-triggering cells with a single quote so they are treated
    as plain text by Excel, Google Sheets, etc."""
    if isinstance(value, str) and value[:1] in _DANGEROUS:
        return "'" + value
    return value

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


def _str(v: object) -> str:
    """Render a value as a string, using '—' for None."""
    return "—" if v is None else str(v)


def report_to_csv(report: dict[str, Any]) -> str:
    """Renders the report as a CSV attachment safe against formula-injection.

    Columns: section, label, metric, value.
    Every cell is passed through _safe() to neutralise spreadsheet formula prefixes.
    """
    show = report["show"]
    buf = io.StringIO()
    writer = csv.writer(buf)

    def row(*cells: object) -> None:
        writer.writerow([_safe(str(c) if not isinstance(c, str) else c) for c in cells])

    row("section", "label", "metric", "value")

    # Show summary
    row("show_summary", "", "generated_at", report["generated_at"])
    row("show_summary", "", "started", show["started"] or "—")
    row("show_summary", "", "ended", show["ended"] or "—")
    row("show_summary", "", "duration", _fmt_duration(show["duration_s"]))
    row("show_summary", "", "standbys_called", show["standbys_called"])
    row("show_summary", "", "gos_called", show["gos_called"])
    row("show_summary", "", "master_gos", show["master_gos"])
    row("show_summary", "", "cue_advances", show["cue_advances"])
    row("show_summary", "", "attention_raised", show["attention_raised"])

    # Per-position stats
    for label, s in report["positions"].items():
        row("position", label, "standbys", s["standbys"])
        row("position", label, "standby_acks", s["standby_acks"])
        row("position", label, "standby_ack_avg_s", _str(s["standby_ack_avg_s"]))
        row("position", label, "standby_ack_max_s", _str(s["standby_ack_max_s"]))
        row("position", label, "gos", s["gos"])
        row("position", label, "go_acks", s["go_acks"])
        row("position", label, "go_ack_avg_s", _str(s["go_ack_avg_s"]))
        row("position", label, "go_ack_max_s", _str(s["go_ack_max_s"]))
        if s["osc_fires"]:
            row("position", label, "osc_fires", s["osc_fires"])
        if s["attention"]:
            row("position", label, "attention_raised", s["attention"])

    # Cue gaps
    for g in report["cue_gaps"]:
        cue_label = f"after cue {g['cue']}" if g["cue"] else "(no cue)"
        row("cue_gap", cue_label, "gap_s", g["gap_s"])

    return buf.getvalue()


def report_to_html(report: dict[str, Any]) -> str:
    """Renders the report as a self-contained HTML page (dark theme, no external deps)."""
    show = report["show"]
    esc = html.escape

    def th(*headers: str) -> str:
        return "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr>"

    def td(*cells: object) -> str:
        return "<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in cells) + "</tr>"

    def table(header_row: str, body_rows: list[str]) -> str:
        body = "\n".join(body_rows) if body_rows else '<tr><td class="empty" colspan="99">(none)</td></tr>'
        return f"<table>\n<thead>{header_row}</thead>\n<tbody>{body}</tbody>\n</table>"

    # Show summary section
    summary_rows = [
        td("Generated", esc(report["generated_at"])),
        td("Started", esc(show["started"] or "—")),
        td("Ended", esc(show["ended"] or "—")),
        td("Duration", esc(_fmt_duration(show["duration_s"]))),
        td("Standbys called", show["standbys_called"]),
        td("GOs called", show["gos_called"]),
        td("Master GOs", show["master_gos"]),
        td("Cue advances", show["cue_advances"]),
        td("Attention raised", show["attention_raised"]),
    ]
    summary_tbl = table(th("Metric", "Value"), summary_rows)

    # Positions section
    pos_rows = []
    for label, s in report["positions"].items():
        avg_sb = _str(s["standby_ack_avg_s"])
        max_sb = _str(s["standby_ack_max_s"])
        avg_go = _str(s["go_ack_avg_s"])
        max_go = _str(s["go_ack_max_s"])
        pos_rows.append(td(
            label,
            s["standbys"], s["standby_acks"], avg_sb + "s", max_sb + "s",
            s["gos"], s["go_acks"], avg_go + "s", max_go + "s",
            s["osc_fires"], s["attention"],
        ))
    pos_tbl = table(
        th("Position",
           "Standbys", "Acked", "SB avg", "SB max",
           "GOs", "GO acked", "GO avg", "GO max",
           "OSC fires", "Attention"),
        pos_rows,
    )

    # Cue gaps section
    gap_rows = [
        td(f"after cue {g['cue']}" if g["cue"] else "(no cue)", _fmt_duration(g["gap_s"]))
        for g in report["cue_gaps"]
    ]
    gap_tbl = table(th("Cue", "Gap"), gap_rows)

    css = """
body{background:#1a1a1a;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     margin:32px auto;max-width:960px;padding:0 16px}
h1{font-size:22px;color:#fff}
h2{font-size:14px;color:#aaa;margin-top:28px;border-bottom:1px solid #333;padding-bottom:4px}
table{border-collapse:collapse;margin-top:8px;width:100%}
th,td{text-align:left;padding:5px 10px;border-bottom:1px solid #2e2e2e;font-size:13px}
th{color:#888;font-weight:600}
.empty{color:#555;font-style:italic}
"""

    body = f"""<h1>CueLight Show Report</h1>
<h2>Summary</h2>
{summary_tbl}
<h2>Positions</h2>
{pos_tbl}
<h2>Time Between Master GOs</h2>
{gap_tbl}
"""
    return (
        f'<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>CueLight Show Report</title>\n"
        f"<style>{css}</style>\n"
        f"</head>\n<body>\n{body}</body>\n</html>\n"
    )
