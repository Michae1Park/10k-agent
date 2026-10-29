# Financial modeling

Equations behind the workbook, all deterministic code (`src/tenk_agent/research/`). The LLM extracts KPIs and may propose bounded assumptions, never a number. Knobs: `research.yaml`. USD millions; ratios as fractions. $x_{-4}$ is the same quarter last year.

## Inputs

| Source | Gives |
|---|---|
| 10-K / 10-Q (XBRL) | Revenue, SBC, D&A, operating income, tax, net income, shares, cash flow, balance sheet |
| 8-K earnings release (LLM-extracted) | Adj. EBITDA, gross bookings, volume (room nights, trips, orders), segments, **guidance** |
| Tiingo, FRED `DGS10`, Damodaran | Price, risk-free rate $r_f$, equity risk premium $ERP$ |

All read as of the run's date (filed strictly before it).

## Workbook tabs

**Model** and **Assumptions** are live formulas (parity-checked in LibreOffice). **Summary, Scenarios, Valuation, Revisions, Checks, Log** are values written by Python.

## 1. Operating build (Model)

| Template | Companies | Equations |
|---|---|---|
| Marketplace / Orders | BKNG, EXPE, ABNB / DASH, LYFT, CART | $GB = \text{volume} \times \text{value per unit}$, $\quad Rev = GB \times \tau$ |
| Segments | UBER | $GB_s = GB_{s,-4}(1+g_s)$, $\quad Rev_s = GB_s \times \tau_s$, $\quad$ sum over Mobility, Delivery, Freight |
| TRIP | TRIP | $Rev = Rev_{-4}(1+g)$ |

$$EBITDA = Rev - c \cdot GB \qquad (\text{TRIP: } EBITDA = m \cdot Rev)$$

with take rate $\tau = Rev/GB$ and cost ratio $c = (Rev - EBITDA)/GB$, both from history.

## 2. Statements (Model)

$$EBIT = EBITDA - SBC - D\&A \qquad NI = EBIT - t \cdot \max(EBIT, 0)$$

$$EPS = \frac{NI}{\text{shares}} \qquad \text{shares}_q = \text{shares}_{q-1}(1+\delta)$$

$$OCF = NI + D\&A + SBC \qquad FCF = OCF - Capex \qquad Cash_q = Cash_{q-1} + FCF - Buybacks$$

| Parameter | Value |
|---|---|
| SBC, D&A, Capex | $\text{ratio}_{-4} \times Rev$ (trailing-4Q share if missing) |
| $t$ | Trailing-4Q $\frac{\text{tax}}{\text{tax}+NI}$, bounded to [10%, 30%] |
| $\delta$ | Compound quarterly change in shares, last 5 quarters |
| Buybacks | Trailing-4Q average |

## 3. Forecast drivers (Assumptions)

$$g_{q+1} = d \cdot g_q \quad (g_0 = \text{last y/y}) \qquad\qquad \tau_q = \tau_{-4} + s_q,\quad s_{q+1} = d \cdot s_q$$

- $d = 0.9$ (proposer range 0.75–1.0).
- Ratios ($\tau$, $c$, $m$) are clamped to their last-8-quarter min/max ± 50 bps (take rate) or 200 bps (margin).
- **Guidance anchor:** one assumption is solved so quarter 1 equals the guided midpoint: bookings via value per unit, revenue via $\tau$, EBITDA via $c$.

## 4. Scenarios

$$x_{\text{bear/bull}} = x_{\text{base}} \mp k\,\sigma_x \qquad PT = 0.25\,PT_{\text{bear}} + 0.50\,PT_{\text{base}} + 0.25\,PT_{\text{bull}}$$

$\sigma_x$ = std. dev. of driver $x$'s history; $k = 1$ (bounds 0.5–2). Three drivers per company (e.g. UBER: Mobility growth, Delivery growth, Mobility take rate).

## 5. Valuation

**Market**

$$MC = P \times \text{shares} \qquad EV = MC + \text{Debt} - \text{Cash} - \text{ST inv.}$$

**Multiple**

$$M^* = \operatorname{median}_{\text{peers}} \frac{EV}{EBITDA_{2027E}} \qquad PT_{M} = \frac{M^* \cdot EBITDA_{2027E} - \text{Net debt}}{\text{shares}}$$

Bear/bull use $M^*(1 \mp 0.15)$; flagged if outside the peer range.

**DCF**

$$UFCF = EBITDA - SBC - t\max(EBIT,0) - Capex$$

$$EV_{DCF} = \sum_{i=1}^{N}\frac{UFCF_i}{(1+WACC)^{\tau_i}} + \frac{UFCF_N(1+g_T)}{WACC-g_T}\cdot\frac{1}{(1+WACC)^{\tau_N}}$$

Forecast quarters, then annual years to year 10, growing at $g_y = g_T + 0.9\,(g_{y-1} - g_T)$ from the last annual revenue growth. Mid-period discounting from the as-of. $g_T = 3\%$.

$$K_e = r_f + \beta \cdot ERP \qquad WACC = \begin{cases} K_e & \frac{D}{D+E} < 10\% \\[2pt] \frac{E}{D+E}K_e + \frac{D}{D+E}K_d(1-t) & \text{otherwise}\end{cases}$$

$\beta$ = 60 monthly returns vs SPY; $K_d = \text{interest}/\text{debt}$. If $EV_{DCF} \le 0$ (TRIP), the multiple alone is used.

**Price target and rating**

$$PT = 0.6\,PT_{M} + 0.4\,PT_{DCF} \qquad R = \frac{PT}{P} - 1 \quad\Rightarrow\quad \begin{cases}\text{Buy} & R \ge +15\% \\ \text{Sell} & R \le -10\% \\ \text{Hold} & \text{otherwise}\end{cases}$$

## 6. Revisions

$$\Delta = \frac{\text{new}}{\text{old}} - 1$$

For revenue, adj. EBITDA and EPS; old = the previous saved version.

## 7. Checks (any FAIL stops the run)

| Check | Test |
|---|---|
| balance | $Assets = Liabilities + NCI + Equity$, within $1M |
| quarters_sum, q4_ties | Quarters sum to the reported FY; Q4 $=$ FY $-$ 9M |
| take_rate, segments_sum | Recompute from reported figures; segments sum to totals |
| no_future_input, formula_only | Inputs filed before the as-of; forecast cells are formulas |
