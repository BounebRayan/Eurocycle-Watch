# GPAO parity

_How the dashboard's **Activity report**, **Landed cost** and **Re-quotation**
tabs relate to the Eurocycles GPAO: what ties exactly, what doesn't and why, and
the thirteen places the GPAO's own arithmetic does not hold up._

Ported so far: `frmActiviteComp1` (§1-4), `frmEtatOFValorises` and
`frmEtatOFValorisesTrans` (§5), `frmAnalyseCoutMatNC` (§6) and
`frmConsultPrixNC` (§7). Still outstanding: §8.

Date of this pass: 2026-09-21.
GPAO source: `d:\Source code\Eurocycles\Eurocycles.sln` (VB.NET, DevExpress).
**The GPAO source is read-only for this project — nothing in it was modified.**

---

## 1. Which GPAO screen this is

Management reporting lives in two folders of the GPAO:

| Folder | What's in it |
|---|---|
| `09-Managements reports` | ~97 operational report screens — stock, needs planning, order feasibility, BOM states, production recaps |
| `10-Financier/05-Direction generale` | ~39 management screens — comparative activity, cost analysis, annual tables, exchange-rate variation, valued production orders |

The flagship is **`10-Financier/05-Direction generale/frmActiviteComp1.vb`**,
captioned _"Activities comparatives"_. It is the screen that produced
`data/finance/erp-report-ytd-2026-09-21.xlsx`, and it is what this tab ports.

It takes a date window, compares it against **the same window shifted back
exactly one year** (`datedeb.AddYears(-1)` — not against a calendar year), and
reports nineteen sections:

| # | Section | Grain | Source |
|---|---|---|---|
| 01 | Chiffre d'affaire par type de vélos | wheel size | `facture ⋈ facture_det ⋈ nomachat ⋈ Wheelsize` |
| 02 | Chiffre en nombre par type de vélos | wheel size | same, units |
| 03 | Prix moyen par type de vélos | wheel size | same, revenue ÷ units |
| 04 | Chiffre d'affaire par client | invoiced customer | `facture ⋈ facture_det ⋈ client` |
| 05 | Chiffre en nombre par client | invoiced customer | + `nomachat ⋈ Wheelsize` |
| 06 | Chiffre d'affaire par pays | customer country | `⋈ client ⋈ payss` |
| 07 | Chiffre en nombre par pays | customer country | `⋈ nomachat ⋈ client ⋈ payss` |
| 08 | Chiffre en nombre (e-bike) | wheel size | `nomachat.ebike = 1` |
| 09 | Commandes clients par pays | country | `planningprev ⋈ planningprev_det` |
| 10 | Commandes par client | customer | same |
| 11 | Commandes par famille de vélo | wheel size | same |
| 12 | Achats par pays d'origine | part origin | `facturef ⋈ facturef_det ⋈ fpiece` |
| 13 | Achats par fournisseur | supplier | `⋈ fournisseur` |
| 14 | Achats par groupes | part group | `⋈ fpiece ⋈ groupes` |
| 15 | Consommation par pays d'origine | part origin | `calc.detart` (DEC_PROD) ⋈ BOM ⋈ `devisesc` |
| 16 | Consommation par fournisseur | supplier | same |
| 17 | Consommation par groupes | part group | same |
| 18 | Transport import | charge heading | `transportf (imp = 1) ⋈ transportdf ⋈ rubriquest` |
| 19 | Transport export | charge heading | `transportf (imp ≠ 1)` |

Section 03 is computed by the GPAO but **excluded from its bulk export** — the
export only walks the sections it merges into the combined grid, and 03 is
reachable only by selecting it. That is why the spreadsheet jumps 02 → 04.

The port is `gpao_activity.py`; the tab is `views/management/activity.py`.

### Measures, verbatim

```
revenue      = SUM(facture_det.qte * facture_det.prx * facture.cours)     -- DT
units        = SUM(facture_det.qte)
purchases    = SUM(facturef_det.qtedfctf * facturef_det.prxdfctf * facturef.coursfctf)
freight      = SUM(transportdf.mntdf)                                     -- already DT
consumption  = SUM(ordprevision.qte * ordprevision_det.qtendach
                   * ordprevision_det.prxndach * <devisesc rate at order date>)
```

Two filters recur and matter:

- `facture.flag <> 1` — cancelled invoice. **Every** sales section applies it.
- `ISNULL(facture.cndrgl,'') <> 'WITHOUT COMMERCIAL VALUE'` — free-of-charge /
  sample invoices. The GPAO calls this `conditionsPai` and applies it to **some
  sections and not others**. See defect 1.

### The rebate block

Sections 01, 04 and 06 subtract a **Remise** computed once for the whole report:

```sql
SUM(DF.qte*DF.prx*F.cours - DF.qte*DR.nprix*F.cours)
FROM facture, facture_det, nomachat, Wheelsize, facture_remise R, facture_remise_det DR
WHERE F.datf BETWEEN R.datedeb AND R.datefin AND DR.cod = DF.article AND F.clif = R.client
```

i.e. the gap between what was invoiced and the negotiated price held in
`facture_remise_det.nprix` for that customer over that date range.

> **This corrects an earlier finding.** `docs/eurocycles-erp-findings.md` §12b
> dropped "discounts given" because `facture.trem` is unpopulated. That was the
> wrong column — the GPAO never reads `trem`. Rebates live in
> `facture_remise` / `facture_remise_det`. On the 2026 YTD window the block
> evaluates to 0, so no figure moves today, but the mechanism is now wired.

---

## 2. What ties

Run against `(localdb)\MSSQLLocalDB` on 2026-09-21, window 2026-01-01 → 2026-09-21
versus 2025-01-01 → 2025-09-21 — the exact window the committed spreadsheet was
exported for.

**15 of the 18 exported sections match the GPAO's own spreadsheet to the cent**,
both years:

| Section | Ours (current) | Spreadsheet | Δ |
|---|---:|---:|---:|
| 01 revenue by wheel | 41,894,202.57 | 41,894,202.57 | **0.00** |
| 02 units by wheel | 136,426 | 136,426 | **0** |
| 04 revenue by customer | 41,895,159.07 | 41,895,159.07 | **0.00** |
| 05 units by customer | 136,421 | 136,421 | **0** |
| 06 revenue by country | 41,893,689.68 | 41,893,689.68 | **0.00** |
| 07 units by country | 136,421 | 136,421 | **0** |
| 08 e-bike units | 366 | 366 | **0** |
| 09/10/11 customer orders | 180,879 | 180,879 | **0** |
| 12 purchases by origin | 28,240,784.97 | 28,240,784.97 | **0.00** |
| 13 purchases by supplier | 28,652,018.84 | 28,652,018.84 | **0.00** |
| 14 purchases by group | 28,240,784.97 | 28,240,784.97 | **0.00** |
| 18 inbound freight | 1,615,676.64 | 1,615,676.64 | **0.00** |
| 19 outbound freight | 483,085.54 | 483,085.54 | **0.00** |

`tests/test_gpao_activity.py` pins this: it reads the committed spreadsheet and
asserts each section's TOTAL row to within a cent. A tidy-up that "simplifies" a
join will fail it.

### The three that don't tie — and why it isn't the port

Sections 15-17 (consumption) come out **23,898,765.69** against the
spreadsheet's **30,162,342.32** for the current year.

The port is not the problem. Running **the GPAO's own SQL, character for
character**, against this database today returns 23,898,765.69 — the same
number. The cause is the data:

```
facture           max datf         2026-08-19
facturef          max datlivfctf   2026-08-19
calc.detart       max dat          2026-06-30   ← seven weeks short
```

`eurocycles_db_calc` is restored separately from `eurocycles_db`, and this copy
of it ends two months earlier. Sections 15-17 are the only three that read it,
so they are the only three that come up short. The tab warns about this
explicitly whenever the window runs past the ledger's last movement, rather than
showing a quietly low number.

The prior year is short by only 0.24 % (51,633,038 vs 51,754,983) — 2025 is fully
present in `detart`, so that residual is retroactive adjustment, not coverage.

---

## 3. Where the GPAO's arithmetic doesn't hold up

Defects 1-4 are in the Activity report and are set out below; 5-7 are in the
landed-cost screens (§5), 8-11 in the re-quotation (§6) and 12-13 in the price
list (§7). All reproduce against the ERP, and 1-4 against the GPAO's own export
too. The dashboard **reproduces them** so the numbers still tie, and says so on
the page.

### 1. Three different revenue totals for one period

Sections 01, 04 and 06 are all captioned _chiffre d'affaire_ and all three
disagree, because each joins and filters differently:

| | wheel-size join | free-of-charge filter | PIECES DIVERS bucket | 2026 YTD |
|---|---|---|---|---:|
| 01 | yes | **yes** | yes | 41,894,202.57 |
| 04 | no | **no** | no | 41,895,159.07 |
| 06 | no | **yes** | no | 41,893,689.68 |

Same for units: 02 reports **136,426** where 05 and 07 report **136,421** —
section 02 is the only sales section with no free-of-charge filter at all.

The free-of-charge population is small in this window (2 invoices, 11 units,
DT 1,469), so the totals differ by hundreds rather than millions. The problem is
not the size, it is that a reader comparing section 01's total to section 04's
cannot tell whether a difference is real.

### 2. Average price per bike divides two different populations

`MOYENNE PAR VELO` is section 01's revenue over section 02's units:

- numerator: free-of-charge excluded, PIECES DIVERS **included**
- denominator: free-of-charge **included**, PIECES DIVERS excluded

```
GPAO       41,894,202.57 / 136,426 = 307.0837   (matches the export exactly)
one basis  41,898,981.90 / 136,421 = 307.1300
```

The gap is small here because the mismatched populations are small. It is not
bounded — the method is wrong regardless of what it happens to produce this
quarter. The tab shows the GPAO figure and carries the one-basis figure in the
tile's tooltip.

### 3. The price-change percentage is rounded before it is divided

`frmActiviteComp1.vb`, the MOYENNE PAR VELO row:

```vb
drTot(4) = (Math.Round((totMoyParVelo1 / totYear1) - (totMoyParVelo2 / totYear2)) _
            / Math.Round(totMoyParVelo2 / totYear2)) * 100
```

Both the numerator and the denominator are rounded to whole dinars **before** the
division:

```
GPAO     Round(307.0837 - 317.4211) / Round(317.4211) = -10 / 317 = -3.1546 %
correct        (307.0837 - 317.4211) /      317.4211            = -3.2567 %
```

The spreadsheet prints `-3.1545741324921135`, which is the first line exactly.
The error is **0.10 pp — 3.1 % of the reported figure**. It grows as the change
gets smaller: a genuine change of DT 0.4 would be reported as 0.

The same row also writes the variance one column to the left of where every
other row puts it, so in the export the "Pourcentage" column holds the variance
percentage and the "Ecarts" column holds the absolute variance.

### 4. Section 01 double-counts a line into two buckets (immaterial)

Its wheel-size half carries **no `typ` filter**, while its PIECES DIVERS half
takes `typ NOT IN ('O','I')`. A non-bike line whose article still resolves to a
wheel size therefore lands in both.

Measured on the 2026 YTD window: **1 line, DT 512.89**. Real, but not worth
acting on — recorded so nobody re-derives it.

---

## 4. What was recommended and what was done

The GPAO source was **not modified** — that was the instruction, and it is also
the right call: the GPAO's numbers are what the business has been steering on,
and changing them mid-year would break comparability with everything already
circulated.

The dashboard therefore **reproduces the GPAO exactly and annotates it**:

| Defect | Handling |
|---|---|
| 1 — three revenue totals | All three shown as the GPAO computes them; each section's caveat names its own filter set; the expander lays out the comparison |
| 2 — mixed-population ASP | GPAO figure on the tile; one-basis figure in the tooltip, via `gpao_activity.Asp` |
| 3 — rounded percentage | GPAO figure on the tile; `Asp.true_delta_pct` gives the correct one, and a test asserts the two differ |
| 4 — double-counted line | Reproduced; documented here only |
| 5-7 — freight coefficient | See §5; both freight readings carried, ties broken deterministically |
| 8 — unpriced parts scored as zero | Both readings carried everywhere (`ecart_pct` and `ecart_lfl_pct`); the tab leads with the gap; the Actions rule ranks on like-for-like |
| 9 — unused quote currency | Reproduced; the 252 conflicting parts are surfaced as a data-quality queue rather than converted |
| 10-11 — sale price and its tie | Reproduced, with the tie broken on `datf, numf, prx` so the figure holds still between runs |
| 12 — freight counted twice | Kept (it is the pricing convention); named on the tab, and `price_dt_single` carries the one-leg reading |
| 13 — rebate base differs | Both derivations returned; `price_dt` is the authoring screen's, because that is what a human approved |

If the business wants the corrected figures promoted to the headline, that is a
one-line change in `views/management/activity.py` (`asp.gpao_*` →
`asp.corrected_*`) — but it should be a deliberate decision, because from that
point the dashboard stops agreeing with the GPAO's own printout.

---

## 5. Landed cost — `frmEtatOFValorises` / `frmEtatOFValorisesTrans`

_Added in the second pass. Tab: **Landed cost**. Module: `gpao_landed.py`._

### What these screens add that nothing else has

Every other margin figure in the dashboard is **margin over `facture_det.mat`** —
standard material cost, which carries no packaging, no paint and **no inbound
freight** (findings §12d). The GPAO's own answer to that gap is
`facturef_det.coef`: the ancillary charges on a supplier invoice, spread per unit,
and added to a part's cost wherever the GPAO wants a real landed figure
(`frmFactFour.vb` reads it back as `prxdfctf·coursfctf + coef`).

`frmEtatOFValorisesTrans.vb` applies it to a production order: walk the bill of
materials, price each component at the FX rate current on the order's date, add
each component's coefficient. Its headline line is
`TotGen+Transp / Prix de vente (%)` — landed build cost as a share of the sale
price.

**The coefficient, verbatim** (`08-Transport/frmDroitDouane.vb`, also
`frmFactTransport.vb`):

```
totFrais  = Σ (transit + femb + fdae + fsa + ass + mag + frsta + tax + trs)   -- DT
totQte    = Σ qtedfctf  where prxdfctf >= 0
totPrxNeg = |Σ (qtedfctf · prxdfctf) · coursfctf|  where prxdfctf < 0          -- credit lines

coef = 0                                              if totQte = 0
     = (totFrais − (totPrxNeg · 100 / totFrais)) / totQte   if totPrxNeg > 0 and totFrais > 0
     = 0                                              if totPrxNeg > 0 and totFrais <= 0
     = totFrais / totQte                              otherwise
```

Stamp duty (`tim`) and `autres` are deliberately outside the pool.

### Parity

There is no committed spreadsheet for these screens, so parity is pinned against
something better: `coef` is a value the GPAO **wrote into the database**.
Recomputing it from `facturef`'s charge columns and landing on the stored number
proves the port.

| | |
|---|---:|
| Supplier invoices since 2024 with a stored coefficient | 1,876 |
| Reproduced exactly | **98.67 %** |
| Reproduced within DT 0.01 | **98.88 %** |
| Worst gap | DT 228.84 |

The residual is invoices whose charges were edited after the coefficient was last
written — the ERP only recomputes it when the customs or transport screen is
saved, so a stale row is expected.

### Execution: same rule, different plan

The GPAO does its as-of lookups (FX rate at the order date, latest coefficient
before the order date) as **correlated subqueries per BOM line**. Over 2026 H1
that is 118,895 lines and **351 s**.

`gpao_landed.py` pulls the BOM lines, the coefficient history (96k rows) and
`devisesc` separately and does both as-of joins with `pandas.merge_asof`:
**1.6 s**, a 220× cut, same rule. This mirrors what `load_sales` already does for
wheel size and country (findings §12d) — the keys are not indexed for these joins,
so SQL Server is the wrong place to do them.

### The payoff

Joining valued invoice lines to their own order's costed BOM
(`facture_det.ofnach` → `ordprevision.ofdnach`) matches **99.3 % of lines and
99.7 % of revenue**. That gives margin after inbound freight:

| Jan–Jun 2026 | |
|---|---:|
| Margin over material (what the Overview shows) | **31.9 %** |
| less inbound freight | −4.6 pp |
| **Margin after freight** | **27.3 %** |
| Accounting gross margin, from the P&L pack (findings §12d) | **26.9 %** |

**The freight coefficient closes almost the whole gap** findings §12d could not
explain — 31.9 % against an accounting 26.9 %, and putting inbound freight back
lands at 27.3 %, within 0.4 pp. The remaining sliver is packaging, paint and
timing, exactly as that section predicted.

### Three more defects

**5. The coefficient subtracts a percentage from an amount.** When an invoice has
credit lines the GPAO computes `totFrais − (totPrxNeg × 100 / totFrais)`. The
middle term is a *ratio* being subtracted from *dinars*; the intent was plainly
`totFrais − totPrxNeg`. Because the stray term carries `/ totFrais`, the error
grows as the charge pool shrinks.

Of 1,876 supplier invoices since 2024, **286 carry credit lines and 41 end up
with a negative coefficient** — parts that *subtract* cost from every order that
uses them. Worst case, invoice `AB23-EURO-CYCLES005`: a DT 29 pool over 2 units
becomes **−81.35 DT per unit**. The ERP stores that figure.

**6. Freight is not scaled by how many of a part a bike uses.** For each component
the GPAO takes price × `qtendach` but freight as a bare `coef`. A bike using four
of a part carries four times its material and once its freight. The two readings
differ on **every** order in the window; across 2026 H1 they give 10.5 % versus
8.7 % of material. The tab shows both.

**7. The coefficient lookup is not reproducible.** `TOP 1 … ORDER BY datlivfctf
DESC` has no tiebreaker, so when a part was delivered twice on one day the answer
is whichever row SQL Server returns. **1,495 of 88,095 part-days (1.7 %) carry
more than one coefficient**, mean spread DT 5.48, worst **DT 2,704**. The
dashboard breaks the tie on the highest invoice number so a figure does not move
between refreshes; this is the one place it cannot be bit-identical to the GPAO,
because the GPAO is not bit-identical to itself.

### Two filters worth knowing about

`frmEtatOFValorises` drops every line with `qte <= 1`. Its own source carries the
note _"TODO ADD ECHANTILLON FROM FACTURE AND DELETE DF.[qte] > 1"_ — it is a
stand-in for a sample flag that was never wired up, and it also drops genuine
one-off sales (49 lines, DT 61,366 over 2026 H1). The tab reports the number
rather than hiding it.

It also filters `DF.typ = 'O'` only, where the Activity report needs `'O'` **and**
`'I'` (findings §12c). So this screen's revenue is deliberately not the Activity
report's revenue, and the two should not be compared line for line.

---

## 6. Re-quotation — `frmAnalyseCoutMatNC`

_Added in the third pass. Tab: **Re-quotation**. Module: `gpao_requote.py`._

### What the screen does

For each live model, walk the bill of materials twice:

```
cost    = Σ  nomachat_det.prxndach · qtendach · cours     what the model was costed at
NVcost  = Σ  fpiece.prxcmdf        · qtendach · cours     what those parts cost to buy now
Ecart   = NVcost − cost
```

`cours` is the rate for the **part's purchase currency** (`fpiece.Devfpiec`) at
**today's** date — the same rate on both legs, so the comparison isolates price
movement rather than FX. Scope is `nomachat.isArchived = 0` plus an `EXISTS`
against `facture_det` for the selected year.

It then attaches a sale price (`getInfoFacture`) and reports the cost movement
as a share of it — the GPAO's `Ecart (Ecart/PV) %`, which is the closest thing
the ERP has to a re-pricing signal.

### Parity

There is no export for this screen either, so parity is pinned the same way §5
was: **the GPAO's own SQL, run character for character**. `getListe` builds one
query string; `tests/test_gpao_requote.py::test_requote_reproduces_the_gpao_query`
runs that string verbatim and asserts the port matches it per model, to the cent.

| | |
|---|---:|
| Models in scope, 2026 | 749 |
| BOM lines walked | 47,609 |
| Models reproduced to the cent | **749 / 749** |

The port is also ~40× faster on the sale price: the GPAO calls `getInfoFacture`
**once per row** inside its render loop, where this does the whole book in one
`ROW_NUMBER()` pass.

### Four more defects

**8. A component with no current price is re-quoted at zero.** The new
quotation is `ISNULL(FP.prxcmdf · DN.qtendach, 0)`, so a part whose `prxcmdf`
was never filled in contributes **nothing** to the new cost while contributing
its full stored cost to the old one. The screen then reports a saving that is
really a hole in the part master.

This is the one that matters. It is not a rounding error — it changes the sign
of the answer:

| 2026 | GPAO | like for like |
|---|---:|---:|
| Material cost movement | **−1.94 %** | **−0.36 %** |
| Models reported cheaper to build | 537 | 411 |

**1,445 of 47,609 components (3.0 %)** carry no current price, holding DT 3,680
of stored cost that the new quotation scores as zero. **126 of 749 models are
painted as a saving when they are a rise.** The proportion is stable across
years — 3.1 % on 2023, 3.2 % on 2024 — so this is a standing condition of the
part master, not a one-off.

The tab shows both readings side by side, and the Actions tab's re-pricing rule
ranks on the like-for-like one. Ranking on the GPAO's figure would put real cost
rises below imaginary savings.

**9. The quote currency is recorded but never used.** `fpiece` holds the current
order price in `prxcmdf` and its currency in `devcmdf`, but `frmAnalyseCoutMatNC`
never selects `devcmdf` — it converts the new quotation at the rate for
`Devfpiec`, the part's *purchase* currency. Where the two disagree the new price
is converted at the wrong rate. **252 live parts** are in that state.

**Honouring `devcmdf` would be worse, and this is worth being explicit about,
because it is the tempting fix.** On 87 of those 252 parts the *number did not
change when the currency label did*:

| Part | Purchase | Quoted | Ratio |
|---|---|---|---:|
| `CN18220.1` CHAIN | 61,923 YEN | 61,923 **USD** | 1.00 |
| `FR18975.2` FRAME | 3,333 DT | 3,333 **EUR** | 1.00 |
| `STK14976.1` STICKERS | 1.85 DT | 1.85 **EUR** | 1.00 |

That is a mis-set dropdown, not a re-denomination. Converting the chain at the
USD rate would value one chain at **182,000 DT**. A first pass at this analysis
applied the conversion across the board and produced a **+47 % re-quotation**
against the GPAO's −1.94 %; almost all of it came from 41 BOM lines carrying
that one chain. The dashboard therefore reproduces the GPAO and lists the 252
parts as a **data-quality queue** on the tab. Neither reading of their cost is
right until they are fixed at source.

**10. The sale price is not the price that was invoiced.** `getInfoFacture`
takes the most recent invoice for the article **ever** — not one inside the
report's year, with no cancelled-invoice filter — and converts it at **today's**
rate rather than the invoice's own `facture.cours`.

Both halves bite. Run the report for a past year and the variance is measured
against a price from *after* it: **60 % of models on a 2023 run, 48 % on 2024**.
And **469 articles** have a cancelled invoice (`flag = 1`) as their most recent.
Its currency CASE also covers only `EUR` and `USD`, falling through to `ELSE 1`
— but no invoice in this database is in yen, so that branch is latent, and the
doc records it rather than claiming an impact it does not have.

**11. The "last invoice" lookup is not reproducible.** `TOP 1 … ORDER BY
F.datf DESC` has no tiebreaker, and the tie arises at two levels: several
invoices can share a model's last date, **and a single invoice can carry the
same article on more than one line at different prices — 41 articles do**. The
second level is the one that caught this port out: an ordering of
`datf DESC, numf DESC` still returned different prices on two consecutive runs.
The dashboard orders by `datf, numf, prx`, which is fully determined.

---

## 7. The price list — `frmConsultPrixNC`

_Same pass, same tab and module._

### There is no stored price in this ERP

`costing_nc` holds one costing sheet per model per revision (`inddf`) — material
split by currency with the rate used, labour, charges, margin rate, two
commissions, rebate, eco-contribution and sale freight. It holds **no price
column**. Every screen that shows a price re-derives it on read:

```
totGDT1 = tusd·cusd + teur·ceur + tyen·cyen + tdt        material, DT
totGDT2 = mov·cmov + cha·ccha + trs·ctrs                 labour, charges, inbound
totG1   = totGDT1 + totGDT2
marge   = totGDT1 · marge/100          ← on material only, not on totG1
com     = totG1·com1/100 + totG1·com2/100
fob     = trsv · ctrsv                                   sale freight
```

| Screen | Price, DT |
|---|---|
| `frmCostingTMP3` (where the sheet is authored) | `(totG1 + marge + fob + com)·(1 + rebait/100) + Eco + fob` |
| `frmConsultPrixNC` (the price list) | `(totG1 + marge + fob + com) + (totG1 + marge + com)·rebait/100 + Eco + fob` |

`frmConsultPrixNC_report1/2/3` swap `trsv·ctrsv` for `trsvd·ctrsvd` on a DDU
selection but are otherwise the price-list expression.

### The payoff: findings §4's missing sell price

`docs/eurocycles-erp-findings.md` §4 flagged that the dashboard has no
trustworthy sell price, because `nomachat.prxnach` is stale. Measured against
realised average selling price for 2026, over the **283 models carrying both**:

| | median abs. error | within ±10 % |
|---|---:|---:|
| Price derived from `costing_nc` | **7.1 %** | **60 %** |
| `nomachat.prxnach` | **35.3 %** | **2 %** |

So the costing sheet is a usable list price and `prxnach` is not. **Its weakness
is reach, not accuracy**: only **314 of the 704 models invoiced in 2026 (45 %)**
have a costing sheet at all, and a further 80 live models are dropped from the
price list by its inner join to `packing`.
`tests/test_gpao_requote.py::test_the_price_list_beats_prxnach` pins the claim,
and a companion test pins the coverage caveat so it cannot quietly go stale.

### Two more defects

**12. The sale freight leg is counted twice — deliberately.** Both derivations
add `trsv · ctrsv` once inside the sub-total and again at the end. It is not an
oversight: `frmCostingTMP3` recomputes its whole build-up a second time inside a
block its own source brackets with

```vb
'BEGIN ADD 2 fois FOB IN Tot2 ======*******************************************
txtTotG2.EditValue = CDbl(txtTotG2.EditValue) + CDbl(txtTotTV1.EditValue)
```

So it is a pricing convention, and the dashboard keeps it. The problem is that
**nothing on the price list says so** — a reader comparing a list price against
a freight quote will not find the second leg. It is worth a median **1.34 %** of
price and more than 5 % on **99 of 10,934** models. The tab names it, and
`price_dt_single` carries the one-leg reading for anyone who needs to reconcile.

**13. The two screens disagree about the rebate.** `frmCostingTMP3` applies
`rebait` to a base that includes the first freight leg; `frmConsultPrixNC`
applies it to one that excludes it. So the price a costing sheet was signed off
at and the price the list reports differ by `trsv · ctrsv · rebait/100`.

Measured, this is **immaterial**: `rebait` is non-zero on only 897 of 24,442
costing rows, and on current revisions the two prices differ on **2 models of
10,934**, worst gap **DT 11.15**. It is recorded rather than acted on. What it
shows is structural, though — because the price is a derivation rather than a
stored value, "the price" depends on which screen you ask, and nothing in the
ERP reconciles them. `load_price_list` returns both, and `price_dt` is the
authoring screen's, because that is the figure a human approved.

---

## 8. Not yet ported

Three passes have now ported `frmActiviteComp1`, the two valued-order screens,
`frmAnalyseCoutMatNC` and `frmConsultPrixNC`. The GPAO has substantially more
management reporting that the dashboard does not yet carry. The largest
remaining blocks, in rough order of likely value:

| GPAO screen | What it adds |
|---|---|
| `frmExchangeRate` | Exchange-rate variation and its effect on cost — the natural next one, since §6 deliberately holds FX constant to isolate price movement |
| `frmTabAnCAF` / `frmTabAnCAFFour` | Annual CAF tables, customer and supplier |
| `frmTabCompAnnuel` / `…Grp` | Multi-year comparative tables, by group |
| `frmAnalyseStock` / `frmStockTheorique` | Stock analysis, theoretical stock by group |
| `frmStatistiques` | The GPAO's own statistics screen |
| `09-Managements reports/02-Stock` (~30 screens) | Needs planning, safety stock, inventory variance |
| `09-Managements reports/05-Etat des nomenclatures` | BOM states — ordered, invoiced, produced, valued |

`frmActiviteComp` and `frmAnalyseComparative2` are **older variants of the same
report** (14 sections instead of 19, no e-bike or consumption cuts). They need no
separate port. `frmAnalyseCoutMatNCModal` — the OF-to-OF comparison behind the
re-quotation grid's other columns — was read but not ported: it compares the
last order invoiced in year *N−1* against the last in year *N*, and when a model
was not invoiced in both it silently compares an order with itself. Its
per-currency totals are also summed with the FX conversion commented out. The
component-level question it answers is better served by `load_requote_lines`,
which §6 ports instead.

### One correction to §8 as it stood before this pass

The previous edition of this table described `frmConsultPrixNC` as "the stored
price list per model (`prxnom` / `tarif_det`)". That was wrong on both counts.
`prxnom` is only a per-customer *selection* flag — which models appear on that
customer's list — and `tarif_det` is not read by the screen at all. The pricing
engine is `costing_nc`, and as §7 sets out it stores no price, only the inputs
to one.
