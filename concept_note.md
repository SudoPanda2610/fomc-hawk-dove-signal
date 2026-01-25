# Concept note + interview questions

Kept deliberately separate from the build. This is the "can you explain it"
layer — the economics and the design choices behind the code.

## 1. What "hawkish" and "dovish" mean

A **hawkish** central bank leans toward *tighter* policy — higher rates, less
stimulus — usually because it is worried about inflation. A **dovish** bank leans
toward *easier* policy — lower rates, more stimulus — usually because it is worried
about growth or employment. Everything in this project uses one convention:
**positive = hawkish, negative = dovish, zero = neutral.**

## 2. Why the LLM only extracts, and never scores or predicts

Three jobs were possible: (a) read and classify the language, (b) turn that into a
number, (c) relate it to markets. The model is genuinely good at (a) — it is
reading comprehension over a controlled menu of categories — and I can check it by
reading the same statement myself. It is *not* trustworthy or defensible at (b) or
(c): a number it emits is unauditable, and "the LLM predicts yields" is the kind of
claim a good interviewer dismantles in one question. So (b) is a transparent
weighted sum I wrote, and (c) is a descriptive analysis with explicit caveats.

## 3. Level vs. change: the most important idea here

The price of a bond already reflects what the market *expects* the central bank to
do. So when a statement lands, the yield moves on the **surprise** — the gap between
what was said and what was priced in — not on the absolute hawkishness. A statement
can be objectively hawkish yet send yields *down* if markets expected even more.

Consequence for this project: my **level** index (how hawkish a statement is) should
correlate only weakly with the yield move. My **change** signal (how much the
language shifted vs the last meeting) is a rough proxy for surprise and should
correlate better. The synthetic check confirms the pattern (change rho ≈ 0.50 vs
level rho ≈ 0.22). The *correct* version of surprise would use pre-meeting market
expectations (fed-funds futures); I flagged that as a limitation rather than faking
it.

## 4. How the score is built (and why equal weights)

Six fields, each classified into a small enum, each mapped to an integer on a
symmetric scale, summed with equal weights, then rescaled to [-1, 1]. Equal weights
are a deliberate choice: the score has **no free parameters**, so I cannot be
accused of tuning it to fit the data (which, on ~80 statements, would be pure
overfitting). If someone wants to argue the rate decision or the forward guidance
should weigh more, that is a one-line change and I can show the conclusions barely
move.

## 5. How I validate the soft spot (the LLM)

Property tests cannot touch the LLM because it is not deterministic. So validation
is manual and small: I hand-label ~12 statements myself (assign each field from the
same menu), then compare to the model's extraction field by field. Two things make
this cheap: the model returns a **short supporting quote** per field, and every
extraction is **cached** so the comparison is stable. Agreement rate per field is
the honest headline; disagreements tell me which fields the schema is fuzzy on.

## 6. Why SQLite

The data is small and relational, I want to JOIN statements to their extractions to
their yields, and caching the LLM calls as rows makes the whole pipeline
reproducible and free to re-run. It also doubles as SQL practice.

---

## Interview questions (with the answers I'd give)

**Q: Isn't this just "LLM sentiment predicts the market"?**
No. The LLM only classifies language into a fixed schema, which I validate by hand.
The scoring and the market comparison are deterministic code. And I don't claim
prediction — I report descriptive associations and say plainly that the level
shouldn't even be expected to predict the move.

**Q: Why would the level correlate weakly but the change correlate better?**
Because markets price expectations in advance, so the move reflects the surprise,
not the level. The change in the Fed's language is a proxy for that surprise. (If
pushed: the *real* surprise measure uses futures-implied expectations, which I
didn't build — stated as a limitation.)

**Q: How do you know the LLM extraction is any good?**
Hand-labelled validation on a sample, field by field, plus a supporting quote per
field for spot-checking, plus caching for reproducibility. I'd quote the per-field
agreement rate.

**Q: Your N is ~80. Can you claim significance?**
No, and I don't. I report sign-agreement and rank correlation as descriptive, show
p-values for honesty, and treat the synthetic run — where I *know* a signal exists —
as the real evidence that the analysis code works.

**Q: Why equal weights? Aren't some fields more important?**
Equal weights mean zero free parameters, so no overfitting and nothing to tune away
from. Arguably the decision and guidance matter more; that's a one-line change and
the conclusions are robust to it. I preferred defensibility over a fitted score.

**Q: Why the 2-year yield and not the 10-year?**
The 2-year is the market's cleanest read on expected policy over the next couple of
years, so it is the most policy-sensitive tenor. The 10-year mixes in growth and
term premium. I store both; I analyse the 2-year.

**Q: What confounds the meeting-day move besides the statement?**
The press conference and the Summary of Economic Projections (the dot plot), which
often move rates more than the statement text. I scored the statement only, on
purpose, and say so — it explains only part of the day's news.

**Q: What would you do with more time?**
Add futures-implied expectations to measure true surprise; widen to more statements;
add a second hand-labeller to measure label agreement; and test whether the change
signal adds anything beyond just knowing the rate decision.
