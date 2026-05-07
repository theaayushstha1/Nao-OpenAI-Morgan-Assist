# -*- coding: utf-8 -*-
"""Vision robustness battery — feed multiple images through observe_face
and the therapist agent, see how the system handles each.

Tests:
  01_mac_screenshot      — desktop screenshot, no face. Edge case.
  02_stretched_face      — face squashed to 200x800 then back. Distortion.
  03_second_webcam       — second webcam capture, different moment.
  04_original_webcam     — control: untouched webcam capture.
  05_extreme_wide        — face stretched to 2400x480 (5:1). Severe distortion.
  06_solid_red           — pure red 512x512. No content. Edge case.

Usage:
  set -a; source .env; set +a
  CS_NAVIGATOR_URL=https://api.inavigator.ai \\
    python -m sim.vision_battery
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

IMG_DIR = Path(os.environ.get("VISION_BATTERY_DIR", "/tmp/live_proof_imgs"))
OUT_DIR = Path(__file__).resolve().parent / "reports" / "vision_battery"

TRANSCRIPT = "I'm feeling really anxious about my midterms"


def run_one(name: str, img_path: Path) -> dict:
    """Run observe_face + therapist on one image."""
    from server.tools.emotion import _observe_face_impl
    from server import _legacy_helpers as legacy

    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    # observe_face
    t0 = time.perf_counter()
    obs = _observe_face_impl({"latest_image_b64": b64})
    obs_ms = (time.perf_counter() - t0) * 1000.0

    # therapist with image
    t1 = time.perf_counter()
    reply, agent, actions, _ = legacy.run_agent(
        username="vision_battery_user",
        hint="therapy",
        transcript=TRANSCRIPT,
        image_b64=b64,
    )
    rep_ms = (time.perf_counter() - t1) * 1000.0

    return {
        "name": name,
        "img_path": str(img_path),
        "img_size": img_path.stat().st_size,
        "observe_face_ms": round(obs_ms, 0),
        "observe_face_out": obs,
        "therapist_ms": round(rep_ms, 0),
        "therapist_agent": agent,
        "therapist_actions": [a.get("name") for a in (actions or [])],
        "therapist_reply": reply,
    }


def main(argv: list[str] | None = None) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in IMG_DIR.glob("*.jpg") if p.is_file())
    if not images:
        print(f"no images in {IMG_DIR}", file=sys.stderr)
        return 2

    print(f"=== Vision battery @ {stamp} ===")
    print(f"transcript: {TRANSCRIPT!r}")
    print(f"{len(images)} images:")
    for p in images:
        print(f"  • {p.name}  ({p.stat().st_size} bytes)")
    print()

    results = []
    for img in images:
        name = img.stem
        print(f"▸ {name} ...", flush=True)
        try:
            row = run_one(name, img)
            results.append(row)
            obs = row["observe_face_out"]
            if isinstance(obs, dict):
                obs_str = json.dumps(obs, ensure_ascii=False)
            else:
                obs_str = str(obs)
            print(f"   observe_face ({row['observe_face_ms']:.0f} ms): {obs_str[:200]}")
            print(f"   therapist ({row['therapist_ms']:.0f} ms, agent={row['therapist_agent']!r},"
                  f" actions={row['therapist_actions']}):")
            for line in (row["therapist_reply"] or "").splitlines():
                print(f"     {line}")
        except Exception as e:
            print(f"   FAIL: {e!r}")
            results.append({"name": name, "error": repr(e), "img_path": str(img)})
        print()

    # Persist
    payload = {
        "generated_at": stamp,
        "transcript": TRANSCRIPT,
        "image_count": len(results),
        "images": results,
    }
    json_path = run_dir / "battery.json"
    md_path = run_dir / "battery.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")

    # Markdown
    md = ["# Vision robustness battery", "",
          f"> Generated: `{stamp}`",
          f"> Transcript fed to therapist: **{TRANSCRIPT}**",
          f"> Image source: `{IMG_DIR}`",
          ""]
    md.append("## Summary table")
    md.append("")
    md.append("| Image | Bytes | observe_face | Therapist (ms) | Therapist reply |")
    md.append("|---|---|---|---|---|")
    for r in results:
        if "error" in r:
            md.append(f"| `{r['name']}` | - | ERROR | - | `{r['error']}` |")
            continue
        obs = r["observe_face_out"]
        if isinstance(obs, dict):
            obs_short = obs.get("dominant_emotion", "")
            if not obs_short and obs.get("error"):
                obs_short = f"error: {obs['error']}"
        else:
            obs_short = str(obs)[:50]
        rep = (r["therapist_reply"] or "").replace("|", "\\|").replace("\n", " ")
        if len(rep) > 110:
            rep = rep[:110] + "…"
        md.append(
            f"| `{r['name']}` | {r['img_size']} | "
            f"{obs_short} ({r['observe_face_ms']:.0f} ms) | "
            f"{r['therapist_ms']:.0f} | {rep} |"
        )
    md.append("")
    md.append("## Per-image detail")
    md.append("")
    for r in results:
        md.append(f"### `{r['name']}`")
        md.append("")
        if "error" in r:
            md.append(f"`{r['error']}`")
            md.append("")
            continue
        md.append(f"**observe_face** ({r['observe_face_ms']:.0f} ms):")
        md.append("")
        md.append("```json")
        md.append(json.dumps(r["observe_face_out"], indent=2, ensure_ascii=False))
        md.append("```")
        md.append("")
        md.append(f"**Therapist agent reply** ({r['therapist_ms']:.0f} ms, "
                  f"actions={r['therapist_actions']}):")
        md.append("")
        md.append(f"> {r['therapist_reply']}")
        md.append("")
    md_path.write_text("\n".join(md), encoding="utf-8")

    # PDF
    pdf_path = run_dir / "battery.pdf"
    try:
        _write_battery_pdf(pdf_path, stamp=stamp, results=results)
        print(f"json:     {json_path}")
        print(f"markdown: {md_path}")
        print(f"pdf:      {pdf_path}")
    except Exception as e:
        print(f"json:     {json_path}")
        print(f"markdown: {md_path}")
        print(f"pdf skipped: {e!r}")

    return 0


def _write_battery_pdf(path: Path, *, stamp: str, results: list[dict]) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image, Preformatted,
        Table, TableStyle, PageBreak, KeepTogether,
    )

    NAVY = HexColor("#0B2545")
    ORANGE = HexColor("#F25C05")
    GRAY = HexColor("#444")
    LIGHT = HexColor("#EEF2F7")

    base = getSampleStyleSheet()
    s_cover = ParagraphStyle("VCover", parent=base["Normal"],
                              fontName="Helvetica-Bold", fontSize=22, leading=28,
                              textColor=NAVY, spaceAfter=8)
    s_sub = ParagraphStyle("VSub", parent=base["Normal"],
                            fontName="Helvetica", fontSize=11, leading=15,
                            textColor=GRAY, spaceAfter=4)
    s_h2 = ParagraphStyle("VH2", parent=base["Normal"],
                           fontName="Helvetica-Bold", fontSize=14, leading=18,
                           textColor=ORANGE, spaceBefore=10, spaceAfter=6)
    s_body = ParagraphStyle("VBody", parent=base["Normal"],
                             fontName="Helvetica", fontSize=10, leading=14,
                             textColor=HexColor("#222"), spaceAfter=4)
    s_code = ParagraphStyle("VCode", parent=base["Normal"],
                             fontName="Courier", fontSize=8.5, leading=11,
                             textColor=HexColor("#1a1a1a"), backColor=LIGHT,
                             borderPadding=4, spaceAfter=6)

    def esc(s: str) -> str:
        return (str(s or "")
                .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    story: list = []
    story.append(Paragraph("Vision Robustness Battery", s_cover))
    story.append(Paragraph(
        f"Multiple image inputs through real GPT-4o vision and the "
        f"therapist agent. Transcript: <i>'{TRANSCRIPT}'</i>", s_sub))
    story.append(Paragraph(f"<b>Generated:</b> {stamp}", s_sub))
    story.append(Paragraph(f"<b>Images tested:</b> {len(results)}", s_sub))
    story.append(Spacer(1, 0.18 * inch))

    # Summary table
    story.append(Paragraph("Summary", s_h2))
    rows = [[
        Paragraph("<b>Image</b>", s_body),
        Paragraph("<b>observe_face dominant</b>", s_body),
        Paragraph("<b>obs ms</b>", s_body),
        Paragraph("<b>thera ms</b>", s_body),
        Paragraph("<b>Reply preview</b>", s_body),
    ]]
    for r in results:
        if "error" in r:
            rows.append([
                Paragraph(esc(r["name"]), s_body),
                Paragraph("<font color='#c0392b'>ERROR</font>", s_body),
                Paragraph("-", s_body),
                Paragraph("-", s_body),
                Paragraph(esc(r["error"]), s_body),
            ])
            continue
        obs = r["observe_face_out"]
        if isinstance(obs, dict):
            short = obs.get("dominant_emotion", "")
            if not short:
                short = obs.get("error") or json.dumps(obs)[:40]
        else:
            short = str(obs)[:40]
        reply = (r["therapist_reply"] or "")[:140]
        rows.append([
            Paragraph(esc(r["name"]), s_body),
            Paragraph(esc(short), s_body),
            Paragraph(f"{r['observe_face_ms']:.0f}", s_body),
            Paragraph(f"{r['therapist_ms']:.0f}", s_body),
            Paragraph(esc(reply), s_body),
        ])
    t = Table(rows, colWidths=[1.4*inch, 1.5*inch, 0.55*inch, 0.6*inch, 3.0*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.2 * inch))

    # Per-image detail
    for r in results:
        story.append(PageBreak())
        story.append(Paragraph(esc(r["name"]), s_h2))
        if "error" in r:
            story.append(Paragraph(esc(r["error"]), s_body))
            continue
        # Embed thumbnail
        try:
            img = Image(r["img_path"], width=3.5*inch, height=2.0*inch,
                        kind="proportional")
            story.append(img)
            story.append(Spacer(1, 0.1*inch))
        except Exception as e:
            story.append(Paragraph(f"(image embed failed: {esc(repr(e))})", s_body))
        # observe_face block
        story.append(Paragraph(
            f"<b>observe_face</b> &nbsp;({r['observe_face_ms']:.0f} ms):", s_body))
        if isinstance(r["observe_face_out"], dict):
            story.append(Preformatted(
                json.dumps(r["observe_face_out"], indent=2, ensure_ascii=False),
                s_code,
            ))
        else:
            story.append(Paragraph(esc(r["observe_face_out"]), s_body))
        story.append(Spacer(1, 0.05*inch))
        story.append(Paragraph(
            f"<b>Therapist reply</b> &nbsp;({r['therapist_ms']:.0f} ms, agent="
            f"<font face='Courier'>{esc(r['therapist_agent'])}</font>, "
            f"actions={esc(r['therapist_actions'])}):", s_body))
        story.append(Paragraph(
            "<i>" + esc(r["therapist_reply"] or "(no reply)") + "</i>",
            s_body,
        ))

    doc = SimpleDocTemplate(
        str(path), pagesize=LETTER,
        leftMargin=0.7*inch, rightMargin=0.7*inch,
        topMargin=0.6*inch, bottomMargin=0.6*inch,
        title="NAO Morgan v2 — Vision Battery",
        author="Aayush Shrestha",
    )

    def _on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8); canvas.setFillColor(GRAY)
        canvas.drawString(0.7 * inch, 0.4 * inch,
                          "Nao-OpenAI-Morgan-Assist  ·  vision battery")
        canvas.drawRightString(LETTER[0] - 0.7 * inch, 0.4 * inch,
                                "page {}".format(_doc.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)


if __name__ == "__main__":
    sys.exit(main())
