"""Jinja2 templates (inline so the package ships as one unit). Autoescape is on for all of them."""

BASE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}QSD{% endblock %} · Quant Strategy Discovery</title>{% block head %}{% endblock %}
<style>
:root{--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--line:#c3c2b7;--ring:rgba(11,11,11,.10);--accent:#2a78d6;--good:#0ca30c;--good-text:#006300;--warning:#fab219;
--serious:#ec835a;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root{--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--line:#383835;--ring:rgba(255,255,255,.10);--accent:#3987e5;--good-text:#0ca30c}}
*{box-sizing:border-box}body{margin:0;background:var(--plane);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;gap:20px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--grid);
background:var(--surface);flex-wrap:wrap}header b{font-size:15px}nav a{color:var(--ink2);text-decoration:none;
margin-right:14px}nav a:hover{color:var(--ink)}main{padding:20px;max-width:1400px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:24px 0 8px}.sub{color:var(--ink2);margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:12px}.tile .value{font-size:22px;font-weight:600;margin-top:4px}
.panel{background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:14px 16px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--ring);border-radius:8px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:12px;white-space:nowrap}td.num,th.num{text-align:right;
font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}a{color:var(--accent)}
.badge{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;border-radius:999px;font-size:12px;
border:1px solid var(--ring);white-space:nowrap}.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.good .dot{background:var(--good)}.warning .dot{background:var(--warning)}.serious .dot{background:var(--serious)}
.critical .dot{background:var(--critical)}.muted{color:var(--muted)}.quote{color:var(--ink2);font-style:italic}
.scroll{overflow-x:auto;border-radius:8px}.scroll table{min-width:1100px}td.src{max-width:220px}
.warn-banner{border-left:4px solid var(--warning);padding:10px 14px;background:var(--surface);margin-bottom:14px}
.flag{display:inline-block;margin:2px 4px 2px 0}form.filters{display:flex;gap:10px;margin-bottom:12px;
flex-wrap:wrap;align-items:end}select,input,button{font:inherit;padding:4px 8px;border:1px solid var(--line);
border-radius:6px;background:var(--surface);color:var(--ink)}.grid2{display:grid;grid-template-columns:1fr 1fr;
gap:14px}@media (max-width:900px){.grid2{grid-template-columns:1fr}}
.bar{height:8px;background:var(--grid);border-radius:999px;overflow:hidden;margin-top:8px}
.bar span{display:block;height:100%;background:var(--accent)}.log td{font-variant-numeric:tabular-nums}
.current{font-size:15px;margin:10px 0 14px}
</style></head><body>
<header><b>Quant Strategy Discovery</b><nav><a href="/live">Live</a><a href="/">Overview</a><a href="/ideas">Ideas</a>
<a href="/sources">Sources</a><a href="/ai-cost">AI cost</a><a href="/errors">Errors</a></nav>
<span class="muted" style="margin-left:auto">Read-only · research candidates only · never places trades</span></header>
<main>{% block body %}{% endblock %}</main></body></html>"""

MACROS = """{% macro status(s) -%}{% set k = STATUS_KIND.get(s, ('neutral','•')) %}
<span class="badge {{ k[0] }}"><span class="dot"></span>{{ k[1] }} {{ s|replace('_',' ')|lower }}</span>
{%- endmacro %}
{% macro regimes(idea) -%}{% for r in idea.regimes if r.suitability.value == 'SUITED' %}
<span class="badge" title="basis: {{ r.basis.value }}">{{ REGIME_LABELS[r.regime.value] }}
{%- if r.basis.value == 'RATIONALE_INFERRED' %} <span class="muted">(inferred)</span>{% endif %}</span>
{% else %}<span class="muted">unknown</span>{% endfor %}{%- endmacro %}"""

OVERVIEW = """{% extends "base.html" %}{% import "macros.html" as m %}{% block title %}Overview{% endblock %}
{% block body %}<h1>Overview</h1><p class="sub">As of {{ now.strftime('%Y-%m-%d %H:%M') }} UTC</p>
<div class="tiles">{% for label, value in cards %}<div class="tile"><div class="label">{{ label }}</div>
<div class="value">{{ value|fmt }}</div></div>{% endfor %}</div>
<h2>Strategies by market direction</h2>
<table><tr><th>Market direction</th><th class="num">Ideas suited</th><th></th></tr>
{% for r in regimes %}<tr><td>{{ r.label }}</td><td class="num">{{ r.suited }}</td>
<td><a href="/ideas?regime={{ r.regime }}">view</a></td></tr>{% endfor %}</table>
<h2>Top research priorities</h2>
<table><tr><th>Strategy</th><th>Assets</th><th>Status</th><th class="num">Priority</th><th class="num">Coverage</th></tr>
{% for i in top %}<tr><td><a href="/ideas/{{ i.id }}">{{ i.strategy_name }}</a></td>
<td>{{ i.asset_classes|join(', ') }}</td><td>{{ m.status(i.status.value) }}</td>
<td class="num">{{ i.research_priority_score|fmt(1) }}</td>
<td class="num">{{ ((i.score_details.get('idea_quality', {}).get('coverage') or 0) * 100)|round|int }}%</td></tr>
{% else %}<tr><td colspan="5" class="muted">No promising or in-research ideas yet.</td></tr>{% endfor %}</table>
{% endblock %}"""

IDEAS = """{% extends "base.html" %}{% import "macros.html" as m %}{% block title %}Ideas{% endblock %}
{% block body %}<h1>Strategy ideas</h1><p class="sub">Sorted by research priority. Scores never use claimed returns.</p>
<form class="filters" method="get"><label>Status<br><select name="status"><option value="">all</option>
{% for s in statuses %}<option value="{{ s }}" {{ 'selected' if f.status == s }}>{{ s|replace('_',' ')|lower }}</option>
{% endfor %}</select></label><label>Market direction<br><select name="regime"><option value="">all</option>
{% for r in regimes %}<option value="{{ r }}" {{ 'selected' if f.regime == r }}>{{ REGIME_LABELS[r] }}</option>
{% endfor %}</select></label><label>Asset<br><input name="asset" value="{{ f.asset or '' }}" size="8"
placeholder="e.g. ETF"></label><button>Filter</button></form>
<div class="scroll"><table><tr><th class="num">Quality</th><th>Strategy</th><th>Asset</th><th>Family</th><th>Source</th>
<th class="num">Source Q</th><th class="num">Evidence Q</th><th>Rationale</th><th>Liquidity</th><th>Exit</th>
<th class="num">Complete</th><th>Setup</th><th class="num">Novelty</th><th class="num">Priority</th><th>Market direction</th>
<th>Status</th></tr>
{% for i in ideas %}{% set iq = i.score_details.get('idea_quality', {}) %}
{% set comps = iq.get('components', {}) %}{% set src = sources.get(i.primary_source_id) %}{% set su = i|setup %}
<tr><td class="num">{{ iq.get('normalized')|fmt(0) }}<br><span class="muted">{{ ((iq.get('coverage') or 0)*100)|round|int
}}% assessed</span></td><td><a href="/ideas/{{ i.id }}">{{ i.strategy_name }}</a></td>
<td>{{ i.asset_classes|join(', ') }}</td><td>{{ i.strategy_families|join(', ')|lower }}</td>
<td class="src">{{ src.title if src else '—' }}</td><td class="num">{{ i.source_quality|fmt(0) }}</td>
<td class="num">{{ i.evidence_quality|fmt(0) }}</td>
<td>{{ 'stated' if i.economic_rationale != 'UNKNOWN' else 'unknown' }}</td>
<td>{{ (comps.get('liquidity', {}).get('value'))|fmt(1) }}</td><td>{{ (comps.get('exit_executability', {}).get('value'))|fmt(1) }}</td>
<td class="num">{{ i.formalization_completeness|fmt(0) }}<br><span class="muted">{{ maturity(i.formalization_completeness)|lower }}</span></td><td class="num">{{ su.readiness|fmt(0) }}<br><span class="muted">{{ su.label }}</span></td><td class="num">{{ i.novelty_score|fmt(0) }}</td>
<td class="num">{{ i.research_priority_score|fmt(1) }}</td><td>{{ m.regimes(i) }}</td>
<td>{{ m.status(i.status.value) }}</td></tr>
{% else %}<tr><td colspan="16" class="muted">No ideas match these filters.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

IDEA = """{% extends "base.html" %}{% import "macros.html" as m %}{% block title %}{{ idea.strategy_name }}{% endblock %}
{% block body %}<p><a href="/ideas">← Ideas</a></p><h1>{{ idea.strategy_name }}</h1>
<p class="sub">{{ m.status(idea.status.value) }} · <span class="badge" title="how completely the rules are written down">{{ pkg.maturity|lower }}</span> · {{ pkg.strategy_id }} · {{ idea.asset_classes|join(', ') }} ·
{{ idea.strategy_families|join(', ')|lower }} · position {{ idea.position_direction.value|lower }} ·
horizon {{ idea.time_horizon.value|lower }}</p>
<div class="warn-banner">⚠ {{ pkg.warning }}</div>
<div class="grid2"><div>
<div class="panel"><h2 style="margin-top:0">Summary</h2><p>{{ idea.summary }}</p>
<p><b>Next research action:</b> {{ idea.next_research_action }}</p></div>
{% set su = idea|setup %}
<div class="panel"><h2 style="margin-top:0">Backtest setup</h2>
<p>{% if su.verdict == 'complete' %}<span class="badge good"><span class="dot"></span>runnable as the source states it</span>
{% elif su.verdict == 'runnable' %}<span class="badge good"><span class="dot"></span>runnable</span>
{% elif su.verdict == 'blocked' %}<span class="badge serious flag"><span class="dot"></span>not runnable yet</span>
{% else %}<span class="badge serious flag"><span class="dot"></span>skipped: source says too little</span>{% endif %}
<span class="muted">{{ su.label }}</span></p>
<p class="muted" title="how much of a runnable setup the source itself supplied">Readiness
{{ su.readiness|fmt(0) }} · written down by the source: {{ su.from_source|join(', ') or 'nothing' }}</p>
{% if su.blocking_missing %}<p class="muted">Must come from the source (never invented):
{{ su.blocking_missing|join(', ') }}</p>{% endif %}
{% if su.bridgeable_missing %}<p class="muted">Can be filled from convention, and recorded as such:
{{ su.bridgeable_missing|join(', ') }}</p>{% endif %}
{% if su.skip and su.skip.reason %}<p class="muted">Skip check: {{ su.skip.reason }} — {{ su.skip.detail }}</p>{% endif %}
</div>
<div class="panel"><h2 style="margin-top:0">Red flags</h2>
{% set iq = pkg.scores %}
{% for f in pkg.concerns.red_flags %}<span class="badge serious flag"><span class="dot"></span>! {{ f }}</span>{% endfor %}
{% for f in idea.hard_fail_reasons %}<span class="badge critical flag"><span class="dot"></span>✕ hard fail: {{ f }}</span>{% endfor %}
{% if not pkg.concerns.red_flags and not idea.hard_fail_reasons %}<span class="muted">None detected.</span>{% endif %}
<p class="muted">Not assessed (unscored, not guessed): {{ iq.unscored_components|join(', ') or 'none' }}</p>
<p class="muted">Missing rules: {{ pkg.unknown_rules|join(', ') or 'none' }}</p></div>
{% set g = pkg.grounding %}{% if g %}<div class="panel"><h2 style="margin-top:0">Removed by fact-check</h2>
<p class="muted">{{ g.removed|length }} of {{ g.values_offered }} values the AI offered were removed because the quote
or a number was not found in the source ({{ g.model }}, prompt {{ g.prompt_version }}). They are not facts; check them
against the source if they look right.{% if g.realigned %} Quotes matched after small wording fixes:
{{ g.realigned|join(', ') }}.{% endif %}</p>
{% for r in g.removed %}<p><b>{{ r.field }}</b>: {{ r.value }} <span class="badge neutral"><span class="dot"></span>
{{ r.reason|lower|replace('_',' ') }}</span>{% if r.quote %}<br><span class="quote">AI quote: “{{ r.quote }}”</span>
{% endif %}{% if r.location %} <span class="muted">{{ r.location }}</span>{% endif %}</p>
{% else %}<p class="muted">Nothing was removed.</p>{% endfor %}</div>{% endif %}
<div class="panel"><h2 style="margin-top:0">Source &amp; provenance</h2>{% set ps = pkg.provenance.primary_source %}
{% if ps %}<p><b>{{ ps.title }}</b><br>{{ ps.author }} · {{ ps.publication_date }} · tier {{ ps.tier or 'unrated' }}
· access {{ ps.access_status|lower }}<br>{% if ps.doi %}DOI {{ ps.doi }} · {% endif %}
{% if ps.canonical_url|safe_url %}<a href="{{ ps.canonical_url|safe_url }}" rel="noopener noreferrer nofollow"
target="_blank">open source</a>{% elif ps.local_path %}{{ ps.local_path }}{% endif %}</p>
<p class="muted">Root evidence: {{ pkg.provenance.root_evidence_id }} · search depth level
{{ pkg.provenance.search_depth_level }}</p>{% else %}<p class="muted">No primary source recorded.</p>{% endif %}
<p>Supporting sources: {{ pkg.supporting_sources|length }} · contradicting: {{ pkg.contradicting_sources|length }}</p></div>
<div class="panel"><h2 style="margin-top:0">Source claims <span class="badge warning"><span class="dot"></span>!
not validated</span></h2>{% for k, c in pkg.source_claims.claims.items() %}<p>{{ k|replace('claimed_','')|replace('_',' ') }}:
<b>{{ c.value }}</b>{% if c.quote %} <span class="quote">“{{ c.quote }}”</span>{% endif %}
{% if c.page %}<span class="muted">p.{{ c.page }}</span>{% endif %}</p>{% else %}<p class="muted">No performance claims.</p>
{% endfor %}</div>
</div><div>
<div class="panel"><h2 style="margin-top:0">Market direction</h2><table>
<tr><th>Direction</th><th>Suitability</th><th>Basis</th><th class="num">Confidence</th></tr>
{% for r in pkg.market_regimes %}<tr><td>{{ REGIME_LABELS[r.regime] }}</td><td>{{ r.suitability|lower }}</td>
<td>{{ r.basis|replace('_',' ')|lower }}</td><td class="num">{{ r.confidence|fmt(2) }}</td></tr>{% endfor %}</table>
{% for r in pkg.market_regimes if r.evidence.quote %}<p class="quote">{{ REGIME_LABELS[r.regime] }}: “{{ r.evidence.quote }}”
{% if r.evidence.page %}<span class="muted">p.{{ r.evidence.page }}</span>{% endif %}</p>{% endfor %}</div>
<div class="panel"><h2 style="margin-top:0">Research completeness · {{ pkg.research_completeness.percent }}%</h2>
<table>{% for k, v in pkg.research_completeness.checks.items() %}<tr><td>{{ k|replace('_',' ') }}</td>
<td>{% if v == 'PASS' %}<span class="badge good"><span class="dot"></span>✓ pass</span>{% else %}
<span class="badge critical"><span class="dot"></span>✕ fail</span>{% endif %}</td></tr>{% endfor %}</table>
<p>Backtest handoff: {% if pkg.handoff.eligible %}<span class="badge good"><span class="dot"></span>✓ eligible</span>
{% else %}<span class="badge neutral"><span class="dot"></span>– not yet</span>
<br><span class="muted">{{ pkg.handoff.blocking_reasons|join('; ') }}</span>{% endif %}</p></div>
<div class="panel"><h2 style="margin-top:0">Scores</h2><table>
{% for k in ['idea_quality_normalized','coverage','source_quality','evidence_quality','formalization_completeness',
'parameter_complexity','replication','novelty','research_priority'] %}<tr><td>{{ k|replace('_',' ') }}</td>
<td class="num">{{ pkg.scores[k]|fmt(2) }}</td></tr>{% endfor %}</table></div>
</div></div>
<h2>Known rules</h2><table><tr><th>Rule</th><th>Value</th><th>Evidence</th><th>Location</th><th class="num">Conf.</th></tr>
{% for k, r in pkg.known_rules.items() %}<tr><td>{{ k|replace('_',' ') }}</td><td>{{ r.value }}</td>
<td class="quote">{{ ('“' ~ r.quote ~ '”') if r.quote else '' }}</td>
<td>{{ ('p.' ~ r.page) if r.page else (('slide ' ~ r.slide) if r.slide else (r.location or '')) }}</td>
<td class="num">{{ r.confidence|fmt(2) }}</td></tr>{% else %}<tr><td colspan="5" class="muted">No rules known.</td></tr>
{% endfor %}</table>
<h2>Downstream checks for the backtester</h2><p>{{ (pkg.point_in_time_requirements + pkg.downstream_checks)|join(' · ') }}</p>
<h2>Status history</h2><table><tr><th>When (UTC)</th><th>From</th><th>To</th><th>Reason</th></tr>
{% for h in history %}<tr><td>{{ h.changed_at.strftime('%Y-%m-%d %H:%M') }}</td>
<td>{{ h.from_status.value|lower if h.from_status else '—' }}</td><td>{{ h.to_status.value|lower }}</td>
<td>{{ h.reason }}</td></tr>{% endfor %}</table>{% endblock %}"""

SOURCES = """{% extends "base.html" %}{% block title %}Sources{% endblock %}{% block body %}<h1>Sources</h1>
<p class="sub">Tier = spec §24 hierarchy (1 primary … 4 community; unrated = not on any list).</p>
<form class="filters" method="get"><label>Tier<br><select name="tier"><option value="">all</option>
{% for t in [1,2,3,4,5] %}<option value="{{ t }}" {{ 'selected' if f.tier == t }}>{{ t }}</option>{% endfor %}
</select></label><label>Access<br><input name="access" value="{{ f.access or '' }}" size="14"
placeholder="e.g. PAYWALLED"></label><button>Filter</button></form>
<table><tr><th>Title</th><th>Author</th><th>Date</th><th class="num">Tier</th><th class="num">Quality</th>
<th class="num">Evidence</th><th>Access</th><th>Format</th><th>Abstract</th><th>Link</th></tr>
{% for s in sources %}<tr><td>{{ s.title }}</td><td>{{ s.author|truncate(40) }}</td><td>{{ s.publication_date }}</td>
<td class="num">{{ s.tier or '—' }}</td><td class="num">{{ s.source_quality_score|fmt(0) }}</td>
<td class="num">{{ s.evidence_quality_score|fmt(0) }}</td><td>{{ s.access_status.value|replace('_',' ')|lower }}</td>
<td>{{ s.format|lower }}</td><td>{{ 'yes' if s.id in abstracts else '—' }}</td>
<td>{% if (s.canonical_url or s.url)|safe_url %}<a href="{{ (s.canonical_url or s.url)|safe_url }}"
rel="noopener noreferrer nofollow" target="_blank">open</a>{% else %}{{ s.local_path or '—' }}{% endif %}</td></tr>
{% else %}<tr><td colspan="10" class="muted">No sources yet.</td></tr>{% endfor %}</table>{% endblock %}"""

AI_COST = """{% extends "base.html" %}{% block title %}AI cost{% endblock %}{% block body %}<h1>AI cost</h1>
<p class="sub">This month. Caps: {{ budgets.max_ai_cost_usd_per_day|money }}/day, {{ budgets.max_ai_cost_usd_per_month|money
}}/month, {{ budgets.max_ai_cost_usd_per_campaign|money }}/campaign.</p>
<div class="tiles"><div class="tile"><div class="label">Calls today</div><div class="value">{{ calls_today|fmt }}</div></div>
<div class="tile"><div class="label">Spend this month</div><div class="value">{{ month_cost|money }}</div></div>
<div class="tile"><div class="label">Month projection (linear)</div><div class="value">{{ projection|money }}</div></div>
<div class="tile"><div class="label">Cache hit rate</div><div class="value">{{ ((hit_rate or 0)*100)|round|int if hit_rate
is not none else '—' }}{{ '%' if hit_rate is not none }}</div></div>
<div class="tile"><div class="label">Cost per promising idea</div><div class="value">{{ per_promising|money }}</div></div></div>
<h2>By provider, model and task</h2><table><tr><th>Provider</th><th>Model</th><th>Task</th><th class="num">Calls</th>
<th class="num">Input tokens</th><th class="num">Output tokens</th><th class="num">Cost</th><th class="num">Cache hits</th></tr>
{% for r in rows %}<tr><td>{{ r[0] }}</td><td>{{ r[1] }}</td><td>{{ r[2] }}</td><td class="num">{{ r[3]|fmt }}</td>
<td class="num">{{ (r[4] or 0)|fmt }}</td><td class="num">{{ (r[5] or 0)|fmt }}</td><td class="num">{{ (r[6] or 0)|money }}</td>
<td class="num">{{ (r[7] or 0)|fmt }}</td></tr>{% else %}<tr><td colspan="8" class="muted">No AI calls this month.</td></tr>
{% endfor %}</table>{% endblock %}"""

ERRORS = """{% extends "base.html" %}{% block title %}Errors{% endblock %}{% block body %}<h1>Errors</h1>
<p class="sub">{{ 'All errors' if show_all else 'Unresolved errors' }} — nothing fails silently (spec §133).
<a href="/errors?all={{ 'false' if show_all else 'true' }}">{{ 'show unresolved only' if show_all else 'show all' }}</a></p>
<table><tr><th>When (UTC)</th><th>Stage</th><th>Handler</th><th>Source</th><th>Error</th><th class="num">Retries</th>
<th>State</th></tr>{% for e in errors %}<tr><td>{{ e.occurred_at.strftime('%Y-%m-%d %H:%M') }}</td><td>{{ e.stage }}</td>
<td>{{ e.handler or '—' }}</td><td>{{ e.source_ref|truncate(60) if e.source_ref else '—' }}</td><td>{{ e.error|truncate(200) }}</td>
<td class="num">{{ e.retry_count }}</td><td>{{ e.state.value|lower }}</td></tr>
{% else %}<tr><td colspan="7" class="muted">No errors.</td></tr>{% endfor %}</table>{% endblock %}"""

LIVE = """{% extends "base.html" %}{% import "macros.html" as m %}{% block title %}Live{% endblock %}
{% block head %}{% if running %}<meta http-equiv="refresh" content="3">{% endif %}{% endblock %}
{% block body %}{% if not c %}<h1>Live research monitor</h1><p class="sub">No research has been started yet. Double-click
research.bat (or run <b>qsd research</b>) and describe what to look for.</p>{% else %}
<h1>Research {{ c.id }}: {{ c.request_text }}</h1>
<p class="sub">{{ m.status(c.status.value) }} {% if running %}· updates every 3 seconds{% if stale %} ·
<b>no update for {{ stale }} min</b> (the research window may have been closed){% endif %}{% else %}·
{{ c.stop_reason or '' }}{% endif %}</p>
<p class="current"><b>{{ (p.phase or 'starting')|capitalize }}:</b> {{ p.current or 'starting…' }}</p>
<div class="tiles">
<div class="tile"><div class="label">Searches</div><div class="value">{{ p.searches_done or 0 }} / {{ p.searches_total or 0 }}</div></div>
<div class="tile"><div class="label">Papers found</div><div class="value">{{ sources_found|fmt }}</div></div>
<div class="tile"><div class="label">Papers read (this round)</div><div class="value">{{ p.papers_done or 0 }} / {{ p.papers_total or 0 }}</div>
<div class="bar"><span style="width:{{ pct }}%"></span></div></div>
<div class="tile"><div class="label">Strategies found</div><div class="value">{{ ideas|length }}</div></div>
<div class="tile"><div class="label">Promising</div><div class="value">{{ promising }}</div></div>
<div class="tile"><div class="label">AI cost (cap {{ cap|money }})</div><div class="value">{{ (p.ai_cost_usd or 0)|money }}</div></div>
</div>
<div class="grid2"><div><h2>Strategies found</h2>
<table><tr><th>Strategy</th><th>Status</th><th class="num">Quality</th><th>Market direction</th></tr>
{% for i in ideas %}<tr><td><a href="/ideas/{{ i.id }}">{{ i.strategy_name }}</a></td><td>{{ m.status(i.status.value) }}</td>
<td class="num">{{ i.idea_quality_score|fmt(1) }}</td><td>{{ m.regimes(i) }}</td></tr>
{% else %}<tr><td colspan="4" class="muted">None yet. Strategies appear here as papers are read.</td></tr>{% endfor %}</table></div>
<div><h2>Activity</h2><table class="log"><tr><th>Time</th><th>Step</th><th>What happened</th></tr>
{% for e in events %}<tr><td class="muted">{{ e.t }}</td><td>{{ e.phase }}</td><td>{{ e.msg }}</td></tr>
{% else %}<tr><td colspan="3" class="muted">No activity yet.</td></tr>{% endfor %}</table></div></div>
{% endif %}{% if others %}<h2>Other research</h2><table><tr><th>#</th><th>Request</th><th>Status</th></tr>
{% for o in others %}<tr><td><a href="/live?id={{ o.id }}">{{ o.id }}</a></td><td>{{ o.request_text }}</td>
<td>{{ m.status(o.status.value) }}</td></tr>{% endfor %}</table>{% endif %}{% endblock %}"""

TEMPLATES = {"live.html": LIVE, "base.html": BASE, "macros.html": MACROS, "overview.html": OVERVIEW, "ideas.html": IDEAS,
             "idea.html": IDEA, "sources.html": SOURCES, "ai_cost.html": AI_COST, "errors.html": ERRORS}
