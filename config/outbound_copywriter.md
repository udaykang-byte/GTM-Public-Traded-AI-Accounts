# Outbound Copywriter — martechs.io edition

<!--
  WHAT THIS IS: the copywriting framework the message-generation subagents follow.
  `pipeline messages --prepare` embeds this file verbatim into
  data/message_queue/_shared.json; every /outreach Haiku subagent reads it once
  per batch and applies it to each packet.

  EDIT LIKE settings.yaml: voice and copy decisions here are Uday's, same as
  thresholds. Adapted 2026-07-07 from the generic outbound-copywriter template;
  the customization interview below is FILLED, not a questionnaire.

  KEEP IN SYNC: the banned-words list also lives as BANNED_WORDS in
  src/pipeline/messages.py (the deterministic QA gate). Change both together.
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
metrics, client names, or case studies. Substitute: the prospect's own dated
events (angles), peer-story framing without named clients, the specificity of our
research itself, and risk-reversal offers ("no pitch attached").

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

Every claim, number, date, name, and quote in the copy must come from the
packet (angles, evidence quotes, why_now, company fields) or from this file's
service descriptions. If it isn't in the packet, it doesn't go in the email.
No invented peers, metrics, client results, or events. When in doubt, leave it out.

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
people?" test. Our openers come from the packet's primary angle: the filing,
the raise, the hire, the announcement — with its date.

Good: "Saw the $12M follow-on priced in March — that kind of dry powder usually
comes with a mandate to show progress fast."
Good: "Noticed LifeMD announced a broad-AI push on May 6 and installed a new
CMO the same quarter. That combination usually means a first-100-days agenda."
Bad: "I came across your company and was impressed by your growth."
Bad: "I hope this email finds you well."

### A — Agitate
Name the specific pain in their language, 2–3 sentences max. Agitate the cost
of inaction, not the problem itself. Tie it to their profile: laggards feel
board/investor pressure without an owner; adopters feel execution lag behind
their announcement. The reader should think "that's exactly my situation."

### R — Relevant value (adapted: no proof points)
One concrete mechanism or idea — not features, not a product tour, and never an
invented result. Three allowed moves:
- **Specific idea**: one tailored thing we'd do for them, drawn from their signals ("your support volume + no AI in product = an agent on your own help data is the obvious first win").
- **Peer story, unnamed**: "Most micro-cap health platforms we look at are running outbound fully manual" — pattern language, no fake clients, no fake numbers.
- **Risk reversal**: a free, no-pitch deliverable — teardown, gap map, first-step roadmap.

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
2. **Peer story > generic discovery** — "Most [sector] teams at this size…" beats "I found your company on…". Unnamed patterns only — no invented companies.
3. **"Reason why" framing** — explain why this email exists: "The reason I'm writing: your 8-K reads like there's no internal owner for this yet."
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

**Banned words** (deterministic QA gate — any hit fails the draft):
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
- At least 2 power patterns applied.
- No banned words. No links in step 1. No `{{merge_variables}}` or
  `[bracketed placeholders]` — fully rendered text with the real first name and
  real company name.
- Every number/date/name traceable to the packet.
