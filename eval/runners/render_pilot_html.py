#!/usr/bin/env python3
"""Render a browseable HTML view of a pilot benchmark run.

Usage:
    python eval/runners/render_pilot_html.py eval/results/pilot_runtime_benchmark_claude_full.json
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Pilot Eval Results — {tag}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0b0c;color:#e8e8ea}}
  header{{padding:20px 28px;background:#111113;border-bottom:1px solid #222225;position:sticky;top:0;z-index:10}}
  h1{{margin:0 0 8px;font-size:18px}}
  .stats{{color:#9b9ba3;font-size:13px}}
  .stats b{{color:#e8e8ea}}
  .filters{{padding:14px 28px;background:#111113;border-bottom:1px solid #222225;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
  .filters input{{flex:1;min-width:240px;padding:8px 12px;border:1px solid #2b2b2f;background:#0b0b0c;color:#e8e8ea;border-radius:8px;font:inherit}}
  button{{padding:6px 12px;border:1px solid #2b2b2f;background:#1a1a1c;color:#e8e8ea;border-radius:8px;font:inherit;cursor:pointer}}
  button:hover{{background:#24242a}}
  button.on{{background:#2563eb;border-color:#2563eb}}
  main{{padding:20px 28px;max-width:1200px;margin:0 auto}}
  .case{{padding:14px;border:1px solid #222225;border-radius:10px;margin-bottom:10px;background:#111113}}
  .case.pass{{border-left:4px solid #1fbf5f}}
  .case.fail{{border-left:4px solid #d33}}
  .case-head{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px;cursor:pointer;user-select:none}}
  .badge{{font-size:11px;padding:2px 8px;border-radius:999px;background:#1e1e22;color:#b7b7c1;text-transform:uppercase;letter-spacing:.5px}}
  .badge.pass{{background:#163b28;color:#5ce18a}}
  .badge.fail{{background:#3a1a1e;color:#ff8089}}
  .badge.provider{{background:#1b2b3f;color:#7db4ff}}
  .badge.category{{background:#2a1e3a;color:#c79cff}}
  .badge.latency{{background:#1e1e22;color:#9b9ba3}}
  .badge.cost{{background:#1e1e22;color:#9b9ba3}}
  .id{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#9b9ba3;font-size:12px}}
  .q{{margin:4px 0 10px;color:#e8e8ea;line-height:1.45}}
  details{{margin-top:6px}}
  summary{{cursor:pointer;color:#9b9ba3;font-size:13px;padding:4px 0}}
  summary:hover{{color:#e8e8ea}}
  .answer,.reason{{white-space:pre-wrap;background:#0b0b0c;border:1px solid #222225;padding:12px;border-radius:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;line-height:1.5;color:#d0d0d6;margin-top:6px;max-height:500px;overflow:auto}}
  .reason{{color:#ffb0b8}}
  .sources{{font-size:12px;color:#9b9ba3;margin-top:6px}}
  .sources code{{background:#1e1e22;padding:2px 6px;border-radius:4px;font-size:11.5px}}
</style>
</head>
<body>
<header>
  <h1>Pilot Eval — {tag}</h1>
  <div class="stats">
    <b>{pass_rate}</b> pass rate ({passed}/{total}) ·
    <b>${total_cost}</b> total cost ·
    <b>{median_latency}ms</b> median non-FAQ latency ·
    <b>{p95_latency}ms</b> P95 latency ·
    part-number hallucinations <b>{hallu}</b> ·
    API errors <b>{api_errors}</b>
  </div>
</header>
<div class="filters">
  <input id="q" placeholder="Search question, answer, SKU, reason...">
  <button data-f="all" class="on">All ({total})</button>
  {category_buttons}
  <button data-f="fail">Fail only ({failed})</button>
</div>
<main id="list">
{cases}
</main>
<script>
  const cards = [...document.querySelectorAll('.case')];
  const buttons = [...document.querySelectorAll('button[data-f]')];
  const q = document.getElementById('q');
  let filter = 'all';
  function apply() {{
    const qv = q.value.trim().toLowerCase();
    cards.forEach(c => {{
      const hide = (filter === 'fail' ? !c.classList.contains('fail') : filter !== 'all' && c.dataset.cat !== filter)
        || (qv && !c.textContent.toLowerCase().includes(qv));
      c.style.display = hide ? 'none' : '';
    }});
  }}
  buttons.forEach(b => b.addEventListener('click', () => {{
    buttons.forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    filter = b.dataset.f;
    apply();
  }}));
  q.addEventListener('input', apply);
  document.querySelectorAll('.case-head').forEach(h => h.addEventListener('click', e => {{
    if (e.target.tagName === 'A') return;
    const det = h.parentElement.querySelector('details');
    if (det) det.open = !det.open;
  }}));
</script>
</body>
</html>"""


def fmt_cost(x: float) -> str:
    return f"{x:.4f}"


def render(pilot_json: Path, out_path: Path) -> Path:
    data = json.loads(pilot_json.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    det = data.get("deterministic", {})
    runtime = data.get("runtime", {})

    categories = sorted({c.get("category", "unknown") for c in cases})
    cat_counts = {}
    for c in cases:
        cat = c.get("category", "unknown")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    category_buttons = " ".join(
        f'<button data-f="{html.escape(cat)}">{html.escape(cat)} ({cat_counts[cat]})</button>'
        for cat in categories
    )

    total = len(cases)
    passed = sum(1 for c in cases if c.get("passed"))
    failed = total - passed
    pass_rate = f"{(passed / total * 100 if total else 0):.1f}%"

    case_html_parts = []
    for c in cases:
        passed_flag = c.get("passed")
        cls = "pass" if passed_flag else "fail"
        tid = html.escape(c.get("test_id", ""))
        cat = html.escape(c.get("category", "unknown"))
        provider = html.escape(c.get("provider_used") or "n/a")
        question = html.escape(c.get("question", ""))
        answer = html.escape(c.get("answer", "") or "(no answer)")
        reason = html.escape(c.get("failure_reason", "") or "")
        latency = c.get("latency_ms", 0)
        cost = c.get("estimated_cost_usd", 0.0)
        sources = c.get("sources", []) or []
        src_html = ""
        if sources:
            src_html = "<div class=\"sources\">Sources: " + ", ".join(
                f"<code>{html.escape(str(s.get('source') or ''))}" +
                (f":p{s['page']}" if s.get('page') else "") +
                "</code>"
                for s in sources
            ) + "</div>"

        pass_badge = '<span class="badge pass">PASS</span>' if passed_flag else '<span class="badge fail">FAIL</span>'
        reason_html = f'<details><summary>Why failed</summary><div class="reason">{reason}</div></details>' if reason else ""

        case_html_parts.append(f"""
<article class="case {cls}" data-cat="{cat}">
  <div class="case-head">
    {pass_badge}
    <span class="badge category">{cat}</span>
    <span class="badge provider">{provider}</span>
    <span class="badge latency">{latency} ms</span>
    <span class="badge cost">${fmt_cost(cost)}</span>
    <span class="id">{tid}</span>
  </div>
  <div class="q">{question}</div>
  <details>
    <summary>Answer ({len(c.get('answer','') or '')} chars)</summary>
    <div class="answer">{answer}</div>
  </details>
  {reason_html}
  {src_html}
</article>
""")

    out_path.write_text(
        TEMPLATE.format(
            tag=html.escape(pilot_json.stem.replace("pilot_runtime_benchmark_", "")),
            total=total,
            passed=passed,
            failed=failed,
            pass_rate=pass_rate,
            total_cost=f"{runtime.get('total_estimated_cost_usd', 0.0):.4f}",
            median_latency=int(runtime.get("non_faq_median_latency_ms") or 0),
            p95_latency=int(runtime.get("p95_latency_ms") or 0),
            hallu=f"{det.get('part_number_hallucination_rate', 0.0) * 100:.2f}%",
            api_errors=det.get("api_errors_excluded", 0),
            category_buttons=category_buttons,
            cases="\n".join(case_html_parts),
        ),
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path, help="Pilot benchmark JSON file")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path")
    args = parser.parse_args()

    src = args.json_path.resolve()
    out = args.out or src.with_suffix(".html")
    render(src, out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
