# Signal Taxonomy

Every signal carries evidence (URL + quote where possible) and a weight
(configurable in `config/settings.yaml` → `scoring.weights`). Signals feed four
scoring components: **intent**, **capability_gap**, **timing**, **commercial_fit**
(mapping in `src/pipeline/scoring.py` → `COMPONENT_OF`).

**Hard signals** (at least one required to qualify): E1, E3, E4, E5, P1, P2, P3.

## EDGAR signals (free — SEC filings via edgartools + XBRL companyfacts)

| Type | Name | Detection | Why it matters | Component |
|------|------|-----------|----------------|-----------|
| E1 | New/rising AI language | AI terms in 10-K Business/MD&A sections; first-time (prior year zero) scores full weight, sharp YoY rise ~70%, sustained ~50% | Board/investors are asking; budget conversations start | intent |
| E2 | AI in risk factors only | AI terms appear ONLY in Item 1A | Aware but defensive — classic laggard marker, consultation angle | intent |
| E3 | Leadership change ≤12mo | 8-K Item 5.02 appointment language + C-level titles | New execs buy new capabilities in their first year | timing |
| E4 | Restructuring / cost program | 8-K Item 2.05 or cost-program phrases | Efficiency mandate → automation/agents ROI pitch | timing |
| E5 | GTM inefficiency | XBRL: S&M (or SG&A) % of revenue rising ≥5% relatively while revenue growth <15% | Paying more for each dollar of growth → AI lead-gen/outreach pitch | commercial_fit |
| E6 | No tech leadership | Latest DEF 14A names no CTO/CIO/CDO-type officer | Nobody inside owns AI → needs outside partner | capability_gap |
| E7 | Recent IPO ≤24mo | 424B4 / S-1 / 8-A12B filed within window | Building GTM + reporting muscle from scratch | timing |
| E8 | Sector peer laggard | Derived at scoring time: no AI language while ≥40% of enriched sector peers show AI signals | Competitive-pressure angle | (packet-only) |
| E9 | Cash capacity | XBRL cash & equivalents ≥ $10M | Can actually afford a services engagement | commercial_fit |

## Parallel signals (paid — web research, one structured task per company)

| Type | Name | Detection | Why it matters | Component |
|------|------|-----------|----------------|-----------|
| P1 | AI job postings | Open AI/ML/data-science roles | Investing in AI (adopter marker) — often still buys specialists | commercial_fit |
| P2 | GTM hiring | SDR/BDR/demand-gen/marketing openings | Scaling outbound the expensive way → outreach services pitch | commercial_fit |
| P3 | AI announcements | Press releases/news on AI initiatives ≤18mo | Proven budget + appetite (adopter marker) | intent |
| P4 | Product AI gap | No AI in product while direct competitors ship it | Falling behind → custom agents / implementation pitch | capability_gap |
| P5 | Martech stack | Evidence of marketing/sales tooling maturity | Low maturity → marketing automation opportunity | commercial_fit |
| P6 | Exec AI commentary | Earnings-call/interview quotes about AI | Direct voice-of-buyer evidence; great for personalization later | intent |

## Profile classification

- **laggard** — intent without execution: E1/E2/E8 present, P1/P3 absent. Lead: consultation/implementation.
- **adopter** — execution visible: P1 and/or P3 present. Lead: specialist services (lead-gen, outreach, custom agents).
- **hybrid** — intent + early scattered execution. **unclear** — weak evidence.

## Scoring

`total = intent(≤30) + capability_gap(≤25) + timing(≤25) + commercial_fit(≤20)`

Deterministic base score = capped sum of signal weights per component. The LLM
scorer (Haiku subagent) sees the base math and may deviate with justification.
Qualify: `total ≥ 65` AND ≥1 hard signal. Disqualify: `total < 45`. Between:
stays `scored` — human review band. All thresholds in `config/settings.yaml`.
