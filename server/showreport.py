"""Post-show report, computed on demand from the show log.

Nothing here is persisted: the report is derived from the in-memory log
entries (which mirror state/showlog.jsonl) every time it is requested.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime
from typing import Any


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def compute_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates the log into report data.

    Standby latency: each standby_called opens a pending window for that
    position; the next standby_acked closes it. A go_called auto-clears a
    pending standby (server behavior), so it counts as never-acked. A
    re-called standby restarts the window without counting the first one.
    """
    latencies: list[dict[str, Any]] = []
    unacked: list[dict[str, str]] = []
    pending: dict[str, tuple[datetime, str, bool]] = {}
    go_times: list[tuple[datetime, str]] = []
    auto_standbys = 0
    manual_standbys = 0
    joins: dict[str, dict[str, int]] = {}
    problems: list[dict[str, str]] = []
    show_started: list[str] = []

    for e in entries:
        t = _parse_time(e.get("time", ""))
        event = e.get("event", "")
        position = e.get("position", "")
        cue = e.get("cue", "")
        detail = e.get("detail", "")

        if event == "standby_called":
            if detail == "auto":
                auto_standbys += 1
            else:
                manual_standbys += 1
            if t is not None:
                pending[position] = (t, cue, detail == "auto")
        elif event == "standby_acked":
            opened = pending.pop(position, None)
            if opened and t is not None:
                latencies.append({
                    "position": position,
                    "cue": opened[1],
                    "ms": (t - opened[0]).total_seconds() * 1000,
                    "auto": opened[2],
                })
        elif event == "go_called":
            opened = pending.pop(position, None)
            if opened:
                unacked.append({"position": position, "cue": opened[1]})
        elif event == "master_go" and t is not None:
            go_times.append((t, cue))
        elif event == "position_joined":
            joins.setdefault(position, {"joins": 0, "disconnects": 0})["joins"] += 1
        elif event == "position_disconnected":
            joins.setdefault(position, {"joins": 0, "disconnects": 0})["disconnects"] += 1
        elif event == "problem_raised":
            problems.append({
                "time": e.get("time", ""),
                "position": position,
                "cue": cue,
                "message": detail,
                "cleared": "",
            })
        elif event == "problem_cleared":
            for p in reversed(problems):
                if p["position"] == position and not p["cleared"]:
                    p["cleared"] = detail
                    break
        elif event == "show_started":
            show_started.append(e.get("time", ""))

    # Standbys still pending when the log ends were never acknowledged
    for position, (_, cue, _) in pending.items():
        unacked.append({"position": position, "cue": cue})

    per_position: dict[str, dict[str, float]] = {}
    for lat in latencies:
        stats = per_position.setdefault(lat["position"], {"count": 0, "min": 0.0, "max": 0.0, "sum": 0.0})
        ms = lat["ms"]
        if stats["count"] == 0:
            stats["min"] = stats["max"] = ms
        else:
            stats["min"] = min(stats["min"], ms)
            stats["max"] = max(stats["max"], ms)
        stats["count"] += 1
        stats["sum"] += ms
    position_stats = [
        {
            "position": pos,
            "count": int(s["count"]),
            "min_ms": s["min"],
            "avg_ms": s["sum"] / s["count"],
            "max_ms": s["max"],
        }
        for pos, s in per_position.items()
    ]

    go_intervals = [
        {"cue": go_times[i][1], "seconds": (go_times[i + 1][0] - go_times[i][0]).total_seconds()}
        for i in range(len(go_times) - 1)
    ]

    return {
        "show_started": show_started,
        "latencies": latencies,
        "position_stats": position_stats,
        "unacked": unacked,
        "go_intervals": go_intervals,
        "auto_standbys": auto_standbys,
        "manual_standbys": manual_standbys,
        "joins": joins,
        "problems": problems,
        "entry_count": len(entries),
    }


def _fmt_ms(ms: float) -> str:
    return f"{ms:.0f}"


def report_html(entries: list[dict[str, Any]]) -> str:
    r = compute_report(entries)
    esc = html.escape

    def table(headers: list[str], rows: list[list[str]], empty: str) -> str:
        if not rows:
            return f'<p class="empty">{esc(empty)}</p>'
        head = "".join(f"<th>{esc(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    started = ", ".join(esc(t) for t in r["show_started"]) or "never (START SHOW not pressed)"
    stats_rows = [
        [s["position"], str(s["count"]), _fmt_ms(s["min_ms"]), _fmt_ms(s["avg_ms"]), _fmt_ms(s["max_ms"])]
        for s in sorted(r["position_stats"], key=lambda s: s["position"].lower())
    ]
    latency_rows = [
        [lat["position"], lat["cue"] or "—", _fmt_ms(lat["ms"]), "auto" if lat["auto"] else "manual"]
        for lat in r["latencies"]
    ]
    unacked_rows = [[u["position"], u["cue"] or "—"] for u in r["unacked"]]
    go_rows = [[g["cue"] or "—", f"{g['seconds']:.1f}"] for g in r["go_intervals"]]
    join_rows = [
        [pos, str(c["joins"]), str(c["disconnects"])]
        for pos, c in sorted(r["joins"].items(), key=lambda kv: kv[0].lower())
    ]
    problem_rows = [
        [p["time"], p["position"], p["cue"] or "—", p["message"] or "(no message)",
         (f"cleared {p['cleared']}" if p["cleared"] else "not cleared")]
        for p in r["problems"]
    ]

    body = f"""
<h1>CueLight Show Report</h1>
<p class="meta">Show started: {started} · {r["entry_count"]} log entries</p>

<h2>Standby response per position</h2>
<p class="hint">Time from a standby being called to the operator acknowledging it.</p>
{table(["Position", "Acks", "Min (ms)", "Avg (ms)", "Max (ms)"], stats_rows, "No acknowledged standbys.")}

<h2>Standby response per cue</h2>
{table(["Position", "Cue", "Latency (ms)", "Called"], latency_rows, "No acknowledged standbys.")}

<h2>Standbys never acknowledged</h2>
{table(["Position", "Cue"], unacked_rows, "None — every standby was acknowledged.")}

<h2>Time between GOs</h2>
<p class="hint">Elapsed time from each master GO to the next, labelled with the cue it fired.</p>
{table(["Cue", "Seconds to next GO"], go_rows, "Fewer than two master GOs fired.")}

<h2>Standby calls</h2>
{table(["Manual", "Auto"], [[str(r["manual_standbys"]), str(r["auto_standbys"])]], "")}

<h2>Joins and disconnects</h2>
{table(["Position", "Joins", "Disconnects"], join_rows, "No positions joined.")}

<h2>Problems raised</h2>
{table(["Time", "Position", "Cue", "Message", "Status"], problem_rows, "No problems raised.")}
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CueLight Show Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 32px auto; max-width: 860px; padding: 0 16px; color: #222; }}
h1 {{ font-size: 24px; }}
h2 {{ font-size: 16px; margin-top: 28px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
table {{ border-collapse: collapse; margin-top: 8px; width: 100%; }}
th, td {{ text-align: left; padding: 4px 10px; border-bottom: 1px solid #eee; font-size: 13px; }}
th {{ color: #666; font-weight: 600; }}
.meta {{ color: #666; font-size: 13px; }}
.hint {{ color: #888; font-size: 12px; margin: 2px 0; }}
.empty {{ color: #999; font-size: 13px; font-style: italic; }}
</style>
</head>
<body>{body}</body>
</html>
"""


def report_csv(entries: list[dict[str, Any]]) -> str:
    """Flat 5-column CSV: section,position,cue,metric,value."""
    r = compute_report(entries)
    buf = io.StringIO()
    writer = csv.writer(buf)

    _DANGEROUS = ("=", "+", "-", "@", "\t", "\r")

    def _safe(row):
        return ["'" + v if isinstance(v, str) and v[:1] in _DANGEROUS else v for v in row]

    def write(row):
        writer.writerow(_safe(row))

    write(["section", "position", "cue", "metric", "value"])
    for t in r["show_started"]:
        write(["show_started", "", "", "time", t])
    for s in r["position_stats"]:
        write(["standby_latency", s["position"], "", "count", s["count"]])
        write(["standby_latency", s["position"], "", "min_ms", _fmt_ms(s["min_ms"])])
        write(["standby_latency", s["position"], "", "avg_ms", _fmt_ms(s["avg_ms"])])
        write(["standby_latency", s["position"], "", "max_ms", _fmt_ms(s["max_ms"])])
    for lat in r["latencies"]:
        write(["standby_latency_per_cue", lat["position"], lat["cue"],
               "auto" if lat["auto"] else "manual", _fmt_ms(lat["ms"])])
    for u in r["unacked"]:
        write(["unacked_standby", u["position"], u["cue"], "", ""])
    for g in r["go_intervals"]:
        write(["go_interval", "", g["cue"], "seconds", f"{g['seconds']:.1f}"])
    write(["standby_counts", "", "", "manual", r["manual_standbys"]])
    write(["standby_counts", "", "", "auto", r["auto_standbys"]])
    for pos, c in r["joins"].items():
        write(["join_summary", pos, "", "joins", c["joins"]])
        write(["join_summary", pos, "", "disconnects", c["disconnects"]])
    for p in r["problems"]:
        write(["problem", p["position"], p["cue"], p["cleared"] or "not cleared", p["message"]])
    return buf.getvalue()
