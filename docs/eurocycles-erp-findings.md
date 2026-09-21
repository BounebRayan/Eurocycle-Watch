# Eurocycles ERP — Data Source Findings (Step 1)

_Discovery pass over the SQL Server LocalDB instance, ahead of building the new
"management" section of the dashboard. This document is **findings only** — no
schema changes, no code. It maps what is in the ERP, what is useful for a
management / margin dashboard, and how the useful pieces join together._

Date of scan: 2026-09-03
Instance: `(localdb)\MSSQLLocalDB`

---

## 1. How to connect

The supplied connection string (`Data Source=(localdb)\MSSQLLocalDB;Initial
Catalog=master;Integrated Security=True;Encrypt=True;TrustServerCertificate=False;…`)
**does not connect as-is** — LocalDB here does not present a certificate, so
`Encrypt=True` + `TrustServerCertificate=False` fails with
_"Chiffrement non pris en charge sur SQL Server"_.

Working settings:

| Setting | Value |
|---|---|
| Server | `(localdb)\MSSQLLocalDB` |
| Auth | Integrated / Windows (`Trusted_Connection=yes`) |
| Encrypt | **`no`** — this LocalDB has no TLS at all; `Encrypt=yes` fails even with `TrustServerCertificate=yes` ("encryption not supported") |
| Instance must be started | `sqllocaldb start MSSQLLocalDB` |

For Python (`pyodbc` / SQLAlchemy) — this is what `erp.py` uses:

```
DRIVER={ODBC Driver 17 for SQL Server};SERVER=(localdb)\MSSQLLocalDB;
DATABASE=eurocycles_db;Trusted_Connection=yes;Encrypt=no;
```

`ODBC Driver 17 for SQL Server` is installed. `sqlcmd` (SQLCMD.EXE 17) is on PATH
(use `-C`, not `-N`). Override the app's connection with `ERP_ODBC` env var or
`st.secrets["erp"]["odbc"]`.

> **Note:** this is a *local* developer copy of the production ERP. It is presumably
> a restore/snapshot; freshness of the data needs to be confirmed with the user
> (latest invoice seen is dated **2026-08-22**, latest label print **2026-08-22
> 12:52**, so it is very recent — possibly a live-attached copy or a near-daily
> restore).

---

## 2. Database inventory

12 databases. Only the `eurocycles_*` group matters; `master/model/msdb/tempdb`
are system.

| Database | Size signal | What it is | Relevance |
|---|---|---|---|
| **`eurocycles_db`** | ~230 tables, millions of rows | **Main ERP** — models, sales, purchasing, costing, production orders, finance, inventory, forecast | **PRIMARY — build the dashboard on this** |
| **`eurocycles_db_calc`** | `detart` 4.2M, `invmens` 2.2M | Costing / valuation engine — weighted-average part cost (PMP), price-change history, stock-movement ledger, monthly inventory, finished-goods accounting | **HIGH** — cost & inventory valuation |
| `ECSynchronize` | `PurchaseOrdersPI` 2.7M | Integration/audit log between the ERP ("EC") and another system ("MG"), tracking PO / fabrication-order changes. `PurchaseOrdersLine` + `PurchaseOrdersPI` give model-level ordered quantities & dates | MEDIUM — supply pipeline / on-time |
| `eurocycles_label` | `OFTraceLine` 937k | Frame- and carton-**label printing** per production order (OF). One row per serial number, with `PrintDate` / `UsedDate` timestamps | MEDIUM — unit-level production throughput & timing |
| `eurocycles_mfc` | `OFTrace_det` 283k | Same idea, "MFC" line (a second customer/plant flow — Intersport/`refIntersport` fields). `ModelList` = clean model cross-reference (codeMFC ↔ codeEC ↔ gencod ↔ Intersport ref) | MEDIUM — clean model mapping + throughput |
| `eurocycles_db_actia` | `AssemblyCycle` 716 | e-bike (Actia motor kit) assembly-cycle records | LOW / niche (e-bike ops) |
| `eurocycles_db_trace` | `trace_*` 760k×7 | Full row-level change-data-capture / replication trace tables | IGNORE (audit plumbing) |
| `ecgmao_db` | ~4.9k `machinesd` | **GMAO** = factory-equipment maintenance management (machines, work orders, spare parts, technicians) | LOW — only if we want an equipment-downtime / maintenance-cost tile later |

---

## 3. What the business is (context for the metrics)

Eurocycles is a **contract bicycle manufacturer / assembler** (Tunisia — base
currency `DT`, buys components in `USD`/`EUR`). It designs & assembles bikes for
**many retail brands / distributors**, of which **Halfords (brand "Apollo",
also Carrera, Indi, Carrera, …) is the single largest customer** — roughly
**85–90% of invoiced units** carry the `HA ` model prefix.

That fact is the backbone of the redesign the user asked for:

- Everything currently in the dashboard = **the Halfords/Apollo distributor view**
  (external market-facing scrape of Halfords.com listings).
- The **new management section** = the *internal* picture from this ERP:
  every distributor, real cost, real margin, production and supply.
- The two connect on the **model** (Apollo model on Halfords.com ≈ a `nomachat`
  row with `codnach` starting `HA `).

### Customers / distributors

| Table | Grain | Key columns |
|---|---|---|
| `customer` | one row per **commercial customer / group** | `codcust` (PK, float), `libcust`, `abrev` (2-char), `codecli` (→ default ship-to), `typc` (`DISTRIBUTOR` / `WHOLESALER` / `REVENDEUR` / …) |
| `client` | one row per **ship-to / bill-to account** | `codecli` (PK, e.g. `2005`), `libcli`, `pays`, `devcli` (currency), `codcust` (→ parent customer), `typc` |

- `facture.clif` = `client.codecli`. e.g. `clif = '2005'` → `HALFORDS LTD`.
- `customer` for Halfords: `codcust = 13`, `abrev = 'HA'`, `libcust = 'HALFORDS'`,
  `codecli = '2005'`. Falcon/Tandem Group = `codcust 10`, Aldi = `19`, Asda = `23`, etc.
- **"Distributor view" filter** = pick a `customer` (or `client.codcust` group) and
  scope every sales/margin metric to it. Halfords is one of ~150 customers.
- **Model → customer** is `nomachat.cusnach` → `customer.codcust`
  (`cusnach` is `nvarchar(20)` holding the numeric id → `TRY_CONVERT(float, cusnach) = codcust`).
  **All 20,012 `nomachat` rows have `cusnach` populated** — this is the authoritative
  link, not the `codnach` prefix (see §4). Two ways to scope a distributor view:
  by *who the model is designed for* (`nomachat.cusnach`) or by *who was actually
  invoiced* (`facture.clif` → `client.codcust`); they usually agree.

---

## 4. Core entity: the **model** (`nomachat`)

`nomachat` = _nomenclature d'achat_ = the SKU / model master. **25,823 rows.**

| Column | Meaning |
|---|---|
| `codnach` | **PK**, e.g. `HA 2427503`. **Free text typed by whoever creates the article** (`frmNomenclatureTMP2.vb` `txtCodnach`) — no code parses or generates it. The 2-letter prefix is a *human naming convention*, not enforced or documented. This is the join key used everywhere (`facture_det.article`, `costing_nc.coden`, `ordprevision.codnach`, `nomachat_det.codndach`, `prxnom.codnach`, …). |
| `libnach` | short description (colour / build) |
| `modnach` | model description (`27.5x18 ALLOY 16SP M310 ALTUS`) |
| `brandnach` | **free-text brand** — messy: `APOLLO`, `APOLLO 2016`, `APOLLO VARIANT`, ` APOLLO`, `HALFORD 2020`… ~2,500 distinct values, many typos. Needs a normalisation map. |
| `framenach`, `wheelnach`, `Color`, `nwnach`/`gwnach` (net/gross weight) | physical attributes |
| `pacnach` (→ `packing.codpack`), `visnach` | packaging |
| `cusnach` | **customer FK → `customer.codcust`** (numeric id as text). Authoritative model→customer link. Resolves to `customer.libcust`; `customer.abrev` is the 2-char display code. |
| `gencodnach` | generic-design code (free text) — flags duplicate designs across articles; **not** a customer code |
| `prxnach` | a reference price (DT) — **often 0 or stale**, do not trust as the sell price |
| `ebike` (tinyint) | e-bike flag (~647 models) |
| `isBike` (tinyint) | is a complete bike vs. a part/accessory kit |
| `isArchived` (tinyint) | retired model |
| `codeActia` | link to the Actia e-bike system |
| `img` | image (nvarchar(max) — likely base64 or path) |

**Active bike models filter:** `isBike = 1 AND isArchived = 0`
(→ ~11,376 active; ~7,018 archived; ~933 non-bike).

### `codnach` is structured — and carries the model **year**

Empirically `codnach` decodes as `<prefix><YY><wheel><serial>`:

| code | prefix | season | wheel | serial |
|---|---|---|---|---|
| `HA 2427813` | HA | 2024 | 27.5" | 813 |
| `HA EB252750` | HAEB | 2025 | 27.5" | 750 |
| `HA 1420209` | HA | 2014 | 20" | 209 |

It parses on **100 % of the 4,171 articles with sales, company-wide** — not just the
customers whose codes look tidy — and on 100 % of Halfords bike rows. The `EB`
prefix is exactly `ebike = 1`. The decoded wheel is *better* than parsing `modnach`,
whose `40x16T` is a sprocket count, not a wheel (they disagree on 7 % of rows and
`codnach` is right every time).

**The season digits matter more than they look.** The same bike gets a new code
every year — `HA 2027813` and `HA 2227813` are the 2020 and 2022 ENTICE 17". So
anything that groups by `article` across years reports the annual re-coding of the
range as models dropped and replaced. Measured: year on year, **59 %** of a year's
margin sits on articles that also sold the year before, but **72-80 %** sits on
*design keys* (`prefix|wheel|serial`, season stripped) that did.

`erp.design_key()` is that season-independent identity; `erp.load_sales` returns it
as `design_key`. Use it for any year-over-year comparison of "the same bike". It is
still a convention, not a constraint, so it falls back to the raw code when a
article doesn't parse.

### Model cross-reference — `eurocycles_mfc.ModelList` (107 rows)
Clean, curated: `codeMFC`, `codeEC` (= `nomachat.codnach`), `mark`, `model`,
`gencod` (EAN/barcode), `bikeType`, `color`, `wheelSize`, `netWeight`,
`refIntersport` / `desigIntersport`, `startCode`/`endCode` (serial-number range).
Small but authoritative — good source for a brand/type normalisation table if we
extend it.

### `codnach` prefix → customer (empirical, **derived from `cusnach`**)

There is **no mapping table** — this is just `LEFT(codnach,2)` correlated with the
real `nomachat.cusnach → customer.codcust` link over active bike models. Useful as a
label/shortcut; the FK is the source of truth. Prefixes are mostly 1:1, a few split
(`FA` = Claudbutler / Falcon-Tandem; `KS` = KS Cycling / Avantago; `AT`+`AP` = Atala).

| prefix | customer (`libcust`) | ~active models |
|---|---|---|
| `AT` | ATALA S.P.A. ITALY | 940 |
| `HA` | **HALFORDS** | 938 |
| `SA` | SARKIS K. ZELVEGIAN | 789 |
| `EC` | EUROCYCLES (internal / own brand) | 714 |
| `DP` | DEPORTICA | 580 |
| `KS` | KS CYCLING GERMANY | 501 |
| `GO` | GO OUTDOORS LTD – JD SPORTS | 460 |
| `ML` | MOORE LARGE | 433 |
| `MO` | MOLHO BROS CO | 422 |
| `MV` | MV SPORTS & LEISURE LTD | 408 |
| `CN` | CONOR SPORT SA | 342 |
| `DE` | DENVER | 317 |
| `HB` | HARO BIKES | 302 |
| `FS` | FSCI | 225 |
| `MS` | MOTOR & SPORT | 210 |
| `MI` | M.I.T.S | 195 |
| `TS` | TSA TOOLS | 176 |
| `BD` | MTF ENTREPRISES | 174 |
| `AC` | AL BIDAYA CYCLES | 122 |
| `CF` | MANUFACTURE FRANÇAISE DU CYCLE (Intersport/Nakamura) | 118 |
| `TK` | TK TRADING IMPORT EXPORT | 116 |
| `TE` | TESCO | 114 |
| `PA` | PENTAGON SPORTS | 93 |
| `SC` | SCHIANO ITALY | 86 |
| `RA` | RALEIGH | 72 |
| … | ~60 more, each &lt;70 models | |

Rebuild any time with:

```sql
SELECT LEFT(N.codnach,2) prefix, C.libcust, COUNT(*) c
FROM nomachat N
LEFT JOIN customer C ON TRY_CONVERT(float, N.cusnach) = C.codcust
WHERE N.isBike = 1 AND N.isArchived = 0
GROUP BY LEFT(N.codnach,2), C.libcust
ORDER BY c DESC;
```

---

## 5. Sales / revenue

### `facture` (13,422 rows) — sales invoice header. `datf` 2009-04 → **2026-08-22**.

| Column | Meaning |
|---|---|
| `numf` (float, PK) | invoice number (`202600443`) |
| `datf` (date) | invoice date |
| `clif` → `client.codecli` | customer ship-to |
| `nclif` | customer name snapshot |
| `dev` | currency (`USD` ~73%, `EUR` ~27%, `DT` rare) |
| `cours` | FX rate to `DT` (avg USD ≈ 2.67, EUR ≈ 3.12) — **multiply by this to get DT** |
| `brut`, `trem` (discount), `net`, `tmat`, `ttc` | invoice money totals (in `dev`) |
| `tcnt` (container), `portf`, `inco` (incoterm), `nav`, `nrdhl` | logistics |
| `vld` (tinyint) | validated flag |
| `avoirFact`, `isAvrFromSel` | credit-note markers |
| `createdDate` / `updatedDate` / `…by` | audit |

### `facture_det` (43,399 rows) — invoice line.

| Column | Meaning |
|---|---|
| `fact` → `facture.numf` | parent invoice |
| `article` → `nomachat.codnach` | **the model sold** (~7% of lines have no matching `nomachat` — freight lines, ad-hoc text, retired codes) |
| `lib` | description snapshot |
| `qte` | units |
| `prx` | **unit price** in the invoice currency |
| `mat` | line material/standard-cost value (looks like a costed amount per unit — confirm) |
| `poids`, `colis` | weight / cartons |
| `typ` | line type (`O` = order/bike vs other) |
| `ofnach` | production-order reference tie-back |

### Sales facts we can compute directly

- **Revenue** = `Σ qte·prx` (native) or `Σ qte·prx·cours` (DT). By model, brand,
  customer, month, year, incoterm.
- **Units** = `Σ qte`. **ASP** = revenue / units.
- **Mix** — share of units/revenue per brand or per customer.
- History is solid **2015 → 2026** (2009-2014 is sparse / pre-migration).
  Ballpark: **30–50 M DT/yr**, ~900–1,500 invoices/yr, ~300–530k bike units/yr
  invoiced.

**Top models by sales value, 2024-01 → 2026-08** (native ccy, mixed — illustrative):

| model | brand | units | ~value |
|---|---|---|---|
| `HA 1920301` 20" RIGID GIRL 1SP | APOLLO | 38,714 | 2.31 M |
| `HA 2024100` 24" HARDTAIL BOYS 18SP | APOLLO | 15,934 | 1.58 M |
| `HA 1920300` 20" RIGID BOYS 1SP | APOLLO | 21,240 | 1.28 M |
| `HA 1924101` 24" HARDTAIL GIRLS 18SP | APOLLO | 11,405 | 1.14 M |
| `HA 2220420` 20" H.TAIL BOYS 7SP | CARRERA | 7,020 | 1.01 M |

Related sales-side docs: `facturep`/`facturep_det` (pro-forma invoices),
`packinglist`/`packinglist_det` (packing lists), `PriceList` + `PriceListConditions`
(volume-bracket price list: 500-2 500 / 2 501-10 000 / >10 000 units),
`tarif`/`tarif_det` (customer-season price lists), `forcast`/`forcast_det` &
`planningprev`/`planningprev_det` (customer demand forecast / shipment planning).

---

## 6. Cost & **margin** — the heart of the ask

### 6a. `costing_nc` (24,443 rows) — the per-article **manufacturing costing sheet**

**Confirmed from `frmCostingTMP3.vb`** (INSERT/SELECT + form labels), plus
`frmConsultPrixNC.vb` / `frmFichePiece_tarifFour.vb` for the price outputs.

Grain: **one row per article + tariff** — `coden` (= `nomachat.codnach`) × `an`
(year) × `saison` (almost always `D`) × `IdCondition` (volume bracket →
`PriceListConditions`: 500–2 500 / 2 501–10 000 / >10 000 units).
Coverage by year: **2024 = 10,927 rows** (a full re-costing), 2015-2021
~1,000-1,900/yr, **2025 only 528, 2026 only 173 so far**.

**Column convention:** a base column holds an amount *in its own currency*; each
cost line has three siblings — `p…` = **percentage**, `c…` = **cours** (FX rate),
`d…` = **devise** (currency code `DT`/`EUR`/`USD`/`YEN`). `amount × rate` = the
DT-converted total for that line.

**Raw-material total** ("Total matière" → `tdt` is "TOTAL I"):

| Column | Meaning |
|---|---|
| `tusd` / `cusd` | raw-material total in **USD** + USD rate used |
| `teur` / `ceur` | same in **EUR** |
| `tyen` / `cyen` | same in **YEN** |
| `tdt` | raw-material total converted to **DT** — the reference **TOTAL I** |

**Cost-line tiers** ("Calcule prix de vente") — each = amount + `%` + rate + ccy:

| base | `%` | rate | ccy | Meaning |
|---|---|---|---|---|
| `trs` | `ptrs` | `ctrs` | `dtrs` | **Transport sur achat** — inbound / purchase freight |
| `mov` | `pmov` | `cmov` | `dmov` | **Main d'œuvre** — labour |
| `cha` | `pcha` | `ccha` | `dcha` | **Charge d'exploitation** — operating overhead |

**Commissions, margin, levies:**

| Column | Meaning |
|---|---|
| `com1` | Commission (01), % — applied after TOTAL II |
| `com2` | Commission (02), % — applied after TOTAL III |
| `marge` | **Marge (profit margin), %** |
| `rebait` | Rebate / discount |
| `EcoValue` | Éco-Emballage (eco-packaging levy), in DT |

**Outbound transport (sale-side), two Incoterm variants:**

| columns | Meaning |
|---|---|
| `trsv` / `ptrsv` / `ctrsv` / `dtrsv` | Transport sur vente — **FOB** (amount, %, rate, ccy) |
| `trsvd` / `ptrsvd` / `ctrsvd` / `dtrsvd` | Transport sur vente — **DDU** |

| Other | Meaning |
|---|---|
| `coden`, `an`, `saison`, `IdCondition` | key (article × year × season × volume bracket) |
| `dtd`, `dtf` | validity window (`2024-01-01`…`2024-12-31`) |
| `inddf` | costing-detail index / revision pointer |
| `createdBy/Date`, `updatedBy/Date` | audit — **drives "margin change over time" per model** |

**Cost → price chain** (confirmed from `calcTotal`, `frmCostingTMP3.vb` ~L699):

```
TotalI  = tdt                              raw-material cost in DT
TotalG1 = TotalI + trs·ctrs + mov·cmov + cha·ccha     + labour + overhead + inbound freight
TotMarge = tdt × (marge / 100)             ⚠ markup computed on RAW-MATERIAL cost only
TotalG2 = TotalG1 + TotMarge               price = full cost + (material × marge%)
  then + commission com1 (→ TOTAL II) + commission com2 (→ TOTAL III)
       − rebait,  + EcoValue (DT),  + outbound transport (trsv FOB / trsvd DDU)
  = final sale price (EUR / USD / DT)   ← computed in-form; not every output
                                          column is persisted in this table
```

**`marge` is a cost-plus markup, not a margin on price** — `price = cost + material·marge%`,
**not** `cost/(1−marge%)`. And the markup base is `tdt` (material only), while it's
added on top of the fuller `TotalG1` (material + labour + overhead + inbound
freight). Realised gross-margin % must be computed as `(price − full cost)/price`
independently — don't read `marge` as the achieved margin.

So `costing_nc` gives **planned cost build-up + the markup rule per model per year
per volume bracket**. `createdDate`/`updatedDate` + year-over-year rows for the same
`coden` → **margin drift**. Not all final sale-price outputs are stored here — for
the persisted price outputs use `prxnom` / `tarif_det` / `PriceList`, or the
consult forms noted above.

⚠️ **Coverage gap:** of the distinct models actually sold since 2024 (2,347),
only **~1,061 (45%)** have a 2024+ `costing_nc` row (across any bracket). Older
sold models fall back to their last costed year. The dashboard must handle
"no current costing".

### 6b. Realised cost / margin

- `nomcom` / `nomcom_det` — **commercial BOM snapshot tied to an invoice**
  (`nomcom.fact` → `facture.numf`). `nomcom_det` has each component's `qtendach`
  (qty), `prxndach` (price), `devndach` (ccy), `fabndach` (maker). → **actual
  costed BOM per invoiced model** → realised gross margin = line revenue − Σ(BOM).
- `nomachat_det` (1.17M rows) — the current/engineering BOM per model
  (`codndach`, `qtendach`, `prxndach`, `typndach`, `impndach` = imported flag).
- `invprodfini` / `invprodfini_det` — finished-goods **physical inventory counts**
  (year-end, per customer `codeclt`), with `prixDT` and `coutRev` (_coût de
  revient_ = COGS) per model — **another realised unit-cost source**, though the
  local copy's `coutRev` is largely 0 in older rows.
- `margeProduitFini` (7 rows) — **company-wide target margin % by year**
  (2020-2026 all = **25%**). A single KPI headline / benchmark line.

### 6c. Part-level cost engine — `eurocycles_db_calc`

| Table | Rows | Use |
|---|---|---|
| `prxpmp` | 6,301 | **weighted-average cost (PMP) movement ledger per part** — `piece`, `dat`, `pmp`, `stk`, `ent`/`srt`/`qte`, `dev`, `cours`. Current landed cost of every component. |
| `prxpmp_BAK` | 509k | historical PMP snapshots (2019 → 2020-02 in this copy — may be a stale backup) |
| `hisprix` | 60k | **price-change log per part**: `ancprx` → `novprx`, `ancdev`→`novdev`, `dat`, `utl`. Drives "component inflation" analysis behind margin erosion. |
| `hprxcmdf` | 7,773 | PO-price change history |
| `detart` / `detart2006` / `detarticle` | 4.2M / 2.4M / 174k | full **stock-movement ledger** per part (`qtep` in / `qtem` out, `sld` balance, `pmp`) |
| `invmens` | 2.2M | **monthly inventory snapshots** per part |
| `AccountingFinishedGoods` | 5,086 | **finished-goods stock accounting** per `ItemCode` per period: `QtyInv/Reg/Dep/Dec/Fac/Stock` + `UnitCost` — clean FG stock & unit cost by model |
| `histn` / `histp` | 1.8M / 81k | nomenclature / part master change logs |

---

## 7. Production

| Table | Rows | Grain / use |
|---|---|---|
| `ordprevision` | 38,925 | **production order (OF) lines** — `id_o` (PK), `codnach` (model), `ofdnach` (OF no.), `libnach`/`modnach`, `semaine`+`an` (planned week), `dat`, `qte`, `cmde` (customer order ref), `brandnach` |
| `ordprevision_det` | 2.48M | component-level requirement per OF (`codndach` part, `qtendach`, `prxndach`) |
| `declarationprd` | 53,658 | **production declarations** — `id_o` (→ `ordprevision`), `dat`, `qte` produced, `fermee` (closed). Multiple per OF. |
| `hisof` | 113k | OF status-change history (`indic`, `txt`, `utl`, `dat`) — lead-time / WIP-age analysis |
| `prod_gaines` | 26k | sub-assembly ("gaines"/cable-housing?) production |
| `eurocycles_label.OFTrace` / `OFTraceLine` | 10,816 / 937k | **serial-number label printing per OF** — `Brand`, `Model`, `ItemOF`, `TotalQty`, and per unit `PrintDate` / `UsedDate`. → true unit-level throughput & timing, **2022-05 → 2026-08**. |
| `eurocycles_mfc.OFTrace` / `OFTrace_det` | 1,068 / 283k | second plant/flow (Intersport) equivalent |
| `eurocycles_db_actia.AssemblyCycle` | 716 | e-bike assembly cycle times |

**Production facts:** planned vs declared units per model / week / brand;
plan attainment (`declarationprd.qte` ÷ `ordprevision.qte`); production lead time
(`hisof` / OFTrace timestamps). Ballpark output **~300–530k units/yr**, 2015-2026.

---

## 8. Purchasing / supply pipeline

| Table | Rows | Use |
|---|---|---|
| `commandf` / `commandf_det` | 27,450 / 105,981 | **supplier purchase orders** for components — `numcmdf`, `datcmdf`, `frncmdf` (supplier), `devcmdf`/`courscmdf`; lines have `artdcmdf`, `qtedcmdf`, `prxdcmdf`, `eta`/`etd` (ship dates) |
| `facturef` / `facturef_det` | 20,864 / 96,193 | **supplier invoices = landed component cost**, incl. freight/insurance/customs buckets (`tim`, `transit`, `femb`, `fdae`, `fsa`, `ass`, `tax`, `trs`). `datfctf` 2014 → 2026-08. ~27–66 M DT/yr. |
| `nomachat` (BOM via `nomachat_det`) | — | which parts each model needs |
| `douane` / `douane_det` | 14,435 | customs declarations & duty |
| `transportf` / `transportdf` / `transportddf` / `containerTracking` | 24k / 30k / … | inbound freight & container tracking |
| `reglementfrns` / `reglfrn` / `suivie` | 11k / 6k / 2.5k | supplier payments, effets, financing |
| `flitige` / `flitige_det` | 6,205 / 35,699 | **supplier disputes / claims** (quality, quantity) — a cost-recovery / supplier-scorecard metric |
| `ReceptionControl*` | 817 + lines | incoming-goods QC |
| `ECSynchronize.PurchaseOrders` / `PurchaseOrdersLine` / `PurchaseOrdersPI` | 25k / 91k / 2.7M | model-level **ordered quantities & dates** and proforma-invoice quantities, plus a change-audit trail (`AlertMessage` in French describes each edit). Useful for "units on order per model / expected arrival", less so as a clean table. |

---

## 9. Finance (supporting KPIs)

`ecritures`/`decritures` don't exist here — GL is in `ecgmao_db` (`ecritures`,
`comptes`, `journaux`) which looks like a *different, smaller* company (a
maintenance-services entity — GMAO). The commercial finance in `eurocycles_db`:

- `reglement` / `reglement_det` (12,894) — **customer receipts**.
- `caisse` (54,870) / `caissef` (5,784) — cash journal (DT / foreign).
- `feffet` / `effetDom*` / `ficheefAB`/`ficheefATB`/`ficheefbh` — bills of exchange / effets.
- `previsions_tresorerie` (80) — **treasury forecast**.
- `agent1` (invoice → agent) + `agent3` / `agent3_ebike` (commission rate per
  model-code per season, e.g. `0.5% USD`) — **sales-commission cost**, feeds net margin.
- `SDisputes` / `SDisputesLine` (1,305 / 8,630) — **customer disputes / debit notes**.
- `DiscountPO` / `DiscountPOLine`, `facture.trem` — discounts given.
- **`facture_remise` / `facture_remise_det` — the rebate the GPAO actually uses.**
  A negotiated per-article price (`nprix`) valid over a date range for one
  customer; the rebate is `qte·prx·cours − qte·nprix·cours` over invoices falling
  in that range. This, not `trem`, is what `frmActiviteComp1.vb` subtracts from
  its revenue totals. See `docs/gpao-parity.md`.

---

## 10. Ready-made KPI feeds already in the ERP

- **`DailySummary` (876 rows) + `DailySummaryCategory`** — the ERP already computes
  a daily operational scoreboard. Categories:
  1 PRODUCTION/JOUR · 2 EXPORT DU JOUR · 3 ÉCHÉANCE DU JOUR · 4 IMPORT DU JOUR ·
  5 ÉCHÉANCE IMPORT · 6 STOCK À DATE · 7 CONSOMMATION · 8 NBRE TOTAL VÉLOS PRODUITS ·
  9 NBRE TOTAL VÉLOS FACTURÉS · 10 NBRE TOTAL VÉLOS / {période} · 11 VÉLOS SUPPLÉMENTAIRES.
  `DailySummary.CatId1..CatId11` hold the values per `DocumentDate`. **Low-effort
  daily KPI strip.**
- **`DashboardPart` / `DashboardConfig` / `DashboardGroup`** — the ERP's own
  dashboard widget catalogue. Tells us what management already watches:
  _Évaluation du CA par mois_, _Meilleurs clients_, _Évolution CA fournisseurs_,
  **_Meilleures nomenclatures_** (best models), _Encours client / fournisseur_
  (AR/AP ageing), _Statistique_, _Planning_, negative-stock lists, inventory
  (theoretical vs physical 31/12), certified-part alerts. Good backlog of tiles to
  mirror / improve.

---

## 11. Metrics the new "management" section can deliver (and how)

| Metric | Source join |
|---|---|
| **Revenue / units / ASP** by model · brand · customer · month | `facture` ⋈ `facture_det` ⋈ `nomachat`; DT via `·cours` |
| **Best / worst models** (revenue, units, growth YoY, margin) | same + `costing_nc` on `coden = article` for the year (+ bracket) |
| **Planned full cost per model** | `costing_nc`: `TotalG1 = tdt + trs·ctrs + mov·cmov + cha·ccha` (→ DT) |
| **Planned price per model** | `TotalG1 + tdt·(marge/100)` then + `com1`/`com2`, − `rebait`, + `EcoValue`, + `trsv`/`trsvd` (see §6a chain) |
| **Planned gross margin % per model** | `(planned price − TotalG1) / planned price` — **compute it; do not read `marge` as the achieved margin** (it's a material-only markup rate) |
| **Realised gross margin per model** | `facture_det` revenue vs `nomcom_det` Σ(`qtendach·prxndach·`rate) for that `nomcom.fact` |
| **Margin gain / erosion over time** | `costing_nc` rows for the same `coden` across `an` (hold `IdCondition` fixed); `updatedDate` deltas; component driver = `hisprix` (`ancprx`→`novprx`) weighted by `nomachat_det` |
| **Net margin** | gross − `com1`/`com2` (`costing_nc`) or `agent3` commission − `rebait` − `EcoValue` − discounts (`facture.trem`, `DiscountPO`) − dispute cost (`SDisputes`, `flitige`) |
| **Distributor scorecard** | scope by `nomachat.cusnach → customer.codcust` (model's customer) and/or `facture.clif → client.codcust` (invoiced customer) |
| **Plan attainment** | `Σ declarationprd.qte` ÷ `Σ ordprevision.qte` by model / week |
| **Production throughput & lead time** | `eurocycles_label.OFTraceLine.PrintDate/UsedDate` by `OFTrace.Model` |
| **Forecast vs actual** | `forcast_det` / `planningprev_det` vs `facture_det` by model / customer / week |
| **Inventory & FG value** | `AccountingFinishedGoods` (FG), `invmens` / `detart` (parts), `invprodfini_det` |
| **Component cost inflation** | `hisprix`, `prxpmp` over time, weighted by BOM usage (`nomachat_det`) |
| **AR / AP ageing, cash forecast** | `reglement`, `reglementfrns`, `previsions_tresorerie`, `suivie` |
| **Supplier scorecard** | `facturef`, `flitige`, `ReceptionControl`, `commandf_det` ETA vs receipt |

---

## 12. Data-quality caveats

- **`brandnach` is free text** with thousands of dirty variants → build a
  `brand_normalization` map (seed from `eurocycles_mfc.ModelList.mark` +
  the `cusnach`-derived customer + `codnach` prefix + manual rules).
- **`codnach` prefix is a naming convention, not enforced** — for the model→customer
  link always use the `nomachat.cusnach → customer.codcust` FK (100% populated).
  The prefix table in §4 is a convenience label only.
- **Currency**: sales in USD/EUR, costing in USD/EUR/YEN/DT, parts in USD/EUR/DT.
  Every amount carries its own rate (`cours` / `cusd` / `c…` siblings). Store
  everything with its native amount + rate; the dashboard has a **currency
  dropdown** (DT / EUR / USD) that re-converts on the fly using the row's own rate
  (or a chosen period rate).
- **`costing_nc` covers ~45%** of recently-sold models → fallback logic needed
  (last costed year, or BOM-derived via `nomcom_det`, or flag "uncosted").
- **`nomachat.prxnach` is unreliable** (0 / stale) — never use it as a sell price;
  use `facture_det.prx`, `tarif_det`, `prxnom`, or `PriceList`.
- **2009-2014 data is thin** — default the dashboard window to **2015+**.
- **Local-only for now** — build against `(localdb)\MSSQLLocalDB` directly.
  (Later: the Streamlit Cloud deploy ships a static SQLite snapshot + daily
  GitHub Action; a SQL Server source will need an equivalent extract step since
  LocalDB isn't reachable from Streamlit Cloud — out of scope for now.)
- Not every final **sale-price** output is persisted in `costing_nc` — it's
  computed in-form. Use `prxnom` / `tarif_det` / `PriceList` for stored prices.

---

## 12b. Build status (2026-09-03) — what got built vs the data

The Management view now has eight tabs. What each turned out to support, after
building against the restored local copy:

| Tab | Built | Notes / dropped |
|---|---|---|
| **Actions** *(2026-09-04)* | the landing tab — a ranked to-do list, not a report: six rules over the other tabs' data, each item sized in DT on a stated basis and pointed at the tab holding the evidence | Rules: under-target margin, sold below build cost, YoY margin erosion on steady volume, the Halfords price-review shortlist, models selling into a shelf that no longer lists them, production orders left under plan. **`declarationprd.fermee` is unusable** — 25 of 16,248 orders carry it, all for 1-6 units — so the production rule ages orders against the data's own latest invoice instead. The loss rule requires `revenue > 0`: a line with COGS and no revenue is a sample, not a pricing decision. On the current restore 4 of 6 rules fire, ~DT 2.4M at stake. |
| Overview | KPI strip (rev / margin / margin % / units / ASP, YoY), revenue & margin by year, revenue by distributor, **margin bridge** *(2026-09-04)* | The bridge decomposes the YoY margin change into volume / mix / price / cost / new / dropped as an **exact identity** — the six effects sum to the change to the cent. Defaults to the `design_key` grain (see §4): at the `article` grain the seasonal re-coding swamps everything, with new+dropped running 6.8× the size of the actual change against 3.7× by design. Both compared years are trimmed to the same elapsed months when either is the part year — `like_for_like` only covers the newest pair, and the bridge lets the reader pick any two. |
| Models | best/worst by revenue·margin·margin %·**margin-per-bike**, **cost-vs-sale-price-per-bike scatter**, margin drift YoY, revenue-vs-margin, full table with cost/bike + margin/bike, **per-model detail dialog** *(2026-09-21)* | **Planned-vs-realised (costing_nc) dropped** — realised `facture_det.mat` runs 10–70 % above `costing_nc` TotalG1 even same-year same-model, so the comparison only ever says "everything is over plan by ~40 %". Basis mismatch, not execution insight. `load_costing_years()` kept in `erp.py` for future use. The detail dialog (`views/management/model_detail.py`) pulls in a fourth satellite DB, `eurocycles_db_images` — see below. |

## 12c. Reconciliation against the ERP's own report (2026-09-21)

Checked our SQL against two exports of the ERP's own management reporting —
`Classeur3.xlsx` (YTD 2026 vs YTD 2025 across 19 dimensions) and `Classeur2.xlsx`
(monthly P&L). The headline result is a **bug in our own sales filter**.

### `facture_det.typ` — `O` alone was wrong, it must be `O` + `I`

`load_sales` filtered `d.typ = 'O' OR d.typ IS NULL`. That silently dropped
`typ = 'I'`, which is **0.6–3.1 M DT of genuine bike revenue a year**:

| | `O` | `I` | `N` | `S` | `P` |
|---|---|---|---|---|---|
| 2026 YTD revenue | 39.37 M | **2.56 M** | −15 k | 33 k | 0.3 k |
| lines with COGS (2019+) | 26,767 / 26,811 | **668 / 671** | 0 / 1,900 | 0 / 439 | 63 / 134 |
| articles | real | real | literal `xxx`/`XXX` | spare parts | spare parts |

`I` goes to the same distributors (Halfords 5.1 M, MFC 1.8 M, JD Sports 1.0 M
over 2019+), carries COGS at the same 99.6 % coverage as `O`, and **127 invoices
contain both `O` and `I` lines** — so it is a line-level attribute, not a
separate document series. `N`/`S`/`P` stay excluded: no COGS at all, and `N`'s
articles are placeholders.

Effect: 2026 revenue **39.4 M → 41.9 M (+6.5 %)**, units +9,468. Every tab moved.

### After the fix, we match the ERP report to ~0.09 %

| cut | ours | ERP report | delta |
|---|---|---|---|
| revenue by wheel size (§01) | 41,933,045 | 41,894,203 | **+0.09 %** |
| units by wheel size (§02) | 136,498 | 136,733 | −0.17 % |
| revenue by country (§06) | 41,933,045 | 41,893,690 | **+0.09 %** |

Six wheel buckets (`26" Kids`, `14"`, `18"`, `12"`, `26x1-3/8`, `20 TR`) and six
countries (Ireland, Germany, Ghana, Libya, Greece, Poland) match **to the dinar**.
The residual sits in the report's own `PIECES DIVERS` / `Remise` buckets, which
reclassify a handful of non-bike lines out of the wheel buckets — a presentation
difference, not a data one.

## 12d. The ERP margin is not the accounting margin

The Overview's KPI strip shows `revenue − facture_det.mat`. That is **margin
over standard material cost**, and it is roughly **five points above** the
accounting gross margin. Over Jan–Jun 2026, company-wide:

| | DT | % of revenue |
|---|---:|---:|
| Revenue — ERP | 32,525,872 | |
| Revenue — pack | 32,520,011 | (agree to 0.02 %) |
| Margin over material cost (ERP) | 10,362,679 | **31.9 %** |
| Gross margin (pack) | 8,762,650 | **26.9 %** |

Revenue agrees; the margins are simply different measures:

- **ERP** — standard material cost × units sold. No packaging, no paint, no
  inbound freight.
- **Pack** — what was actually purchased, adjusted for stock movement, and it
  does carry those. Over a short window the two also drift on timing alone:
  what was bought in a month is not what was sold in it.

Neither is wrong. The ERP figure is per-unit and consistent, so it is the right
one for comparing models; the pack is the right one for what the company
earned. The KPI tile is therefore labelled **"Margin over build cost"**, not
"Gross margin", and the Overview carries a reconciliation expander so the two
numbers on the page don't look like a contradiction.

**And gross margin is not the bottom line.** Walking the pack down: gross 26.9 %
→ less 19.5 % of revenue in operating costs → P.B.I.T **8.6 %** → after profit
tax, **net 7.3 %**. A reader whose only headline was the 32.6 % tile would be
out by a factor of four.

### Payroll — `chargeprs` is the only P&L section that exists here

Checked the whole of `Classeur2.xlsx` (the monthly P&L) against the database.
**Only salaries are in the ERP.** `chargeprs` is a year × code matrix with a
column per month (`m01`..`m12`), 105 rows; nothing else in any attached database
carries the other sections (cost of sales, external services, taxes,
depreciation, financial charges, other income). Searched for the `m01`-style
shape and for `compta`/`ecriture`/`journal`/`bilan` tables — `chargeprs` is the
only hit, and `rubriquest` is transport tariffs, not a chart of accounts. The
rest of that pack comes from an accounting system that is not in this restore.

The two code schemes differ, so `erp.PAYROLL_LINES` is the bridge:

| `chargeprs.cod` | pack code | line | Jan–Jun 2026 |
|---|---|---|---|
| `001` | 10 | Wages | **exact** (1,823,818) |
| `020` | 25 | Staff transport | **ERP empty**, pack has 44,991 |
| `040` | 50 | CNSS | **exact** (627,678) |
| `050` | 60 | Workwear | **exact** (75,251) |
| `070` | 80 | Occupational health | **exact** (11,024) |
| `080` | 90 | Social charges & other | 269,159 vs 280,785 (−11,626) |
| `090` | 100 | Executive pay | **exact** (123,900) |

Five of seven match to the dinar. The two gaps (44,991 + 11,626 = 56,617)
account for the whole −1.9 % difference on the total, so it is a population gap
in this restore, not a mapping error. `load_payroll` says so in its docstring
and the Finance tab repeats it — the total is a floor.

Months stored as `0` are dropped rather than plotted: the current year's
unreached months are zeros and would otherwise draw a cliff to December.

### Dimensions this added to `load_sales`

- `wheel_size` — `nomachat.wheelnach` → `Wheelsize.libwheel` (17 rows). The ERP's
  own buckets (`20"`, `27" 1/2`, `700C`), better than parsing `modnach`.
- `country` — `facture.clif` → `client.pays` (233 rows). The **invoiced client's**
  country, which is not the model's owning customer: a distributor can invoice
  through an entity elsewhere.

⚠️ **Both are mapped in pandas, not joined in SQL.** Added as `LEFT JOIN`s on
`LTRIM(RTRIM(...))` they took `load_sales` from **0.7 s to 121 s** — neither key
is indexed for that. The dimension tables are 17 and 233 rows; map them in memory.

### `eurocycles_db_images.nomachat_img` — product photos (147 rows)

One row per model, keyed on `code` = `nomachat.codnach` verbatim. Covers 129 of
the ~4,171 articles with sales company-wide — most models have no photo, so
callers (`erp.load_model_image`) must handle `None`.

The blob isn't a raw image: it's gzip-compressed with an **undocumented 4-byte
little-endian length prefix** ahead of the gzip stream — no column says so,
confirmed empirically by decompressing and checking for a JPEG SOI marker
(`FFD8FF..`). `extension` is always `NULL`; every sampled image decompresses to
a JPEG regardless.
| **Value chain** *(2026-09-04)* | the Halfords shelf against our build cost — per bike, in £, ex-VAT: KPI strip (retail value, our margin, their margin, our share), a three-way split of the retail price per model, an our-margin-vs-their-margin quadrant, the full table, and both sides' unmatched rows | The join is `crosswalk.py`, by **model name** + e-bike flag + wheel size — no shared identifier exists. **`codnach` is structured for this customer**: `<prefix><YY><wheel><serial>` parses on 100 % of Halfords bike rows, `EB` prefix ≡ `ebike = 1`, and its wheel beats the `modnach` text (whose `40x16T` is a sprocket). Covers **~98 % of Apollo units**; Apollo is ~82 % of Halfords volume, the rest Carrera/Indi/Trax which the Apollo-only scrape can't see. **No GBP rate exists in the ERP** (Halfords invoices in USD) so the tab carries its own rate control. **An exact-key join is not available**: `gencodnach` holds an EAN on most models, but Halfords publishes no GTIN anywhere — checked 2026-09-04 across the Bloomreach search API (any `fl`), the product page HTML/`ld+json`, and the `shopper-products` payload (~90 `c_` attributes, no `ean`/`upc`/`gtin`). That payload does carry `c_wheelsize`, `c_gender`, `c_framematerial`, `c_braketype`, `c_suspension`, `c_numberofgears`, `c_rearderailleur` and per-size `variants[]` — the same tokens `modnach` is built from, and a better narrowing key than an EAN would have been. |
| Customers | per-customer scorecard (rev/margin/units/models/YoY), customer drill-down, **plan-vs-actual units** from `planningprev` (link `planningprev.client → client.codecli`) | **AR ageing dropped** — `reglement` has no customer FK in this copy. |
| Production | plan attainment (`ordprevision` vs `declarationprd`) by week & brand, label throughput by brand (`eurocycles_label`), line-flow time (first→last frame label), produced-vs-invoiced (moved from Overview) | `OFTrace.sysCreatedDate` is first-print, not OF-creation → no true order lead time; framed as "line-flow" instead. `declarationprd.fermee` is `'Oui'/'Non'` text. |
| Supply & cost | component price-change index & currency split (`hisprix`), biggest part moves, supplier spend + freight buckets (`facturef` ⋈ `fournisseur`), supplier claims by count (`SDisputes`), inbound PO pipeline by `eta` | **FG inventory valuation dropped** — `AccountingFinishedGoods.Stock` all 0 in this copy. **BOM-weighted per-model inflation not built** (stretch) — `nomachat_det` part ref (`codendach`) doesn't cleanly key to `hisprix.code`. `commandf.fermeecmdf` always 0 → forward `eta` is the "still awaited" proxy. Supplier master is **`fournisseur`** (852 rows, 100 % match), not `fourn`. |
| Finance | billings vs collections (`reglement_det` cash-in, company-wide), commission cost estimate (`agent1` × `agent3` blended rate) → margin-after-commission | Lean by design. No customer-level AR; `previsions_tresorerie` (80 rows) not surfaced; `facture.trem` ≈ unpopulated so "discounts given" dropped — **this was the wrong column**: the GPAO reads `facture_remise`/`facture_remise_det`, now wired in `gpao_activity.load_remise` (see `docs/gpao-parity.md`). `agent3` = 3 agents × 13 categories, no model→category map, so commission is a blended-rate estimate. |

Satellite DBs needed: `eurocycles_db_calc` (Supply component inflation), `eurocycles_label`
(Production throughput/flow). Tabs degrade to a "restore it" notice when absent
(`erp.db_present()`).

## 13. Proposed shape of the redesign (for discussion — not built yet)

```
Dashboard
├── Distributor view                     ← everything we have today
│   └── Halfords / Apollo   (Halfords.com scrape: price/discount drift,
│                            catalog changes, badges, stock/rating)
│       + NEW: internal cross-check (our sell price, our margin, our units
│              shipped to Halfords) from the ERP, joined on model
│
└── Management view                      ← NEW, from eurocycles_db (+ _calc)
    ├── Company scoreboard      (DailySummary + revenue/units/margin headline,
    │                            vs margeProduitFini 25% target)
    ├── Models                  (best / worst; margin per model; margin drift;
    │                            new vs delisted; mix)
    ├── Distributors / customers(revenue, margin, forecast accuracy, AR ageing,
    │                            disputes — per customer, Halfords highlighted)
    ├── Production              (plan attainment, throughput, lead time, WIP age)
    ├── Supply & cost           (component inflation, PMP, supplier scorecard,
    │                            landed-cost bridge, container pipeline)
    └── Finance                 (cash forecast, AR/AP, effets, commissions)
```

---

## 14. Open questions for the user

**Resolved:**
- ~~`codnach` prefix → customer master list~~ → no table; use `nomachat.cusnach →
  customer.codcust` FK. Empirical prefix labels in §4.
- ~~`costing_nc` column meanings~~ → confirmed from `frmCostingTMP3.vb`, see §6a.
- ~~default display currency~~ → **currency dropdown** (DT / EUR / USD), re-converts live.
- ~~data source~~ → **work locally** against `(localdb)\MSSQLLocalDB` for now.

- ~~`marge` semantics~~ → **cost-plus markup on raw-material cost** (`frmCostingTMP3.vb`
  `calcTotal` ~L699): `TotMarge = tdt·(marge/100)`, `price = TotalG1 + TotMarge`. See §6a.

**Still open:**
1. Priority order of the Management sub-sections (§13) — what does management look
   at first thing in the morning? (All six tabs built; order is Overview → Models →
   Customers → Production → Supply → Finance.)
2. ~~Which `IdCondition` (volume bracket) to show~~ → moot: `costing_nc` comparison
   dropped (§12b); `IdCondition` is `NULL` on ~99 % of rows anyway.
3. Is **`ecgmao_db`** (equipment maintenance) in scope at all, or ignore? (Not built.)
4. `eurocycles_mfc` / Intersport (`CF` = Manufacture Française du Cycle) flow — a
   live second customer to surface, or legacy? (`_mfc` restored but not yet used.)
5. Refresh cadence the management view needs (daily assumed).
6. **Why does `facture_det.mat` (realised COGS) run well above `costing_nc` TotalG1
   (planned full cost)?** Blocks a planned-vs-realised view — is `mat` carrying
   overhead absorption the costing sheet's three tiers don't, or is the costing
   sheet a stale should-cost?
7. **`SDisputes` — customer or supplier?** Built as supplier claims (`SupplierId` +
   `InvoiceId` on the line, part-code `ItemCode`). Confirm it isn't also used for
   customer debit notes.
