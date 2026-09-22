# GPAO parity

_How the dashboard's **Activity report**, **Landed cost**, **Re-quotation**,
**Exchange rate** and **Build from stock** tabs relate to the Eurocycles GPAO:
what ties exactly, what doesn't and why, and the twenty-two places the GPAO's
own arithmetic does not hold up._

Ported so far: `frmActiviteComp1` (§1-4), `frmEtatOFValorises` and
`frmEtatOFValorisesTrans` (§5), `frmAnalyseCoutMatNC` (§6),
`frmConsultPrixNC` (§7), `frmExchangeRate` (§8) and `frmStockADate` (§10).
Still outstanding: §9.

§10 also carries the one piece of this dashboard that is **not** a port —
`bike_builder`, which proposes buildable bikes from the unmoved stock §10
reports. It is marked as such there and on the tab itself.

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

The port is `gpao_activity.py`; the tab is `views/management/activity.py`, on the **Commercial** page.

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

The tab's **Excel export** carries the same chain one step further: it writes the
whole report to a single `.xlsx`, a sheet per section, exactly as
`frmActiviteComp1` does. Checked against the same window, the exported cells are
the port's own figures to the cent — 01 at 41,894,202.57, 02 at 136,426, 12 at
28,240,784.97 — so GPAO export, port and our workbook all agree. It differs in
one respect only: the first sheet records the window, the filters and the
caveats, including that these are the GPAO's queries with its defects intact.

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

The tab shows both readings side by side, and the Actions page's re-pricing rule
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

## 8. Exchange-rate variation — `frmExchangeRate`

_Added in the fourth pass. Tab: **Exchange rate**. Module: `gpao_exchange.py`._

### Why this one came next

§6 prices both legs of the re-quotation at today's rate precisely so the
comparison isolates price movement rather than FX. The Actions page had no such
protection. Its erosion rule reads booked invoices at booked rates and told the
reader that steady volume "leaves price or cost" — but every sale Eurocycles
makes is invoiced in EUR or USD (DT invoices are **5 of 2,213** over 2024-26)
while `facture_det.mat`, the cost side, is stored in dinar. `line_rev_dt` is
`qte · prx · facture.cours`; `line_cogs_dt` is `mat`. The two sides of the
margin do not move together when the rate does, so a model sold at an unchanged
foreign price, to the same customer, in the same volume, still loses dinar
margin. Measured on 2025→2026, that was **a third of the money the rule was
sizing**.

### What the screen does

Take every non-cancelled EUR/USD invoice in a window, group by customer ×
currency × calendar month, and revalue the same foreign-currency total twice:

```
Cours (2) = the month's own average devisesc rate
Cours (1) = the previous month's average        (January: the 31/12 rate)
Ecart     = TotalDev · Cours(2)  -  TotalDev · Cours(1)
```

Volume and price are identical on both legs, so the whole `Ecart` is exchange
rate. Two grids — by customer and by currency — plus a TOTAL band that revalues
the year at the closing rate against the opening one. Filters are
`facture.flag <> 1`, `dev IN ('EUR','USD')` and the `WITHOUT COMMERCIAL VALUE`
exclusion, which this screen applies unconditionally — unlike §1's, see defect 1.

### Parity

No export for this screen either, so parity is pinned as §5-§7 were: **the
GPAO's own SQL, run character for character**. `_gpao_sql` holds the query
`GetListAchatsALL` builds; `_ecart` reproduces the VB's per-cell arithmetic.
`tests/test_gpao_exchange.py` ties the turnover to the invoice book, the monthly
rate to `SUM(x)/COUNT(x)` over `devisesc` and January to the 31/12 scalar, and
checks the two grids agree once the customer is summed out.

| 2026 | EUR | USD |
|---|---:|---:|
| Turnover | 3,872,750 € | 9,874,577 $ |
| Sum of the 12 monthly `Ecart` columns | DT 10,295 | DT 86,767 |
| TOTAL band `Ecart` | **DT −18,589** | **DT 356,472** |

The last two rows are the same grid disagreeing with itself. See defect 14.

### Four more defects

**14. The monthly columns and the TOTAL band measure different things.** Each
month's `Ecart` compares that month's sales against the *previous month's* rate
— twelve independent month-on-month movements. The TOTAL band beside them
revalues the **whole year's** volume at the closing rate against the opening
one. Summing the columns does not give the total the grid reports next to them:
on 2026 the two readings are 4× apart on USD and **opposite in sign on EUR**, so
a reader who totals the row gets a number the screen itself contradicts. Both
readings are reproduced and shown side by side rather than reconciled, because
neither is wrong on its own terms — they answer different questions, and the
screen never says which one it is answering.

**15. Month columns are mislabelled unless the window starts in January.** The
grid captions its bands by walking forward from the start date
(`datedeb.AddMonths(j)`) while the data is filed by calendar month
(`DATEPART(Month, datf)`). Start the window in March and March's figures land in
the column captioned MAY, with the first two columns empty. The screen opens on
1 January, so the default view is correct and this only fires when someone moves
the date — which the screen invites, since the date pickers are its main control.

**16. Neither leg uses the rate the invoice was booked at.** Both legs are
priced from monthly averages of `devisesc`, while `facture.cours` — the rate
every revenue figure in the GPAO and in this dashboard is computed from — sits
on the invoice itself. They track closely (median gap **−0.12 %**) but **94 of
1,253** invoices sit more than 1 % apart, so this screen's dinar totals cannot be
reconciled against any other screen's revenue. This is why `fx_split` does not
use this screen's arithmetic at all.

**17. A month with no quoted rate is priced at zero, not skipped.** The rate
subqueries end `ELSE 0`, so a gap in `devisesc` would revalue that month's sales
at nothing and report the entire turnover as exchange variation — while the
`Ecart (%)` guard returns a tidy 0 % beside it. `devisesc` carries a row for
every day of 2024-26, so this is latent here rather than measured.

One thing that looks like a defect and isn't: the month averages are written
`SUM(x)/COUNT(x)` rather than `AVG(x)`. SQL Server rejects a correlated
aggregate spanning more than one outer column, which is what `AVG` over a `CASE`
on `F.dev` would be. The division is the workaround, and the port keeps it.

### The correction — `fx_split`

The tab leads with this rather than with the screen. It reprices **both** years
at one rate — the prior year's turnover-weighted booked rate — line by line in
each line's own currency, so a model that moved between EUR and USD is handled
by construction rather than excluded:

```
rev_const[y]    = SUM(qte · prx · rate_prev[line currency])   for y in (prev, yr)
margin_const[y] = 1 − cogs[y] / rev_const[y]
drop_pp_ex_fx   = margin_const[prev] − margin_const[yr]
fx_pp           = drop_pp − drop_pp_ex_fx
```

Repricing only `yr` and leaving `prev` at its reported margin looks equivalent
and is not: `line_rev_dt` uses each *invoice's* own `cours`, so a model whose
sales cluster in months when the rate ran above the year's average carries that
timing in its reported margin. `HA 2426011` is the case in point — its 2025
sales were booked at an average 2.9266 against a book-wide 2.9940, so the
one-sided reading scored it at 1.83 pp of FX where the honest figure is 0.39 pp.
Holding one rate across both years cancels it, and makes the split return
exactly zero when a year is compared against itself — which is the test that
catches the mistake.

NaN is a real answer here. A model can appear in a year with credit notes and no
units: `BF 2424260` has two 2025 lines totalling zero units, DT 104 of revenue
and *negative* COGS, which reports as a 115 % margin and a 75 pp "drop".
Constant-rate revenue is then zero and the margin undefined, so the split
returns NaN rather than a number that would rank near the top of any list sorted
by damage.

### What it changed on the Actions page

Book-wide, across the 433 models sold in both 2025 and 2026, the rate moved
reported margin by **+0.85 pp**, worth **DT 236,150**. On the erosion rule's own
scope — 47 models over the 200-unit floor in both years:

| | models | sized at |
|---|---:|---:|
| Rule as it stood (`drop_pp ≥ 3 pp`) | 23 | DT 559,298 |
| Rule now (`drop_pp_ex_fx ≥ 3 pp`) | **16** | **DT 303,302** |

Seven models came off the list: their whole decline was the dinar. None joined,
though the mechanism is symmetric and the rule would pick up a model whose real
erosion a favourable rate was masking. The `why` text now names the FX figure it
removed, and the table carries `of which FX` and `Ex-FX` columns beside the
booked change, so the correction sits next to the GPAO's reading rather than
replacing it.

---

## 9. Not yet ported

Five passes have now ported `frmActiviteComp1`, the two valued-order screens,
`frmAnalyseCoutMatNC`, `frmConsultPrixNC`, `frmExchangeRate` and
`frmStockADate`. The GPAO has
substantially more management reporting that the dashboard does not yet carry.
The largest remaining blocks, in rough order of likely value:

| GPAO screen | What it adds |
|---|---|
| `frmTabAnCAF` / `frmTabAnCAFFour` | Annual CAF tables, customer and supplier |
| `frmTabCompAnnuel` / `…Grp` | Multi-year comparative tables, by group |
| `frmAnalyseStock` / `frmStockTheorique` | Stock analysis, theoretical stock by group |
| `frmStatistiques` | The GPAO's own statistics screen |
| `09-Managements reports/02-Stock` (~29 remaining) | Needs planning, safety stock, inventory variance — `frmStockADate` is done (§10) |
| `ECMagasin` (not a GPAO screen) | The WMS proper: per-storehouse, per-location stock across 15 depots — see §10 |
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

### One correction to §9 as it stood before this pass

The previous edition of this table described `frmConsultPrixNC` as "the stored
price list per model (`prxnom` / `tarif_det`)". That was wrong on both counts.
`prxnom` is only a per-customer *selection* flag — which models appear on that
customer's list — and `tarif_det` is not read by the screen at all. The pricing
engine is `costing_nc`, and as §7 sets out it stores no price, only the inputs
to one.

---

## 10. Unmoved stock — `frmStockADate`

_Added in the fifth pass. Tab: **Build from stock**. Modules: `gpao_stock.py`
(the port) and `bike_builder.py` (**not** a port — see below)._

### Why this one came next

§9 listed the thirty-odd screens of `09-Managements reports/02-Stock` as the
largest unported block. This is the one with a business question attached:
**DT 6.3M of parts have not been issued or reserved since the start of 2024**,
and 90 % of that value is a part a live bike BOM still calls for. It is not
scrap — it is bikes that were never assembled, sitting at full cost.

### What the screen does

`frmStockADate` is a radio group over one query. Four of its seven readings are
balance filters; three are movement readings, and those are what is ported:

```
balance(asof) = Σ (detarticle.qtep − detarticle.qtem)   dat <= asof
                                                        HAVING qte <> 0

option 4  "Stock non mouvementé"     balance ≠ 0
                                     AND Σ qtem over (detart ∪ detarticle)
                                         in [since, asof] = 0
option 5  "Stock jamais mouvementé"  option 4
                                     AND NOT EXISTS lib LIKE 'Achat Facture%'
                                         in (detart ∪ detarticle), dat >= since
option 6  option 4, with `lib NOT LIKE 'Cmde Frns%'` removed from the balance
```

**Option 5 is the ERP's own caption for "jamais mouvementé", and its own
definition of it.** Both it and option 4 are *window* rules: a part is never
moved relative to a date you choose, not over all time. The tab defaults to
option 5 and exposes the other two.

Valuation follows `rbPrice.SelectedIndex = 1`: the latest `detart.pmp` at or
before the as-of date, ordered `dat DESC, indice DESC, lib DESC`, falling back
to `prxfobfpiec × cours`.

### Parity

No export for this screen either, so parity is pinned as §5-§8 were: **the
GPAO's own SQL, run character for character**. `_gpao_sql` holds the query the
VB builds; `tests/test_gpao_stock.py::test_port_reproduces_the_gpao_query` runs
it for all three modes and asserts the port matches on row count, units and
dinar.

| As at 2026-09-21, unmoved since 2024-01-01 | parts | units | value |
|---|---:|---:|---:|
| option 4 — not moved | 3,595 | 2,741,317 | DT 6,756,871 |
| **option 5 — never moved** | **3,355** | **2,617,645** | **DT 6,273,660** |
| option 6 — not moved, ex. undelivered | 3,569 | 2,728,509 | DT 6,628,963 |
| of option 5, used by a live bike BOM | 2,858 | — | **DT 6,093,890** |

Option 5 is a strict subset of option 4, which a test asserts — the extra
`NOT EXISTS` can only remove rows, and if it ever added one it would have been
wired the wrong way round, silently.

### Five more defects

**18. Before the ledger's rebase the screen reports nothing, and presents it as
an answer.** The balance is summed over `detarticle` alone, which in this
restore begins at its `STOCK DEPART` rebase on **2026-07-01**. An as-of date one
day earlier matches no rows, so the grid comes back empty — not "I cannot see
that far back", but a clean and confident nothing. Measured: 19,613 parts are
visible at 2026-07-01 and **zero** at 2026-06-30. The older history is in
`detart`, which the balance never reads. This is the one place the tab does not
reproduce the screen: `gpao_stock.ledger_start()` reads the rebase date and the
tab refuses an earlier as-of rather than showing the empty grid.

**19. The balance is not physical stock — it nets off orders that have not
happened yet.** `SUM(qtep - qtem)` runs over every ledger page, so customer-order
reservations (`Cmde Client`, 3.97M units) and production reservations
(`Of En Cours`, 2.20M) are subtracted, while supplier orders not yet delivered
(`Cmde Frns`, 1.77M) are added. The screen offers a switch for the supplier side
only. What it calls stock is an availability figure — **26.69M units against
28.89M of purely physical movement**.

> **This one works in our favour, and it is why the builder can trust the pool.**
> Because reservations are booked as `qtem`, a part that is spoken for fails the
> movement test and never reaches the unmoved list. "Unmoved" therefore means
> *neither consumed nor reserved*, so the pool is genuinely free to allocate
> rather than quietly competing with the order book.
> `test_reservations_keep_a_spoken_for_part_out_of_the_pool` pins it.

**20. Option 5's re-purchase test has no upper date bound.** Every other clause
is bracketed by the as-of date; the `NOT EXISTS ... LIKE 'Achat Facture%'` check
carries only `dat >= since`. A receipt dated after the as-of date would
disqualify a part from a historical run. `detarticle` does hold 84,721
forward-dated rows out to 2027-12-06, but none is a receipt, so this is **latent
on this restore rather than measured** — recorded the way §8's `ELSE 0` was.

**21. A part with no costed movement is valued at its FOB price, with nothing to
say so.** `CASE WHEN PMP <> 0 THEN PMP ELSE prxfobfpiec * cours END` collapses
two different things into one column — what was paid, and what was quoted. On
the option 5 population that is 37 of 3,355 parts. `load_unmoved` returns
`value_basis` so a reader can tell them apart.

**22. The FOB fallback is converted at the as-of rate, not the rate it was quoted
at.** `prxfobfpiec` was set when the part was sourced, sometimes years earlier,
but the query multiplies it by the latest `devisesc` rate at the as-of date. For
the 15,478 live parts priced in USD, EUR or YEN, that turns a currency move into
an apparent change in the value of stock that has not moved at all.

One thing that looks like a defect and isn't: the inner joins to `fournisseur`
and `groupes` look like they could silently drop parts. They do not — all 19,628
live parts survive both, checked by query before the claim was written.

---

### `bike_builder.py` — **not a port**

Everything above reproduces a GPAO screen. What sits on top of it does not, and
the tab says so on its face. There is no ERP screen that proposes a bike, so
there is no number to tie to and none is claimed.

**The finding that shaped the design.** Coverage of every live bike BOM by
unmoved stock was measured first: **no model exceeds 49 %**, 5,216 score zero,
and the best bucket is 25-49 % with 217 models. So "find a model you can already
build" returns nothing. The pile is skewed to the expensive structural parts —
frames DT 1.50M, suspension forks DT 0.99M, rear hubs DT 0.47M — because those
are what gets over-ordered and stranded, while the cheap consumable tail (paint,
decals, cartons, labels, lubricant, ties, screws) is never unmoved because it
never stops moving.

**So every build needs a bought tail, and that is fine.** Minimal outside parts
has to be measured **by value and by lead time, never by line count** — and lead
time is the real constraint, not money.

### The tab leads with new bikes, not substitution

A substitution into a model the factory already builds swaps one part for
another in a bike that was going to be built anyway: it moves cost, it does not
make a sale, and the GPAO's own production planner already substitutes on
`fpieceq`. So the primary view specifies a **new** bike — one that does not
exist yet — slot by slot out of the shelf, and the substitution view sits below
it as the smaller prize.

**How many slots a bike needs is read off the corpus, not chosen.** A 700C bike
here fills a median of **55 distinct slots** across 70 BOM lines, with quartiles
at 52 and 57, of which **26 are required** (on ≥ 95 % of live bikes). So a build
filling fifty-odd slots is a normal bike rather than an inflated one — a point
the tab makes on the page, because "32 filled, 23 bought" invites the reasonable
suspicion that something has gone wrong. Nothing has: the 23 bought slots are
the consumable tail, and at a batch of 100 they come to **DT 1,033 — about
DT 10 a bike**. An `include_common` toggle drops the build to the 26 required
slots for anyone who wants to see the floor.

### Two objectives, because they genuinely disagree

The first cut maximised stock cleared, which means taking the dearest part in
every slot. That is also what makes a bike expensive, and it produced a 700C mid
build costing **DT 489 against a DT 400 list price** — a bike nobody could sell.
Constraining it to the tier's own price envelope fixes that, but clears less.
Neither is a safe default to impose, so the tab asks:

| 700C, mid tier, batch of 100 | stock cleared | cost/bike | list | margin |
|---|---:|---:|---:|---:|
| **Protect the margin** | DT 27,257 | DT 298.76 | DT 400 | **+25.4 %** |
| **Clear the shelf** | **DT 47,920** | DT 489.53 | DT 400 | **−22.3 %** |

Both are shown whichever is selected, because the choice depends on whether the
shelf space or the sale is the binding problem, and that is not the tool's call
to make. `test_the_two_objectives_trade_stock_against_margin` pins that they
really do trade against each other — clearing can never free *less*, and where
it frees more it must cost margin.

Under "protect the margin" the budget is `benchmark_price × (1 − 25 %)`, the
same `margeProduitFini` target the Overview page, Models tab and Actions page draw their
reference lines at, with the slots after the current one reserved at what the
corpus typically pays for them. Where nothing on the shelf fits the remaining
budget, a shelf part is taken **only if it undercuts buying the slot new** —
otherwise clearing stock would cost more than the part is worth.

The benchmark is the median `costing_nc` list price for live models at that
wheel size and tier, which §7 measured at 7.1 % median error against realised
selling price. A negative margin is a real answer, not a bug: 29-inch high-end
comes out at **−32.8 %** because the shelf holds no battery, motor or
controller, so the tail stops being a rounding error. The tab says so rather
than hiding the build.

### Slots are shown with their names

`typepieces` carries `Libtpiec` (what the slot is), `Ordretpiec` (a hierarchical
assembly rank) and `Grptpiec` (which of the fifteen `groupes` it belongs to).
Every slot table on the tab is labelled from it and sorted into the factory's own
build order — frame, wheels, drive, gears, steering, seating, brakes, then the
finishing groups. A bare `FFS` means nothing to the person deciding what to
build, and descriptions stay in French because that is how the ERP holds them.

**The rules are mined, not written.** Each is a count over the 11,934 live bike
BOMs (763,692 lines):

| Rule | How it is derived |
|---|---|
| Required slot | on ≥ 95 % of live bikes — 24 slots overall, 26 at 700C |
| Common slot | on 40-95 % |
| Fits a wheel size | the part has been built at that wheel size |
| Interchangeable | `fpieceq`, or paired with the same frame in a real BOM |

A slot legitimately repeats within a model — `SKN` twice at 36 spokes, `STK` up
to nine times, 15 slot types in all — so the engine works **line by line**, never
one part per slot. Wheel size is applied per slot from evidence rather than as a
blanket rule: 1,351 of 1,424 frame parts appear at exactly one wheel size, while
saddles and bars cross freely, and both fall out of the same query.

**Substitutions are tiered so a proposal can be audited.** Over the top 40
proposals: T0 own part in stock, 510 lines / DT 777k; T1 `fpieceq`-declared, 13
lines / DT 1.8k; T2 paired with the same frame before, 56 lines / DT 48.6k; T3
same slot and wheel size, 476 lines / DT 465k. A proposal's headline confidence
is the **worst** tier among its structural swaps (groups 01-07), not an average —
one unchecked fork must not hide behind thirty certain decals.

**Tiers are learned, not banded.** Build cost per model at one FX date,
terciled *within each wheel size* (a 20" child's bike and a 29" MTB are not one
scale), then described by marker prevalence. The result is worth reading on its
own: **disc brakes rise 6.8 % → 18.7 % → 37.9 % across the tiers and e-bike
drive 0.2 % → 0.2 % → 14.4 %, while suspension fork and derailleur barely move.**
In this factory, brakes and electrification make a bike expensive; suspension and
gearing do not.

### Two things that would have been wrong, and are not

**Contention**, in the substitution view. Proposals compete for the same frames. On the current pool the
60-deep shortlist wants **DT 2,225,810** standing alone and can actually clear
**DT 956,054** — summing the proposals would have overstated the prize by 2.3×.
`allocate()` hands the pool out in rank order and reports both, and
`test_allocation_removes_the_double_counting` is the test this suite exists for.
The allocation is greedy, which is not optimal and is not claimed to be; the
figure is a floor.

**The bought tail has to be scaled too.** A proposal that wins a tenth of the
stock it wanted builds a tenth of the batch and buys a tenth of its tail. The
first cut of the portfolio tile summed the *unscaled* buy cost — pricing every
proposal at full volume, six thousand bikes for a sixty-deep list — and reported
DT 9.4M of cash against DT 956k cleared, a ratio of 0.1× for a programme whose
best proposal returns 24×. Scaling by the allocated share gives DT 952k and a
portfolio ratio of 1.0×, with 41 proposals clearing more than they cost. The
ratio degrades sharply down the ranking, which is the argument for running the
top few rather than the table.

### What it changed on the Actions page

`_rule_dead_stock` sizes the prize at **DT 622,540** — stock cleared after
allocation, less the scaled bought tail, over the 22 models that clear more than
they cost at a batch of 100. It is deliberately the post-allocation figure: the
standalone total would put this rule above every other rule on the list while
being roughly twice the money that exists.

Since the Management view split into pages, this rule also feeds the **Overview**
page's attention list, so it runs on the landing page. That made its cost worth
fixing: `rank_retrofits` and `allocate` were uncached, and `rank_retrofits` runs
`propose_retrofit` over the shortlist — several full passes over a ~12,000-model
corpus each — on *every* rerun, so changing the display currency paid for it
again. `bike_builder.dead_stock_portfolio()` wraps both behind an
`st.cache_data` keyed on the window scalars. The Actions page and the Overview's
attention list ask for the same window and so share one entry; the
Build-from-stock tab keys on its own controls (shortlist, batch, movement rule,
tier, wheel) and pays once per combination. Cold, the rule costs ~48s and every
other rule on the list together costs ~2s.

### Not done, and worth knowing

`ECMagasin` is a full WMS that nothing in the dashboard reads — `StockMovement`
(565k rows, 2024-01 → 2026-08-19, per storehouse and per location, with
`UnitCostPmp` / `RealStock` / `VirtualStock`), `Location` (26,244 rows down to
aisle and level) and 15 named depots. It would answer "where physically is this
dead stock", which is the next question anyone acting on this tab will ask, and
it answers "never moved" and "last movement date" far more cleanly than the
`detart` / `detarticle` union does. It is a separate unported source and folding
it in here would have doubled the work, so it is the natural next pass.
