"""AIPT pipeline CLI. Run via: uv run python -m pipeline <command>"""
from __future__ import annotations

import csv as csv_mod
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="AI-readiness pipeline for public micro-caps (martechs.io)", no_args_is_help=True)
console = Console()

STATUS_ORDER = ["new", "enriched", "scored", "qualified", "disqualified", "contacts_found"]


@app.command()
def status(brief: bool = typer.Option(False, "--brief", help="One-line funnel summary")):
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


@app.command()
def ingest(
    tickers: str = typer.Argument("", help="Comma-separated tickers, e.g. 'ABCD,EFGH'"),
    csv: Path = typer.Option(None, "--csv", help="CSV file with a 'ticker' column"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Add user-provided companies to the pipeline (overrides the screen)."""
    from pipeline import db, universe

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
    uni = __import__("pipeline.config", fromlist=["SETTINGS"]).SETTINGS.get("universe", {})
    cap_min, cap_max = uni.get("market_cap_min", 0), uni.get("market_cap_max", 0)
    for c in resolved:
        note = ""
        if c.sector_bucket.value == "other":
            note = "outside target sectors"
        elif c.market_cap and not (cap_min <= c.market_cap <= cap_max):
            note = "outside cap band"
        table.add_row(
            c.ticker, c.name[:36], c.sector_bucket.value,
            f"${(c.market_cap or 0)/1e6:.0f}M" if c.market_cap else "?",
            c.exchange or "?", note,
        )
    console.print(table)
    if unresolved:
        console.print(f"[yellow]Unresolved: {', '.join(unresolved)}[/yellow]")

    if dry_run:
        console.print("[dim]dry run — nothing written[/dim]")
        return
    known = db.existing_ciks()
    fresh = [c for c in resolved if c.cik not in known]
    skipped = len(resolved) - len(fresh)
    n = db.upsert_companies(fresh)
    console.print(f"Ingested {n} new companies" + (f" ({skipped} already in pipeline, untouched)" if skipped else ""))


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
        sample.add_row(c.ticker, c.name[:40], c.sector_bucket.value, f"${(c.market_cap or 0)/1e6:.0f}M")
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
        targets = angles_mod.select_deep_targets(pool, totals, min(limit or cap, cap))

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
        _print_signals(company["ticker"], sigs_e + sigs_p, errs_e + errs_p)
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
):
    """Score + qualify. v1: --prepare, then the /score skill, then --commit."""
    from pipeline import scoring

    if provider == "openrouter":
        from pipeline.config import QUEUE_DIR, RESULTS_DIR
        from pipeline.llm import get_provider

        paths = scoring.prepare(limit=limit)
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
        paths = scoring.prepare(limit=limit)
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
        for bucket in ("qualified", "review", "disqualified"):
            items = summary[bucket]
            console.print(f"[bold]{bucket}[/bold] ({len(items)}): " + ", ".join(
                f"{i['ticker']}={i['total']}({i['profile']})" for i in items))
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
    from pipeline.people import find_people_batch, target_roles

    cap = int(SETTINGS.get("people", {}).get("max_companies_per_run", 10))
    if ticker:
        row = db.get_company_by_ticker(ticker)
        if not row:
            raise typer.BadParameter(f"{ticker} not in pipeline")
        targets = [row]
    else:
        targets = db.get_companies(status="qualified", limit=min(limit or cap, cap))

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
def export(out: Path = typer.Option(Path("data/exports/qualified.csv"), "--out")):
    """CSV of qualified accounts + contacts (one row per contact)."""
    from pipeline import db

    rows = []
    for st in ("qualified", "contacts_found"):
        for company in db.get_companies(status=st):
            s = db.latest_score(company["cik"]) or {}
            fits = s.get("service_fit") or []
            base = {
                "ticker": company["ticker"], "company": company["name"],
                "sector": company["sector_bucket"], "market_cap": company["market_cap"],
                "status": company["status"], "profile": company.get("profile"),
                "score": s.get("total"), "lead_service": fits[0]["service"] if fits else "",
                "why_now": (s.get("why_now") or "")[:300],
                "reasoning": (s.get("reasoning") or "")[:300],
            }
            contacts = db.get_contacts(company["cik"])
            if not contacts:
                rows.append({**base, "contact": "", "title": "", "linkedin": "", "email": ""})
            for c in contacts:
                rows.append({**base, "contact": c["name"], "title": c["title"],
                             "linkedin": c.get("linkedin_url") or "", "email": c.get("email") or ""})
    if not rows:
        console.print("Nothing qualified yet.")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        writer = csv_mod.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    console.print(f"Wrote {len(rows)} rows -> {out}")


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
