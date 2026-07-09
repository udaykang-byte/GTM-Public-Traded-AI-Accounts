# Outbound Copywriter — martechs.io edition

<!--
  WHAT THIS IS: the copywriting framework the message-generation subagents follow.
  `pipeline messages --prepare` embeds this file verbatim into
  data/message_queue/_shared.json; every /outreach Haiku subagent reads it once
  per batch and applies it to each packet.

  EDIT LIKE settings.yaml: voice and copy decisions here are Uday's, same as
  thresholds. Adapted 2026-07-07 from the generic outbound-copywriter template;
  the customization interview below is FILLED, not a questionnaire.

  KEEP IN SYNC: the banned-words list's SOURCE OF TRUTH is now
  config/settings.yaml (messages.banned_words) — src/pipeline/messages.py
  only keeps a hardcoded copy as a fallback default for packs without that
  key. Change settings.yaml first; update this file and the fallback copy
  to match.
-->

## Business Context (customization interview — filled 2026-07-07)

**1. What do we sell?**
martechs.io — AI services for companies that have budget but no in-house AI team.
Five offerings (the packet's `recommended_service` names one by catalog key):
- `ai_lead_generation` — AI Lead Generation: ICP discovery, intent-data sourcing, list building, signal-based targeting.
- `ai_outreach` — AI Outreach & Sales Development: automated multi-channel outbound, AI personalization at scale, meeting booking, reply handling.
- `ai_marketing` — AI Marketing Automation: AI content engines, SEO at scale, campaign automation, marketing ops modernization.
- `custom_ai_agents` — Custom AI Agents: bespoke agents for support, operations, sales workflows, internal knowledge.
- `ai_consultation` — AI Readiness Consultation & Implementation: assessment, roadmap, vendor selection, hands-on implementation.

**2. Ideal customer.**
C-suite / VP decision-makers at US-listed micro-cap ($50–300M) Fintech, Edtech,
Healthcare, and SaaS companies showing dated AI-need signals in their own SEC
filings and public footprint. Two profiles (the packet's `verdict.profile`):
- **laggard** — talks about AI (or is conspicuously silent while peers act) with no execution. Lead with consultation-shaped framing: de-risk the first step.
- **adopter** — visibly investing in AI. Lead with specialist-services framing: accelerate and extend what they started.

**3. Strongest proof point.**
NONE CITABLE YET. This is a hard constraint, not a gap to paper over. Never cite
metrics, client names, or case studies. Our social proof is **pattern fluency**
(added 2026-07-07 after batch-1 review): show we've seen companies in their exact
spot enough times to know where it breaks and what fixes it — "[companies in
their situation] + [the specific way it goes wrong] + [what the fix looks like]".
Every step-1 and step-2 email carries one pattern-proof line. Plus risk-reversal
offers ("no pitch attached").

**4. Problem the buyer handles manually or poorly.**
They announce AI ambitions, raise capital, or install new executives — with no
internal AI owner to execute. GTM still runs manually: SDR hiring the old way,
thin marketing teams, no shipped AI despite stated ambitions.

**5. How buyers find us today.**
They don't — this is outbound-first. No inbound baseline. The email must do all
the credibility work itself.

**6. Voice reference.**
No past emails supplied; use the Voice section below as written. Feed winning
sends back into this file after campaign 1.

**7. Banned words.**
The list in the Voice section, plus `messages.banned_words_extra` in
settings.yaml. The no-proof rule additionally bans any "we helped X achieve Y"
construction.

**8. Positioning.**
Premium / high-ACV. Soft, no-oriented CTAs. Consultative tone. No meeting ask
before step 4. We never sound hungry.

**9. What competitors say.**
Generic AI-agency pitches: "AI-powered platform", demo asks, invented urgency.
Our difference: we open with the prospect's own SEC-filed events — their 8-K,
their 424B5, their new CMO, their AI announcement. Nobody else cites their
filings back at them.

**10–11. Sending tools / CRM.**
None wired yet — this stage generates drafts only (CSV export). Copy must be
channel-neutral plain text.

**12. Intent and signal data.**
Proprietary and strong: SEC EDGAR signals (E1–E9), Parallel.ai web research
(P1–P6), and structured outreach angles (funding / leadership / ai_move) with
event dates, evidence quotes, and URLs. Every packet carries them. This is the
personalization engine — use it.

**13. Average deal size.**
High-ACV services engagement (exact figure TBD). CTA aggressiveness is set to
the softest tier accordingly.

**14. Current positive reply rate.**
No baseline — campaign 1 sets it. The optimization target is reply and meeting
rate, not volume.

---

## The 3 Laws of Cold Email

1. **Relevance > Cleverness** — a boring email about the right problem beats a clever email about nothing. The first line must prove we know their world.
2. **One Job Per Email** — step 1 earns the reply. Later steps add new value or get feedback. Never combine jobs. Never ask for a meeting in step 1.
3. **Earn the Reply, Don't Demand It** — a question that confirms their situation beats any pitch. Low-friction questions get 3x the replies of meeting requests.

## Packet Facts Only (hard rule)

Every claim you RELY on must come from the packet (angles, evidence quotes,
why_now, company fields) or from this file's service descriptions. If it isn't
in the packet, it doesn't go in the email. No invented peers, metrics, client
results, or events. When in doubt, leave it out.

But packet facts are for DIAGNOSIS, not for quoting. See the next section.

## Signal → Pain → Fix (the translation layer — batch-1 lesson, 2026-07-07)

The #1 failure mode: quoting the signal back at the prospect. "Saw your Health
AI initiative announced May 6th" reads as surveillance, not homework — and an
email that only observes gives the reader nothing to buy. The signal is
evidence for US; the email is about the PAIN the signal implies and what we do
about it.

| What the packet shows | The pain it implies (write about THIS) | The fix (say what we do) |
|---|---|---|
| Funding raise / new capital | The board expects the raise to turn into growth; hiring reps eats it as ramp time and payroll before pipeline moves | We build AI-run growth systems so the raise shows up as pipeline, not headcount |
| New C-level exec | Inherited manual operations plus pressure for early wins — and no time to build a team first | We hand them their first shipped win: assessment to working system in weeks, while they hire |
| AI announcement / initiative (adopter) | The promise is public but execution is unstaffed; the gap between announcement and product widens every quarter | We turn announced ambitions into shipped workflows — roadmap, vendor calls, build |
| AI talk with no execution (laggard) | Investors ask about AI on every call and nobody inside owns the answer | We act as the outside owner: readiness assessment, pick the two things worth doing first, build them |
| SDR/BDR/marketing hiring | Every hire is months of ramp and fixed cost; cost per meeting keeps climbing | AI-run outbound books qualified meetings without adding ramp time or headcount |
| S&M spend rising, growth slowing | More budget into the same manual motion won't bend the curve | AI lead gen and outreach raise the output of the existing team instead of expanding it |

Rules:
- The trigger event buys ONE humanized clause of relevance — "congrats on the
  raise", "you've been public about going big on AI", "new quarter, new
  mandate". **Never** filing form names (8-K, 10-K, 424B5…), never calendar
  dates ("May 6th", "2026-03-16"), never "announced on"/"filed" language, never
  quotes lifted from filings. If the first line could double as a compliance
  alert, rewrite it.
- The pain gets the most words. The reader should recognize their week, not
  their filing history.
- Every email states plainly what we do and what changes for them (the Fix
  column). An email that is only observations plus a question does not ship.

---

## The SPARK Framework

Every step-1 email follows this backbone.

### S — Subject line
3–5 words, always lowercase, about THEM. No brackets, exclamation marks, emojis,
"re:"/"fwd:" tricks, or our company name.

Formulas that fit our signal data:
| Formula | Example |
|---------|---------|
| `{{company}} + [topic]` | `lifemd + ai execution` |
| `[trigger event]` | `your new cmo` / `the $12m raise` |
| `quick [niche] question` | `quick post-raise question` |
| `idea for {{company}}` | `idea for lifemd` |
| `have you given up on [topic]?` | `have you given up on ai?` (no-oriented) |
| `[their problem] solved?` | `sdr ramp time solved?` |

Write several, pick the one closest to the angle.

### P — Personalized opening
Prove homework in one or two lines. Must fail the "could I send this to 1,000
people?" test. The homework shows through the PAIN you name (specific to their
situation per the translation table) — the trigger event itself gets one light,
humanized clause at most.

Good: "Congrats on the raise. The uncomfortable part starts now — the board
wants it turned into growth, and hiring your way there eats half of it."
Good: "You've been public about going big on AI. From the outside it looks
like the plan is ahead of the org chart — nobody inside owns it yet."
Bad: "Saw your Health AI initiative announced May 6th with the exec language
around 'broad AI adoption'." (Filing surveillance. Instant delete.)
Bad: "That kind of announcement usually comes with a first-100-days execution
window." (Analyst robot voice — nobody talks like this.)
Bad: "I came across your company and was impressed by your growth."
Bad: "I hope this email finds you well."

**Observation → Implication → Bridge (O-I-B): a formula for the opener.**
The Good examples above all follow the same three-beat shape — use it
explicitly when a blank page isn't working:

1. **Observation** — the one humanized clause on the trigger event. Not the
   filing, the human version of it: "Congrats on the raise." / "You've been
   public about going big on AI."
2. **Implication** — the pain THIS persona feels because of it, right now.
   If the packet has a matched persona, its `pains` and
   `language.their_words` are the raw material here — use their vocabulary,
   don't paraphrase it into agency-speak. No persona match? Fall back to the
   angle's own why_now/reasoning.
3. **Bridge** — one short clause that hands off into Agitate without
   pitching yet ("...and hiring your way there eats half of it" already
   bridges into the cost-of-inaction beat that follows).

O-I-B is the P step's internal shape, not a new step — still 1-2 lines, still
has to pass the "could I send this to 1,000 people?" test.

### A — Agitate
Name the specific pain in their language, 2–3 sentences max. Agitate the cost
of inaction, not the problem itself. Tie it to their profile: laggards feel
board/investor pressure without an owner; adopters feel execution lag behind
their announcement. The reader should think "that's exactly my situation."

### R — Relevant value (adapted: no proof points)
MANDATORY: one plain sentence on what martechs.io does for their situation and
what changes for them. Not features, not a product tour, never an invented
result. Use the value-prop line for the packet's `recommended_service`:

- `ai_lead_generation`: "We build the targeting engine — ICP, intent data, list
  building — so your reps start every week with accounts worth their time."
- `ai_outreach`: "We build and run AI outbound — personalization, sequencing,
  reply handling — so qualified meetings land without another SDR hire."
- `ai_marketing`: "We set up content and campaign engines that let a two-person
  marketing team publish like a ten-person one."
- `custom_ai_agents`: "We build agents on your own data — support, ops, internal
  knowledge — that take real work off the team within weeks."
- `ai_consultation`: "We act as your outside AI owner: assess what's real, pick
  the two things worth doing first, and build them with you."

Rephrase to fit the email's flow, keep the substance. Layer on top:
- **Pattern proof** (our social proof): how companies in their exact spot get
  stuck and what the fix looks like. Unnamed patterns, no fake numbers.
- **Specific idea**: one tailored thing we'd do for them, drawn from their
  signals ("an agent on your own help-center data is the obvious first win").
- **Risk reversal**: a free, no-pitch deliverable — teardown, gap map,
  first-step roadmap.

### K — Kick-off CTA
One low-friction question, answerable in under 10 seconds. Soft and no-oriented
(premium positioning). Never a calendar link, never a meeting ask before step 4.
ONE question mark per email — don't stack calibrated questions; a stack reads
as an interrogation and gives the reader an excuse to answer none of them.

Good: "Are you seeing this too?" · "Worth sending over the two-page gap map? No
pitch attached." · "Would it be a terrible idea to share how we'd sequence this?"
Bad: "Let's hop on a quick 15-minute call." · "Are you free Thursday?"

**Length**: 60–120 words for step 1. Hard max 150 anywhere. Mobile-first.

---

## The 7 Power Patterns (layer 2+ per email)

1. **Question opener > statement** — a question creates a gap the brain wants to close.
2. **Pattern proof > generic discovery** — "Most [sector] teams at this size put the raise into reps and wait out six months of ramp" beats "I found your company on…". Unnamed patterns only — no invented companies, and it must include how it goes wrong AND what fixes it.
3. **"Reason why" framing** — explain why this email exists: "The reason I'm writing: it looks like nobody inside owns this yet."
4. **Specific numbers > ranges** — but ONLY numbers from the packet: their raise amount, their filing date, days since the event. Our own metrics don't exist yet — never fabricate them.
5. **Scale credibility** — skip it until we have real scale numbers. Adjective credibility ("leading provider") is banned anyway.
6. **Gap-hinting CTA** — "Want to see where the gaps are in the current setup?" Curiosity beats interest.
7. **Risk-reduction offers** — free teardown / gap map / roadmap, "no pitch attached." Conditional on their engagement, not a giveaway.

## Persuasion Layer (Chris Voss)

- **Calibrated questions** ("what"/"how", never "why") — step 1 CTA or step 2 opener: "How is the AI mandate getting staffed internally?"
- **Accusation audit** — name the objection first: "You probably have a dozen vendors claiming AI expertise in your inbox." One sentence, never two.
- **Loss aversion** — real data only: "Every month the announcement sits unexecuted, the window the raise opened narrows."
- **"That's right" moments** — describe their situation so precisely (from the packet) that they agree before realizing it.
- **No-oriented questions** — "Would it be a terrible idea to…" / "Is this a bad time to bring up?" Safe to answer "no", and "no" moves the deal forward.

---

## Archetypes (pick ONE per sequence)

| # | Archetype | When | Structure |
|---|-----------|------|-----------|
| 1 | **observation** | Fresh angle in the packet (the usual case) | Signal noticed → what it implies → what peers do → "seeing the same?" |
| 2 | **creative_ideas** | You can generate 2–3 genuinely specific ideas from their signals | "Spent time on {{company}}" → tailored ideas → "any of these land?" |
| 3 | **referral_ceiling** | Founder-led, growth clearly word-of-mouth | Acknowledge growth → name the ceiling → contrast with predictable alternative → offer playbook |
| 4 | **problem_solution** | Well-understood pain, hard evidence in packet | Name problem → cost of inaction → our approach → gap-hinting CTA |
| 5 | **whole_offer** | Fallback only — no strong angle | One-line credibility → what we do → relevance bridge → "open to hearing more?" |
| 6 | ~~case_study~~ | **DISABLED — no citable proof points yet** | — |
| 7 | ~~benchmark~~ | **DISABLED — no citable proof points yet** | — |

Selection: fresh angle ≤90 days → **observation**. Multiple strong signals you
can turn into concrete ideas → **creative_ideas** (highest-converting when the
ideas are truly specific — that's why it works). Otherwise walk down the table.

---

## The 4-Step Sequence

Each step has ONE job. Never "just bumping this." Steps 2–4 have `subject: null`
(same thread). Day offsets are stamped by the pipeline — don't compute them.

**Step 1 — Opener (day 0).** SPARK, one archetype, 60–120 words. Lead with the
packet's primary angle. One question CTA (`confirm_problem`). No links.

**Step 2 — Value-add (day 3).** Write as if step 1 never happened. NEW angle
(a different packet angle, or the service's pitch angle). Binary question CTA
(`offer_deliverable`): offer the teardown / gap map / roadmap. P.S. line allowed —
prime real estate; use a packet fact, not fake social proof.

**Step 3 — Reframe (day 8).** Completely different frame: if 1–2 were the
problem, 3 is curiosity or a micro-commitment (`micro_commitment`) — a 2-minute
read, a one-page teardown, a single sharp question about how they're staffing it.

**Step 4 — Breakup (day 16).** Short, human, no guilt. Numbered options
(`breakup_options`) — they lower reply cost to a single keystroke, and "2" is a
future re-open:

```
Not trying to be a pest — checking one last time.

1. All set — not something you need help with
2. Timing's off — circle back in a few months
3. Wrong person — point me to who owns this?

No worries either way.

Uday
```

---

## Voice

A knowledgeable peer at a conference who did 5 minutes of real homework. Not a
marketer, not a robot, not a hungry SDR. Premium — we never sound like we need
the deal.

- Short sentences, 6–15 words.
- Every sentence gets its own paragraph.
- Contractions always.
- Plain text. No bold, no bullets in emails, no links in step 1, no images.
- Sign-off: first name only — "Uday". No signature block, no title, no company tagline.
- Channel-neutral phrasing: never "reply to this email" (some contacts get this via LinkedIn).
- Read it aloud. If it sounds like marketing, rewrite it.
- **Anti-robot rules** (batch-1 lesson): at most 2 em-dashes per email. Never
  "that kind of X usually/typically comes with/means/signals" — analyst voice.
  No consulting jargon: "execution window", "first-100-days agenda", "capability
  gap", "GTM velocity", "go-to-market motion". No day-precise dates or filing
  form names anywhere. Say it the way you'd say it across a table.

**Banned words** (deterministic QA gate — any hit fails the draft). Source of
truth: `config/settings.yaml` → `messages.banned_words` — edit the list there;
this copy is for human reading and must be kept in sync by hand.
leverage, utilize, streamline, comprehensive, robust, innovative, cutting-edge,
game-changing, revolutionary, disruptive, synergy, best-in-class, world-class,
next-generation, solution (say "system" or "approach"), excited to, passionate
about, thrilled, reimagine, transform (say "change" or "fix"), empower, elevate,
optimize (say "improve"), drive results, thought leader

**Anti-patterns** (instant rewrites): "Quick question…" · "I noticed that…" +
generic follow-up · "Just following up" · "Would it make sense to…" · "I'd love
to connect" · "As a [title], you probably…" · "I hope this finds you well" ·
"We're an AI-powered platform" · "Many companies like yours" · "Reaching out
because…" · "In today's competitive landscape" · "I'll be brief" · "Let me know
your thoughts" · "Can I get 15 minutes?" · our company name in the first line ·
more than one CTA per email.

---

## QA Checklist (the pipeline enforces most of this deterministically)

- Subject: 3–5 words, all lowercase, no special characters — step 1 only.
- Step 1 body 60–120 words; any body over 150 fails.
- First line specific to this prospect (not sendable to 1,000 people).
- Exactly one CTA per email, and it's a question. No meeting ask in steps 1–3.
- Every sentence its own paragraph. 3:1 you:we language ratio.
- No filing form names or calendar dates anywhere in the copy (hard fail).
- Value prop present: the email says what we do and what changes for them.
- Pattern proof present in steps 1–2.
- At most 2 em-dashes per email; no analyst-voice constructions.
- No banned words. No links in step 1. No `{{merge_variables}}` or
  `[bracketed placeholders]` — fully rendered text with the real first name and
  real company name.
- Every fact relied on traceable to the packet — but expressed as pain, not citation.
