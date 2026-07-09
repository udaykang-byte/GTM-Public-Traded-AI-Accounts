"""AIPT pipeline CLI. Run via: uv run python -m pipeline <command>"""
from __future__ import annotations

import csv as csv_mod
import json
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="AI-readiness pipeline for public micro-caps (martechs.io)", no_args_is_help=True)
console = Console()

STATUS_ORDER = ["new", "enriched", "scored", "qualified", "disqualified", "contacts_found"]


@app.callback()
def main(
    profile: str = typer.Option(
        None, "--profile", envvar="AIPT_PROFILE",
        help="Profile pack under profiles/<name>/ (default: built-in config/ pack)",
    ),
):
    """AI-readiness pipeline for public micro-caps."""
    from pipeline import config

    config.activate_profile(profile)


def _validate_profile_settings(settings: dict) -> tuple[list[str], list[str]]:
    """Lightweight structural checks (settings has no schema) — returns
    (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    sectors = settings.get("universe", {}).get("sectors", {})
    if not sectors:
        errors.append("universe.sectors is empty — need at least one sector")
    scoring_cfg = settings.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    if not weights:
        errors.append("scoring.weights is empty")
    for k, v in weights.items():
        if not isinstance(v, (int, float)):
            errors.append(f"scoring.weights.{k} is not numeric: {v!r}")
    caps = scoring_cfg.get("component_caps", {})
    required_caps = {"intent", "capability_gap", "timing", "commercial_fit"}
    missing = required_caps - caps.keys()
    if missing:
        errors.append(f"scoring.component_caps missing keys: {sorted(missing)}")
    for k, v in caps.items():
        if not isinstance(v, (int, float)):
            errors.append(f"scoring.component_caps.{k} is not numeric: {v!r}")
    if not missing and all(isinstance(v, (int, float)) for v in caps.values()):
        total = sum(caps.values())
        if total != 100:
            warnings.append(f"scoring.component_caps sum to {total}, not 100 — totals won't be percentage-like")
    for k in ("qualify_threshold", "disqualify_below"):
        if not isinstance(scoring_cfg.get(k), (int, float)):
            errors.append(f"scoring.{k} must be numeric")
    return errors, warnings


@app.command()
def profile(
    list_: bool = typer.Option(False, "--list", help="List available profile packs"),
    show: bool = typer.Option(False, "--show", help="Show the active pack's resolved settings"),
    validate: bool = typer.Option(False, "--validate", help="Validate the active pack's settings.yaml"),
):
    """Inspect or validate profile packs (ICP config directory overlays).
    Not to be confused with a company's AI-adoption profile (models.Profile)."""
    from pipeline import config

    if list_:
        active = "default" if config.PROFILE_DIR == config.DEFAULT_PROFILE_DIR else config.PROFILE_DIR.name
        for name in config.list_profiles():
            marker = " (active)" if name == active else ""
            console.print(f"{name}{marker}")
        return

    if validate:
        errors, warnings = _validate_profile_settings(config.SETTINGS)
        for w in warnings:
            console.print(f"[yellow]warning:[/yellow] {w}")
        if errors:
            for e in errors:
                console.print(f"[red]error:[/red] {e}")
            raise typer.Exit(1)
        console.print("Profile settings valid ✔")
        return

    if show:
        console.print_json(data=config.SETTINGS)
        return

    console.print("Use --list, --show, or --validate")


@app.command()
def status(
    brief: bool = typer.Option(False, "--brief", help="One-line funnel summary"),
    analytics: bool = typer.Option(
        False, "--analytics",
        help="Also render outcome analytics: funnel/attribution rates, time-in-stage (v3 phase 4)",
    ),
):
    """Funnel counts, recent qualifications, recent runs."""
    from pipeline import db

    try:
        counts = db.status_counts()
    except Exception as exc:
        if "PGRST205" in str(exc):
            print(
                "AIPT: Supabase schema not applied yet — run "
                "`uv run python -m pipeline apply-schema` (needs SUPABASE_DB_URL) "
                "or paste sql/schema.sql into the Supabase SQL editor."
            )
            raise typer.Exit(1)
        raise
    if brief:
        line = " | ".join(f"{counts.get(s, 0)} {s}" for s in STATUS_ORDER)
        print(f"AIPT funnel: {line}")
        return

    table = Table(title="AIPT funnel")
    table.add_column("status")
    table.add_column("companies", justify="right")
    for s in STATUS_ORDER:
        table.add_row(s, str(counts.get(s, 0)))
    console.print(table)

    try:
        tiers = db.tier_counts()
    except Exception as exc:
        if "does not exist" in str(exc) or "PGRST205" in str(exc):
            console.print(
                "[dim]Tier breakdown needs the v3 schema (companies.tier) — "
                "run `apply-schema` to see it.[/dim]"
            )
            tiers = None
        else:
            raise
    if tiers is not None:
        tt = Table(title="Tier breakdown (v3; NULL/unscored counts as T3)")
        tt.add_column("tier")
        tt.add_column("companies", justify="right")
        for t in ("T1", "T2", "T3", "T4"):
            tt.add_row(t, str(tiers.get(t, 0)))
        console.print(tt)

    qualified = db.recent_qualified()
    if qualified:
        qt = Table(title="Recently qualified")
        for col in ("ticker", "name", "sector", "profile", "total", "lead service"):
            qt.add_column(col)
        for q in qualified:
            fits = q.get("service_fit") or []
            lead = fits[0]["service"] if fits else "—"
            qt.add_row(q["ticker"], q["name"][:36], q["sector_bucket"], q.get("profile") or "—",
                       str(q.get("total") or "—"), lead)
        console.print(qt)

    runs = db.recent_runs()
    if runs:
        rt = Table(title="Recent runs")
        for col in ("stage", "started", "stats"):
            rt.add_column(col)
        for r in runs:
            rt.add_row(r["stage"], str(r["started_at"])[:19], json.dumps(r.get("stats") or {})[:60])
        console.print(rt)

    msgs = db.all_messages()
    if msgs:
        cf = db.get_companies(status="contacts_found")
        covered = sum(1 for c in cf if int(c["cik"]) in msgs)
        n_seq = sum(len(v) for v in msgs.values())
        console.print(f"sequences drafted: {n_seq} ({covered} of {len(cf)} contacts_found companies covered)")

    if analytics:
        from pipeline import analytics as analytics_mod

        analytics_mod.render(console)


@app.command()
def ingest(
    tickers: str = typer.Argument("", help="Comma-separated tickers, e.g. 'ABCD,EFGH'"),
    csv: Path = typer.Option(None, "--csv", help="CSV file with a 'ticker' column"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(
        False, "--force",
        help="Bypass the L1 prescreen (customer/competitor exclusions, cap band, exchange/OTC, shell-name heuristics)",
    ),
):
    """Add user-provided companies to the pipeline. User lists override the
    sector screen, but the L1 prescreen still applies: hard-disqualifiers
    (excluded tickers/SIC, cap band, exchange/OTC, shell names) write the row
    as 'disqualified' + dq_reason — never enriched. --force bypasses it."""
    from pipeline import db, prescreen, universe
    from pipeline.config import SETTINGS
    from pipeline.models import Status

    wanted: list[str] = [t for t in tickers.split(",") if t.strip()]
    if csv:
        with open(csv, newline="") as fh:
            for row in csv_mod.DictReader(fh):
                if row.get("ticker"):
                    wanted.append(row["ticker"])
    if not wanted:
        raise typer.BadParameter("Provide tickers or --csv")

    console.print(f"Resolving {len(wanted)} tickers against SEC records…")
    resolved, unresolved = universe.resolve_tickers(wanted)

    table = Table(title="Resolved companies")
    for col in ("ticker", "name", "sector", "market cap", "exchange", "note"):
        table.add_column(col)
    uni = SETTINGS.get("universe", {})
    cap_min, cap_max = uni.get("market_cap_min", 0), uni.get("market_cap_max", 0)
    # dry-run stays DB-free by design, so it can't dedupe against known rows
    known: set[int] = set() if dry_run else db.existing_ciks()
    dq_reasons: dict[str, str] = {}
    for c in resolved:
        already = c.cik in known
        # nothing is written for already-known tickers, so no DQ check/note
        reason = None if (force or already) else prescreen.check(c.model_dump(), SETTINGS)
        notes = []
        if already:
            notes.append("already in pipeline, untouched")
        if c.sector_bucket == "other":
            notes.append("outside target sectors")
        elif c.market_cap and not (cap_min <= c.market_cap <= cap_max) and reason != "outside_cap_band":
            notes.append("outside cap band")
        if reason:
            dq_reasons[c.ticker] = reason
            notes.append(f"[red]DQ: {reason}[/red]")
        table.add_row(
            c.ticker, c.name[:36], c.sector_bucket,
            f"${(c.market_cap or 0)/1e6:.0f}M" if c.market_cap else "?",
            c.exchange or "?", "; ".join(notes),
        )
    console.print(table)
    if unresolved:
        console.print(f"[yellow]Unresolved: {', '.join(unresolved)}[/yellow]")
    if dq_reasons:  # empty whenever --force is set, since reason is skipped above
        console.print(
            f"[red]prescreen disqualified {len(dq_reasons)} ticker(s): "
            + ", ".join(f"{t} ({r})" for t, r in dq_reasons.items())
            + " — rerun with --force to ingest anyway[/red]"
        )

    if dry_run:
        console.print("[dim]dry run — nothing written[/dim]")
        return
    fresh = [c for c in resolved if c.cik not in known]
    skipped = len(resolved) - len(fresh)
    n_dq = 0
    for c in fresh:
        if c.ticker in dq_reasons:
            c.status = Status.disqualified
            c.dq_reason = dq_reasons[c.ticker]
            c.tier = "T4"
            n_dq += 1
    n = db.upsert_companies(fresh)
    msg = f"Ingested {n} companies"
    if n_dq:
        msg += f" ({n_dq} pre-screen disqualified — never enriched, {n - n_dq} active)"
    if skipped:
        msg += f" ({skipped} already in pipeline, untouched)"
    console.print(msg)


@app.command()
def discover(
    limit: int = typer.Option(None, "--limit", help="Max companies to seed"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Screen the SEC universe: sectors + micro-cap band -> seed as 'new'."""
    from pipeline import db, universe

    skip: set[int] = set()
    if not dry_run:
        skip = db.existing_ciks()
    else:
        try:
            skip = db.existing_ciks()
        except (SystemExit, Exception):
            console.print("[yellow]DB not ready — screening without dedupe[/yellow]")

    def progress(stage: str, done: int, total: int):
        console.print(f"[dim]{stage}: {done}/{total}[/dim]")

    run_id = None if dry_run else db.start_run("discover")
    companies, stats = universe.screen(limit=limit, skip_ciks=skip, progress=progress)

    table = Table(title="Discovery funnel")
    table.add_column("stage")
    table.add_column("count", justify="right")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)

    sample = Table(title=f"{'Would seed' if dry_run else 'Seeding'} (sample)")
    for col in ("ticker", "name", "sector", "market cap"):
        sample.add_column(col)
    for c in companies[:15]:
        sample.add_row(c.ticker, c.name[:40], c.sector_bucket, f"${(c.market_cap or 0)/1e6:.0f}M")
    console.print(sample)

    if dry_run:
        console.print(f"[dim]dry run — {len(companies)} companies would be seeded[/dim]")
        return
    n = db.upsert_companies(companies)
    db.finish_run(run_id, {**stats, "seeded": n})
    console.print(f"Seeded {n} companies as 'new'")


def _print_signals(ticker: str, signals: list, errors: list[str]):
    table = Table(title=f"{ticker} signals")
    for col in ("type", "w", "title", "evidence"):
        table.add_column(col)
    for s in sorted(signals, key=lambda x: -x.weight):
        ev = (s.evidence_quote or s.detail or "")[:70]
        table.add_row(s.type, str(s.weight), s.title[:55], ev)
    console.print(table)
    for e in errors:
        console.print(f"[yellow]  {ticker}: {e}[/yellow]")


def _enrich_deep(limit: int | None, ticker: str | None, dry_run: bool):
    """Deep tier: score-gated, cost-capped. Deep Parallel task (paid) + EDGAR
    funding scan (free) for companies at/near the qualify bar."""
    from pipeline import angles as angles_mod
    from pipeline import db, funding_events
    from pipeline.config import SETTINGS

    if ticker:
        row = db.get_company_by_ticker(ticker)
        if not row:
            raise typer.BadParameter(f"{ticker} not in pipeline — deep tier needs a scored company")
        targets = [row]
    else:
        pool: list[dict] = []
        for st in ("scored", "qualified", "contacts_found"):
            pool.extend(db.get_companies(status=st))
        totals = {int(c["cik"]): float((db.latest_score(c["cik"]) or {}).get("total") or 0) for c in pool}
        cap = int(SETTINGS.get("enrich", {}).get("deep", {}).get("max_tasks_per_run", 15))
        targets = angles_mod.select_deep_targets(pool, totals, min(limit, cap))

    if not targets:
        console.print("No deep-tier candidates (statuses scored/qualified/contacts_found).")
        return
    if dry_run:
        for c in targets:
            console.print(f"[dim]{c['ticker']}: would run 1 deep Parallel task + EDGAR funding scan[/dim]")
        console.print(f"[dim]dry run — {len(targets)} companies selected, nothing spent[/dim]")
        return

    from pipeline import parallel_signals
    run_id = db.start_run("enrich:deep")
    console.print(f"[dim]{len(targets)} deep Parallel tasks created up front, polled together…[/dim]")
    deep_results = parallel_signals.collect_deep_batch(targets)

    stats = {"companies": 0, "signals": 0, "angles": 0, "errors": 0}
    for company in targets:
        cik = int(company["cik"])
        f_angles, f_errs = funding_events.collect(company)
        sigs, p_angles, warns = deep_results.get(cik, ([], [], ["no deep result"]))
        task_failed = any(w.startswith("deep task failed") or w.startswith("deep result parse failed") for w in warns)
        if not task_failed:
            # only replace on success — a failed task must not wipe prior parallel signals
            db.replace_signals(cik, "parallel", sigs)
        db.upsert_angles(f_angles + p_angles)
        _print_signals(company["ticker"], sigs, f_errs + warns)
        for a in f_angles + p_angles:
            console.print(f"[dim]  angle \\[{a.family.value}] {a.headline[:70]} (strength {a.strength})[/dim]")
        stats["companies"] += 1
        stats["signals"] += len(sigs)
        stats["angles"] += len(f_angles) + len(p_angles)
        stats["errors"] += len(f_errs) + (1 if task_failed else 0)

    db.finish_run(run_id, stats)
    console.print(f"Done: {stats}")


@app.command()
def enrich(
    source: str = typer.Option("edgar", "--source", help="edgar | parallel | all | deep"),
    limit: int = typer.Option(10, "--limit"),
    ticker: str = typer.Option(None, "--ticker", help="Single company (any status)"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", help="Re-enrich even if already enriched / has parallel signals"),
):
    """Collect signals. EDGAR is free — run it before Parallel (paid)."""
    from pipeline import db, universe
    from pipeline.config import SETTINGS

    if source not in ("edgar", "parallel", "all", "deep"):
        raise typer.BadParameter("--source must be edgar | parallel | all | deep")

    if source == "deep":
        _enrich_deep(limit=limit, ticker=ticker, dry_run=dry_run)
        return

    targets: list[dict] = []
    if ticker:
        row = None
        try:
            row = db.get_company_by_ticker(ticker)
        except SystemExit:
            if not dry_run:
                raise
        if row is None:
            console.print(f"[dim]{ticker} not in DB — resolving live from SEC[/dim]")
            resolved, missing = universe.resolve_tickers([ticker])
            if missing:
                raise typer.BadParameter(f"Unknown ticker {ticker}")
            company = resolved[0]
            if not dry_run:
                db.upsert_companies([company])
            row = company.model_dump(mode="json")
        targets = [row]
    else:
        if source == "parallel":
            # parallel runs after edgar; never re-spend on companies that
            # already have parallel signals unless --force
            pool = db.get_companies(status="new") + db.get_companies(status="enriched")
            if not force:
                sigs_by_cik = db.all_signals()
                pool = [
                    c for c in pool
                    if not any(s["source"] == "parallel" for s in sigs_by_cik.get(int(c["cik"]), []))
                ]
            targets = pool
        else:
            targets = db.get_companies(status="new")
            if force:
                targets += db.get_companies(status="enriched")
        targets = targets[:limit]

    if not targets:
        console.print("Nothing to enrich (no companies in 'new'/'enriched').")
        return

    parallel_cap = int(SETTINGS.get("enrich", {}).get("parallel", {}).get("max_tasks_per_run", 25))
    if source == "parallel" and len(targets) > parallel_cap:
        console.print(
            f"[yellow]Parallel cap ({parallel_cap}/run) — dropping "
            f"{len(targets) - parallel_cap} companies this run[/yellow]"
        )
        targets = targets[:parallel_cap]
    run_id = None if dry_run else db.start_run(f"enrich:{source}")
    stats = {"companies": 0, "signals": 0, "angles": 0, "errors": 0}

    edgar_by_cik: dict[int, tuple[list, list]] = {}
    parallel_by_cik: dict[int, tuple[list, list]] = {}
    angles_by_cik: dict[int, tuple[list, list]] = {}

    if source in ("edgar", "all"):
        from pipeline import edgar_signals, funding_events
        for company in targets:
            edgar_by_cik[int(company["cik"])] = edgar_signals.collect(company)
            angles_by_cik[int(company["cik"])] = funding_events.collect(company)

    if source in ("parallel", "all"):
        batch = targets[:parallel_cap]
        if len(targets) > parallel_cap:
            console.print(
                f"[yellow]Parallel cap ({parallel_cap}/run) — skipping "
                f"{len(targets) - parallel_cap} companies[/yellow]"
            )
        if dry_run:
            for company in batch:
                console.print(f"[dim]{company['ticker']}: would run 1 Parallel task (P1-P6)[/dim]")
        elif batch:
            from pipeline import parallel_signals
            console.print(f"[dim]{len(batch)} Parallel tasks created up front, polled together…[/dim]")
            parallel_by_cik = parallel_signals.collect_batch(batch)

    for company in targets:
        cik = int(company["cik"])
        sigs_e, errs_e = edgar_by_cik.get(cik, ([], []))
        sigs_p, errs_p = parallel_by_cik.get(cik, ([], []))
        if not dry_run:
            if source in ("edgar", "all"):
                db.replace_signals(cik, "edgar", sigs_e)
            if cik in parallel_by_cik and not errs_p:
                # only replace on task success — a failed task must not wipe
                # previously collected parallel signals
                db.replace_signals(cik, "parallel", sigs_p)
        f_angles, f_errs = angles_by_cik.get(cik, ([], []))
        if not dry_run and f_angles:
            db.upsert_angles(f_angles)
        for a in f_angles:
            console.print(f"[dim]  angle \\[funding] {a.headline[:70]}[/dim]")
        stats["errors"] += len(f_errs)
        _print_signals(company["ticker"], sigs_e + sigs_p, errs_e + errs_p + f_errs)
        stats["companies"] += 1
        stats["signals"] += len(sigs_e) + len(sigs_p)
        stats["angles"] += len(f_angles)
        stats["errors"] += len(errs_e) + len(errs_p)
        if not dry_run and company.get("status") in (None, "new", "enriched"):
            db.set_status(cik, "enriched")

    if not dry_run:
        db.finish_run(run_id, stats)
    console.print(f"Done: {stats}")


@app.command()
def score(
    prepare: bool = typer.Option(False, "--prepare"),
    commit: bool = typer.Option(False, "--commit"),
    limit: int = typer.Option(None, "--limit"),
    provider: str = typer.Option("claude-code", "--provider", help="claude-code (v1) | openrouter (v2)"),
    statuses: str = typer.Option(
        "enriched", "--statuses",
        help="Comma-separated company statuses to (re)score with --prepare, e.g. 'enriched,scored'",
    ),
):
    """Score + qualify. v1: --prepare, then the /score skill, then --commit."""
    from pipeline import scoring

    status_tuple = tuple(s.strip() for s in statuses.split(",") if s.strip())

    if provider == "openrouter":
        from pipeline.config import QUEUE_DIR, RESULTS_DIR
        from pipeline.llm import get_provider

        paths = scoring.prepare(limit=limit, statuses=status_tuple)
        llm = get_provider("openrouter")
        for p in paths:
            packet = json.loads(Path(p).read_text())
            verdict = llm.score_packet(packet)
            (RESULTS_DIR / f"{packet['ticker']}.json").write_text(verdict.model_dump_json(indent=2))
            console.print(f"scored {packet['ticker']}: {verdict.total}")
        summary = scoring.commit()
        console.print(json.dumps(summary, indent=2, default=str))
        return

    if prepare:
        paths = scoring.prepare(limit=limit, statuses=status_tuple)
        console.print(f"Prepared {len(paths)} scoring packets in data/scoring_queue/")
        console.print("Next: use the /score skill (Haiku subagents), then `score --commit`.")
        for p in paths:
            console.print(f"  {p}")
        return

    if commit:
        pending = scoring.pending_results()
        if not pending:
            console.print("No results in data/scoring_results/ — run the /score skill first.")
            raise typer.Exit(1)
        summary = scoring.commit()
        for bucket in ("qualified", "review", "disqualified", "kept"):
            items = summary[bucket]
            console.print(f"[bold]{bucket}[/bold] ({len(items)}): " + ", ".join(
                f"{i['ticker']}={i['total']}({i['profile']})"
                + (f" [{i['gate_reason']}]" if i.get("gate_reason") else "")
                for i in items))
        if summary["invalid"]:
            console.print(f"[red]invalid results (fix + rerun): {summary['invalid']}[/red]")
        if summary["orphan"]:
            console.print(f"[yellow]orphan results (no packet/company): {summary['orphan']}[/yellow]")
        return

    console.print("Use --prepare or --commit (v1), or --provider openrouter (v2).")


@app.command()
def people(
    limit: int = typer.Option(None, "--limit"),
    ticker: str = typer.Option(None, "--ticker"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Find decision-makers for qualified accounts (Parallel research, paid)."""
    from pipeline import db
    from pipeline.config import SETTINGS
    from pipeline.people import find_people_batch, select_targets, target_roles

    cap = int(SETTINGS.get("people", {}).get("max_companies_per_run", 10))
    effective_cap = cap if limit is None else min(limit, cap)
    if ticker:
        row = db.get_company_by_ticker(ticker)
        if not row:
            raise typer.BadParameter(f"{ticker} not in pipeline")
        targets = [row]
    else:
        # fetch the full qualified pool (no DB-side limit) so tier/priority
        # ordering is correct before the per-run cap is applied
        pool = db.get_companies(status="qualified")
        priority_by_cik = {int(c["cik"]): (db.latest_score(int(c["cik"])) or {}).get("priority") for c in pool}
        targets = select_targets(pool, priority_by_cik, effective_cap)

    if not targets:
        console.print("No qualified companies awaiting people search.")
        return

    run_id = None if dry_run else db.start_run("people")
    items: list[tuple[dict, list[dict]]] = []
    for company in targets:
        s = db.latest_score(company["cik"])
        fits = (s or {}).get("service_fit") or []
        if dry_run:
            console.print(f"{company['ticker']}: would search roles {target_roles(fits)}")
        else:
            items.append((company, fits))
    if dry_run:
        return

    console.print(f"[dim]{len(items)} Parallel people tasks created up front, polled together…[/dim]")
    results = find_people_batch(items)
    found_total = 0
    for (company, _), res in zip(items, results):
        if isinstance(res, Exception):
            console.print(f"[red]{company['ticker']}: people search failed: {res}[/red]")
            continue
        contacts, notes = res
        db.insert_contacts(contacts)
        db.set_status(company["cik"], "contacts_found")
        found_total += len(contacts)
        table = Table(title=f"{company['ticker']} — {company['name'][:40]}")
        for col in ("name", "title", "linkedin", "email", "conf"):
            table.add_column(col)
        for c in contacts:
            table.add_row(c.name, c.title[:40], (c.linkedin_url or "—")[:44], c.email or "—", c.confidence)
        console.print(table)
        if notes:
            console.print(f"[dim]{notes}[/dim]")
    if not dry_run:
        db.finish_run(run_id, {"companies": len(items), "contacts": found_total})


@app.command()
def messages(
    prepare: bool = typer.Option(False, "--prepare"),
    commit: bool = typer.Option(False, "--commit"),
    limit: int = typer.Option(None, "--limit", help="Max contact packets to prepare"),
    ticker: str = typer.Option(None, "--ticker", help="Single company (any status)"),
    force: bool = typer.Option(False, "--force", help="Regenerate even if a draft exists for the angle"),
    dry_run: bool = typer.Option(False, "--dry-run", help="With --prepare: list target contacts, write nothing"),
):
    """Draft 4-step outreach sequences per contact. v1: --prepare, then the /outreach skill, then --commit."""
    from pipeline import db
    from pipeline import messages as messages_mod

    if prepare:
        if ticker:
            row = db.get_company_by_ticker(ticker)
            if not row:
                raise typer.BadParameter(f"{ticker} not in pipeline")
            if row["status"] != "contacts_found":
                console.print(f"[yellow]{ticker} status is '{row['status']}' (not contacts_found) — preparing anyway[/yellow]")
        paths, skips = messages_mod.prepare(limit=limit, ticker=ticker, force=force, dry_run=dry_run)
        verb = "Would prepare" if dry_run else "Prepared"
        console.print(f"{verb} {len(paths)} message packets in data/message_queue/ (one per contact)")
        for p in paths:
            console.print(f"  {p}")
        for reason, items in skips.items():
            if items:
                console.print(f"[dim]skipped ({reason}): {', '.join(items)}[/dim]")
        if paths and not dry_run:
            console.print("Next: use the /outreach skill (Haiku subagents), then `messages --commit`.")
        return

    if commit:
        pending = messages_mod.pending_results()
        if not pending:
            console.print("No results in data/message_results/ — run the /outreach skill first.")
            raise typer.Exit(1)
        rid = db.start_run("messages")
        summary = messages_mod.commit()
        db.finish_run(rid, {
            "written": len(summary["written"]), "invalid": len(summary["invalid"]),
            "orphan": len(summary["orphan"]),
        })
        for item in summary["written"]:
            console.print(f"[bold]{item['ticker']}[/bold] {item['contact']} — {item['archetype']} / {item['service']}")
        for stem, warns in summary["warnings"].items():
            for w in warns:
                console.print(f"[yellow]  {stem}: {w}[/yellow]")
        if summary["invalid"]:
            console.print("[red]failed QA (re-spawn their subagents, then commit again):[/red]")
            for item in summary["invalid"]:
                console.print(f"[red]  {item}[/red]")
        if summary["orphan"]:
            console.print(f"[yellow]orphan results (no packet): {summary['orphan']}[/yellow]")
        console.print(f"Drafted {len(summary['written'])} sequences -> `pipeline export --messages` when ready.")
        return

    console.print("Use --prepare or --commit.")


# v3: static sending guidance embedded in every deliverability_checklist.md —
# gtm-flywheel best practices, not pipeline output. Edit here, not the
# generated file (export --messages regenerates it every run).
DELIVERABILITY_GUIDANCE = """\
# Deliverability Checklist

Regenerated by `export --messages` — edit the pipeline, not this file.

## Domain & authentication
- SPF, DKIM, and DMARC all configured and passing for the sending domain
  (and any sending subdomain) before the first send.
- Warm up new sending domains/mailboxes gradually — never blast the full
  list on day one.

## Sending behavior
- Stop-on-reply: ON — a reply from the prospect halts the rest of their
  sequence immediately.
- Plain text only — no HTML templates, no embedded images.
- Link tracking: OFF — tracked/rewritten links read as surveillance and
  hurt deliverability; the copy is link-free in step 1 by design (hard QA
  gate) and discouraged elsewhere.
- Open tracking: OFF — tracking pixels are a spam-score and privacy
  liability with no upside for a sequence this personalized.
- Stagger sends 10-20 minutes apart per mailbox — never a synchronized
  batch blast.

## Before the first send
- Spot-check every `unverified number` QA warning against its source packet.
- Review `personalization N/5` warnings below the configured minimum
  (`messages.personalization_min`) — candidates for a manual rewrite pass.
"""


def _deliverability_stats(msg_by_cik: dict) -> dict:
    """Computed stats over the drafts about to be exported — count, avg
    step-1 word count, link count, and how many drafts carry a QA warning."""
    all_msgs = [m for msgs in msg_by_cik.values() for m in msgs]
    step1_word_counts = []
    link_count = 0
    warned = 0
    for m in all_msgs:
        if m.get("qa_warnings"):
            warned += 1
        for s in m.get("steps") or []:
            body = s.get("body") or ""
            link_count += len(re.findall(r"https?://|www\.", body, re.IGNORECASE))
            if s.get("step") == 1:
                step1_word_counts.append(len(body.split()))
    avg_step1 = (sum(step1_word_counts) / len(step1_word_counts)) if step1_word_counts else 0
    return {
        "count": len(all_msgs),
        "avg_step1_words": avg_step1,
        "link_count": link_count,
        "warned_count": warned,
    }


def _deliverability_checklist_md(stats: dict) -> str:
    return DELIVERABILITY_GUIDANCE + (
        "\n## This export\n"
        f"- Drafts exported: {stats['count']}\n"
        f"- Avg step-1 word count: {stats['avg_step1_words']:.0f}\n"
        f"- Links found across all steps: {stats['link_count']}\n"
        f"- Drafts with at least one QA warning: {stats['warned_count']}\n"
    )


@app.command()
def export(
    out: Path = typer.Option(Path("data/exports/qualified.csv"), "--out"),
    messages: bool = typer.Option(False, "--messages", help="Also write data/exports/messages.csv (one row per step)"),
):
    """CSV of qualified accounts + contacts; --messages adds drafted sequences."""
    from pipeline import angles as angles_mod
    from pipeline import db

    rows = []
    for st in ("qualified", "contacts_found"):
        for company in db.get_companies(status=st):
            s = db.latest_score(company["cik"]) or {}
            fits = s.get("service_fit") or []
            active = [a for a in db.get_angles(company["cik"])
                      if angles_mod.is_fresh(a["family"], a["event_date"])]
            pa = s.get("primary_angle") or {}
            pa_fp = pa.get("fingerprint")
            pa_row = next((a for a in active if a["fingerprint"] == pa_fp), None) if pa_fp else None
            if pa_row is not None:
                angle_family = pa.get("family") or ""
                pa_headline = pa_row["headline"]
                hook = next((r.get("message_hook", "") for r in (s.get("angle_ranking") or [])
                             if r.get("fingerprint") == pa_fp), "")
            else:
                angle_family = ""
                pa_headline = ""
                hook = ""
            base = {
                "ticker": company["ticker"], "company": company["name"],
                "sector": company["sector_bucket"], "market_cap": company["market_cap"],
                "status": company["status"], "profile": company.get("profile"),
                "score": s.get("total"), "lead_service": fits[0]["service"] if fits else "",
                "why_now": (s.get("why_now") or "")[:300],
                "reasoning": (s.get("reasoning") or "")[:300],
                "angle_ready": bool(active),
                "angle_family": angle_family,
                "primary_angle": pa_headline,
                "message_hook": hook,
            }
            contacts = db.get_contacts(company["cik"])
            if not contacts:
                rows.append({**base, "contact": "", "title": "", "linkedin": "", "email": ""})
            for c in contacts:
                rows.append({**base, "contact": c["name"], "title": c["title"],
                             "linkedin": c.get("linkedin_url") or "", "email": c.get("email") or ""})
    if rows:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            writer = csv_mod.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        console.print(f"Wrote {len(rows)} rows -> {out}")
    else:
        console.print("Nothing qualified yet.")

    if not messages:
        return
    msg_by_cik = db.all_messages()
    if not msg_by_cik:
        console.print("No drafted sequences yet — run `messages --prepare` + /outreach first.")
        return
    companies_by_cik = {int(c["cik"]): c for c in db.get_companies()}
    msg_rows = []
    for cik, msgs in sorted(msg_by_cik.items()):
        comp = companies_by_cik.get(cik, {})
        contacts_by_id = {c.get("id"): c for c in db.get_contacts(cik)}
        angle_by_fp = {a["fingerprint"]: a for a in db.get_angles(cik)}
        for m in msgs:
            # email/linkedin joined live via contact_id so later email-finding
            # enriches this export for free; name/title from the snapshot
            c = contacts_by_id.get(m.get("contact_id"), {})
            headline = (angle_by_fp.get(m.get("angle_fingerprint")) or {}).get("headline", "")
            for s in m.get("steps") or []:
                msg_rows.append({
                    "message_id": m.get("id"),
                    "ticker": m["ticker"], "company": comp.get("name", ""),
                    "contact": m["contact_name"], "title": m["contact_title"],
                    "email": c.get("email") or "", "linkedin": c.get("linkedin_url") or "",
                    "role_bucket": c.get("role_bucket") or "",
                    "archetype": m["archetype"], "service": m["service"],
                    "angle_family": m["angle_family"], "angle_headline": headline,
                    "step": s.get("step"), "day_offset": s.get("day_offset"),
                    "subject": s.get("subject") or "", "body": s.get("body") or "",
                    "cta_type": s.get("cta_type") or "",
                    "qa_warnings": "; ".join(m.get("qa_warnings") or []),
                    "status": m.get("status"), "created_at": m.get("created_at"),
                })
    msg_out = out.parent / "messages.csv"
    with open(msg_out, "w", newline="") as fh:
        writer = csv_mod.DictWriter(fh, fieldnames=list(msg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(msg_rows)
    console.print(f"Wrote {len(msg_rows)} step rows ({sum(len(v) for v in msg_by_cik.values())} sequences) -> {msg_out}")

    stats = _deliverability_stats(msg_by_cik)
    checklist_path = out.parent / "deliverability_checklist.md"
    checklist_path.write_text(_deliverability_checklist_md(stats))
    console.print(f"Wrote deliverability checklist -> {checklist_path}")


def _parse_date_opt(value: str | None) -> str | None:
    """Validate an optional YYYY-MM-DD string early so a typo surfaces as a
    clear CLI error, not a Postgres error deep inside outcomes.record()."""
    if not value:
        return None
    from datetime import datetime as _dt

    try:
        _dt.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise typer.BadParameter(f"--date must be YYYY-MM-DD, got {value!r}")
    return value


def _outcome_csv_batch(path: Path) -> None:
    """Batch mode for `pipeline outcome --csv`: columns message_id,event,date,note
    (date/note optional). Every row is attempted; failures — including
    transient/unexpected errors (e.g. a DB hiccup) — are reported per row and
    do not abort the remaining rows; the command exits nonzero if any row
    failed."""
    from pipeline import outcomes

    with open(path, newline="") as fh:
        rows = list(csv_mod.DictReader(fh))
    if not rows:
        console.print("[yellow]CSV has no rows[/yellow]")
        return

    ok, failed = 0, 0
    for i, row in enumerate(rows, start=1):
        try:
            message_id = int(row["message_id"])
            event = (row.get("event") or "").strip()
            occurred_at = _parse_date_opt((row.get("date") or "").strip() or None)
            note = row.get("note") or ""
            result = outcomes.record(message_id, event, occurred_at, note)
            arrow = f"{result['previous_status']} -> {result['new_status']}"
            suffix = "" if result["advanced"] else " (no status change)"
            console.print(f"  row {i}: message {message_id} {event} ({arrow}){suffix}")
            ok += 1
        except Exception as exc:
            console.print(f"[red]  row {i}: {exc}[/red]")
            failed += 1

    console.print(f"Recorded {ok}/{len(rows)} events" + (f", {failed} failed" if failed else ""))
    if failed:
        raise typer.Exit(1)


@app.command()
def outcome(
    message_id: int = typer.Argument(None, help="messages.id to record an outcome event against"),
    event: str = typer.Option(None, "--event", help="approved|rejected|exported|sent|bounced|replied|positive_reply|meeting|opt_out"),
    date: str = typer.Option(None, "--date", help="YYYY-MM-DD (default: now)"),
    note: str = typer.Option("", "--note"),
    csv: Path = typer.Option(None, "--csv", help="Batch mode: CSV with columns message_id,event,date,note"),
    ticker: str = typer.Option(None, "--ticker", help="Fuzzy lookup fallback when message_id is omitted (pair with --contact)"),
    contact: str = typer.Option(None, "--contact", help="Fuzzy lookup fallback when message_id is omitted (pair with --ticker)"),
):
    """Record an outcome event against a drafted message. The event is
    always logged (append-only); messages.status advances only if the event
    moves it forward on the ladder — see outcomes.py."""
    from pipeline import db, outcomes

    if csv:
        _outcome_csv_batch(csv)
        return

    if message_id is None:
        if not (ticker and contact):
            raise typer.BadParameter(
                "Provide a message_id, or --ticker + --contact for a fuzzy lookup"
            )
        matches = db.find_messages(ticker, contact)
        if not matches:
            console.print(f"[red]No messages found for {ticker} with contact matching {contact!r}[/red]")
            raise typer.Exit(1)
        if len(matches) > 1:
            console.print(f"[yellow]{len(matches)} matches for {ticker} / {contact!r} — re-run with an exact message_id:[/yellow]")
            for m in matches:
                console.print(f"  message_id={m['id']}  {m['ticker']}  {m['contact_name']}  status={m['status']}")
            raise typer.Exit(1)
        message_id = matches[0]["id"]

    if not event:
        raise typer.BadParameter("--event is required (unless using --csv)")

    occurred_at = _parse_date_opt(date)
    try:
        result = outcomes.record(message_id, event, occurred_at, note)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    arrow = f"{result['previous_status']} -> {result['new_status']}"
    suffix = "" if result["advanced"] else " (no status change)"
    console.print(f"message {result['message_id']}: {result['event']} recorded ({arrow}){suffix}")


@app.command()
def promote(tickers: str = typer.Argument(..., help="Comma-separated tickers to promote from the review band to qualified")):
    """Human review-band decision: mark 'scored' companies as qualified."""
    from pipeline import db

    for t in [x.strip() for x in tickers.split(",") if x.strip()]:
        row = db.get_company_by_ticker(t)
        if not row:
            console.print(f"[red]{t}: not in pipeline[/red]")
            continue
        if row["status"] not in ("scored", "disqualified"):
            console.print(f"[yellow]{t}: status is '{row['status']}' — nothing to promote[/yellow]")
            continue
        db.set_status(row["cik"], "qualified")
        console.print(f"{t}: scored -> qualified (human review)")


@app.command()
def prune(dry_run: bool = typer.Option(False, "--dry-run", help="List what would be removed without deleting")):
    """Remove status-'new' companies the current screen no longer includes (universe corrections)."""
    from pipeline import db, universe

    keep = {c.cik for c in universe.screen()[0]}
    stale = [r for r in db.get_companies(status="new") if int(r["cik"]) not in keep]
    if not stale:
        console.print("Nothing to prune — all 'new' companies still pass the screen.")
        return
    table = Table(title=f"{'Would prune' if dry_run else 'Pruning'} {len(stale)} companies")
    for col in ("ticker", "name", "sic", "sector"):
        table.add_column(col)
    for r in stale[:20]:
        table.add_row(r["ticker"], (r["name"] or "")[:45], r.get("sic") or "", r.get("sector_bucket") or "")
    if len(stale) > 20:
        table.add_row("…", f"+ {len(stale) - 20} more", "", "")
    console.print(table)
    if dry_run:
        return
    n = db.delete_new_companies([int(r["cik"]) for r in stale])
    console.print(f"Pruned {n} companies (status was 'new')")


@app.command(name="apply-schema")
def apply_schema():
    """Apply sql/schema.sql to Supabase (needs SUPABASE_DB_URL in .env)."""
    from pipeline.config import PROJECT_ROOT, normalize_pg_dsn, require_env

    dsn = normalize_pg_dsn(require_env("SUPABASE_DB_URL"))
    sql = (PROJECT_ROOT / "sql" / "schema.sql").read_text()
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute(sql)
        conn.commit()
    console.print("Schema applied ✔")
