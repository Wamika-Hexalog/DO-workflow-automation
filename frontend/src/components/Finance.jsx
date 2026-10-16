import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import mascot from "../assets/mascot-ready.webp";
import { SearchIcon } from "./Icons.jsx";

const PERIODS = [
  ["all", "All time"],
  ["month", "This month"],
  ["30", "Last 30 days"],
  ["7", "Last 7 days"],
  ["custom", "Dates…"],
];
const PAYMENTS = [
  ["all", "Any payment"],
  ["unpaid", "Unpaid"],
  ["overdue", "Overdue"],
  ["paid", "Paid, not confirmed"],
  ["confirmed", "Confirmed by CMA CGM"],
];
const PAYMENT_BADGE = {
  confirmed: ["ok", "Confirmed"],
  paid: ["current", "Paid"],
  unpaid: ["missing", "Unpaid"],
  overdue: ["error", "Overdue"],
};
const DEPOSIT_BADGE = { approved: ["ok", "Approved"], paid: ["current", "Paid"] };

const inr = (n) =>
  `₹${Number(n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const daysBefore = (iso, days) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() - days);
  return d.toLocaleDateString("en-CA");
};
const shortDate = (iso) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "-";

// A payment that is due and not made is overdue: CMA CGM's invoices are payable before delivery.
function paymentOf(job, today) {
  const status = job.payment.status;
  return status === "unpaid" && job.invoice.payable_by && job.invoice.payable_by < today ? "overdue" : status;
}

function boxes(invoice) {
  const sizes = {};
  invoice.charges
    .filter((c) => c.basis === "UNI" && c.description.startsWith("Terminal Handling"))
    .forEach((c) => {
      sizes[c.size_type] = (sizes[c.size_type] || 0) + c.quantity;
    });
  const parts = Object.entries(sizes).map(([size, n]) => `${n} × ${size}`);
  return parts.length ? parts.join(", ") : `${invoice.containers.length} container${invoice.containers.length === 1 ? "" : "s"}`;
}

function sum(rows, pick) {
  return rows.reduce((total, row) => total + (Number(pick(row)) || 0), 0);
}

// What CMA CGM charged, grouped by a key of each line, largest first.
function grouped(lines, key, amount) {
  const groups = new Map();
  for (const { job, line } of lines) {
    const k = key(line);
    const g = groups.get(k) || { key: k, line, amount: 0, taxable: 0, jobs: new Set(), units: {} };
    g.amount += amount(line);
    g.taxable += Number(line.taxable) || 0;
    g.jobs.add(job.job_no);
    if (line.basis) g.units[line.basis] = (g.units[line.basis] || 0) + line.quantity;
    groups.set(k, g);
  }
  return [...groups.values()].sort((a, b) => b.amount - a.amount);
}

function toCsv(rows, today) {
  const head = ["Job", "MBL", "Invoice no.", "Invoice date", "Payable by", "Containers", "Charges (INR)", "GST (INR)",
    "Total (INR)", "Payment", "Payment UTR", "Deposit"];
  const lines = rows.map((j) => [
    j.job_no, j.mbl, j.invoice.invoice_no, j.invoice.invoice_date, j.invoice.payable_by, boxes(j.invoice),
    j.invoice.total_excluding_tax, j.invoice.total_tax, j.invoice.total, PAYMENT_BADGE[paymentOf(j, today)][1],
    j.payment.utr || "", DEPOSIT_BADGE[j.deposit.status]?.[1] || "Unpaid",
  ]);
  return [head, ...lines].map((cells) => cells.map((c) => `"${String(c ?? "").replaceAll('"', '""')}"`).join(",")).join("\n");
}

export default function Finance({ stages, onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [period, setPeriod] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [stage, setStage] = useState("all");
  const [payment, setPayment] = useState("all");
  const [charge, setCharge] = useState("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    api
      .finance()
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  const today = data ? data.today : "";
  const invoiced = useMemo(() => (data ? data.jobs.filter((j) => j.invoice) : []), [data]);
  const waiting = data ? data.jobs.filter((j) => !j.invoice) : [];
  const chargeNames = useMemo(
    () => [...new Set(invoiced.flatMap((j) => j.invoice.charges.map((c) => c.description)))].sort(),
    [invoiced],
  );

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const start = { month: today.slice(0, 8) + "01", 30: daysBefore(today, 30), 7: daysBefore(today, 7) }[period];
    return invoiced
      .filter((j) => {
        const on = j.invoice.invoice_date || "";
        if (period === "custom") return (!from || on >= from) && (!to || on <= to);
        return period === "all" || on >= start;
      })
      .filter((j) => stage === "all" || j.stage === stage)
      .filter((j) => payment === "all" || paymentOf(j, today) === payment)
      .filter((j) => charge === "all" || j.invoice.charges.some((c) => c.description === charge))
      .filter((j) => !q || `${j.job_no} ${j.mbl} ${j.invoice.invoice_no} ${j.hbls.join(" ")}`.toLowerCase().includes(q))
      .sort((a, b) => (b.invoice.invoice_date || "").localeCompare(a.invoice.invoice_date || ""));
  }, [invoiced, period, from, to, stage, payment, charge, query, today]);

  const charges = useMemo(
    () => grouped(shown.flatMap((job) => job.invoice.charges.map((line) => ({ job, line }))), (l) => l.description, (l) => l.amount_inr),
    [shown],
  );
  const taxes = useMemo(
    () => grouped(shown.flatMap((job) => job.invoice.taxes.map((line) => ({ job, line }))), (l) => l.description, (l) => l.amount),
    [shown],
  );

  if (error) return <main className="finance"><p className="problems fin-body">{error}</p></main>;
  if (!data) return <main className="finance"><p className="hint fin-body">Loading…</p></main>;

  const total = sum(shown, (j) => j.invoice.total);
  const excluding = sum(shown, (j) => j.invoice.total_excluding_tax);
  const gst = sum(shown, (j) => j.invoice.total_tax);
  const paid = shown.filter((j) => j.payment.status !== "unpaid");
  const unpaid = shown.filter((j) => j.payment.status === "unpaid");
  const overdue = unpaid.filter((j) => paymentOf(j, today) === "overdue");
  const deposits = shown.filter((j) => j.deposit.status !== "unpaid");
  const containers = sum(shown, (j) => j.invoice.containers.length);
  const filtered = period !== "all" || stage !== "all" || payment !== "all" || charge !== "all" || query;
  const stageName = Object.fromEntries(stages.map((s) => [s.id, s.name]));
  const amountOf = (job) => sum(job.invoice.charges.filter((c) => c.description === charge), (c) => c.amount_inr);

  const exportCsv = () => {
    const url = URL.createObjectURL(new Blob([toCsv(shown, today)], { type: "text/csv" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `do-invoices-${today}.csv` });
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <main className="finance">
      <header className="hero fin-hero">
        <div className="hero-text">
          <h1>DO invoices and payments</h1>
        </div>
        <div />
        <img className="hero-mascot" src={mascot} alt="" width="290" height="480" />
      </header>

      <div className="fin-body">
        <div className="fin-filters" role="search" aria-label="Filter the invoices">
          <select className="input" aria-label="Period" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          {period === "custom" && (
            <>
              <input className="input" type="date" aria-label="From" value={from} onChange={(e) => setFrom(e.target.value)} />
              <input className="input" type="date" aria-label="To" value={to} onChange={(e) => setTo(e.target.value)} />
            </>
          )}
          <select className="input" aria-label="Stage" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="all">Any stage</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <select className="input" aria-label="Payment" value={payment} onChange={(e) => setPayment(e.target.value)}>
            {PAYMENTS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          <select className="input" aria-label="Charge" value={charge} onChange={(e) => setCharge(e.target.value)}>
            <option value="all">Any charge</option>
            {chargeNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <label className="search">
            <SearchIcon />
            <input
              type="search"
              placeholder="Job, MBL, HBL or invoice no."
              aria-label="Search the invoices"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          {filtered && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                setPeriod("all");
                setStage("all");
                setPayment("all");
                setCharge("all");
                setQuery("");
              }}
            >
              Clear filters
            </button>
          )}
          <button type="button" className="btn" onClick={exportCsv} disabled={!shown.length}>
            Export CSV
          </button>
        </div>

        <div className="fin-kpis">
          <div className="fin-kpi">
            <span>Invoiced</span>
            <strong>{inr(total)}</strong>
            <small>
              {shown.length} invoice{shown.length === 1 ? "" : "s"} · {containers} container{containers === 1 ? "" : "s"}
            </small>
          </div>
          <div className="fin-kpi">
            <span>Charges</span>
            <strong>{inr(excluding)}</strong>
            <small>before GST</small>
          </div>
          <div className="fin-kpi">
            <span>GST</span>
            <strong>{inr(gst)}</strong>
            <small>IGST at 18%</small>
          </div>
          <div className="fin-kpi">
            <span>Paid to CMA CGM</span>
            <strong>{inr(sum(paid, (j) => j.invoice.total))}</strong>
            <small>
              {paid.length} paid · {paid.filter((j) => j.payment.status === "confirmed").length} confirmed
            </small>
          </div>
          <div className={`fin-kpi${overdue.length ? " alert" : ""}`}>
            <span>To pay</span>
            <strong>{inr(sum(unpaid, (j) => j.invoice.total))}</strong>
            <small>
              {unpaid.length} unpaid{overdue.length ? ` · ${overdue.length} overdue` : ""}
            </small>
          </div>
          <div className="fin-kpi">
            <span>Security deposits</span>
            <strong>{inr(sum(deposits, (j) => j.deposit.amount))}</strong>
            <small>
              {deposits.length} paid · {deposits.filter((j) => j.deposit.status === "approved").length} approved
            </small>
          </div>
        </div>

        <div className="fin-split">
          <section className="panel fin-panel" aria-labelledby="fin-charges">
            <div className="fin-head">
              <h2 id="fin-charges">Charges by type</h2>
              <span className="fin-note">From the charges table of each invoice, in INR</span>
            </div>
            <div className="fin-table-wrap">
              <table className="fin-table">
                <thead>
                  <tr>
                    <th>Charge</th>
                    <th className="num">Jobs</th>
                    <th className="num">Based on</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {charges.map((g) => (
                    <tr key={g.key} className={g.key === charge ? "picked" : undefined}>
                      <td>
                        <button type="button" className="fin-link" onClick={() => setCharge(g.key === charge ? "all" : g.key)}>
                          {g.key}
                        </button>
                        <div className="fin-bar" aria-hidden="true">
                          <i style={{ width: `${excluding ? (g.amount / excluding) * 100 : 0}%` }} />
                        </div>
                      </td>
                      <td className="num">{g.jobs.size}</td>
                      <td className="num">
                        {Object.entries(g.units)
                          .map(([basis, n]) => `${n} ${basis}`)
                          .join(", ")}
                      </td>
                      <td className="num">{inr(g.amount)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={3}>Total before GST</td>
                    <td className="num">{inr(excluding)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>

          <section className="panel fin-panel" aria-labelledby="fin-gst">
            <div className="fin-head">
              <h2 id="fin-gst">GST</h2>
              <span className="fin-note">From the GST table of each invoice</span>
            </div>
            <div className="fin-table-wrap">
              <table className="fin-table">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>SAC</th>
                    <th className="num">Taxable</th>
                    <th className="num">Tax</th>
                  </tr>
                </thead>
                <tbody>
                  {taxes.map((g) => (
                    <tr key={g.key}>
                      <td>{g.key.replace(/^UP IGST @ 18%\s*/, "")}</td>
                      <td>{g.line.sac}</td>
                      <td className="num">{inr(g.taxable)}</td>
                      <td className="num">{inr(g.amount)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2}>IGST, {taxes[0] ? taxes[0].line.state.charAt(0) + taxes[0].line.state.slice(1).toLowerCase() : "-"}</td>
                    <td className="num">{inr(sum(taxes, (g) => g.taxable))}</td>
                    <td className="num">{inr(gst)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>
        </div>

        <section className="panel fin-panel" aria-labelledby="fin-invoices">
          <div className="fin-head">
            <h2 id="fin-invoices">Invoices</h2>
            <span className="fin-note">
              {shown.length} of {invoiced.length}, newest first
            </span>
          </div>
          {shown.length === 0 ? (
            <p className="fin-note">No invoice matches these filters.</p>
          ) : (
            <div className="fin-table-wrap">
              <table className="fin-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Invoice</th>
                    <th>Payable by</th>
                    <th>Containers</th>
                    <th className="num">Charges</th>
                    <th className="num">GST</th>
                    <th className="num">Total</th>
                    {charge !== "all" && <th className="num">{charge.replace(/ at destination$/, "")}</th>}
                    <th>Payment</th>
                    <th>Deposit</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((j) => {
                    const [tone, label] = PAYMENT_BADGE[paymentOf(j, today)];
                    const deposit = DEPOSIT_BADGE[j.deposit.status];
                    return (
                      <tr key={j.shipment_id}>
                        <td>
                          <button type="button" className="fin-link" onClick={() => onOpen(j.shipment_id)}>
                            {j.job_no}
                          </button>
                          <span className="fin-sub">
                            {j.mbl} · {stageName[j.stage]}
                          </span>
                        </td>
                        <td>
                          <a className="fin-link" href={j.invoice_url} target="_blank" rel="noreferrer">
                            {j.invoice.invoice_no}
                          </a>
                          <span className="fin-sub">{shortDate(j.invoice.invoice_date)}</span>
                          {j.invoice.problems.length > 0 && (
                            <span className="badge missing" title={j.invoice.problems.join(" ")}>
                              Check totals
                            </span>
                          )}
                        </td>
                        <td>{shortDate(j.invoice.payable_by)}</td>
                        <td>{boxes(j.invoice)}</td>
                        <td className="num">{inr(j.invoice.total_excluding_tax)}</td>
                        <td className="num">{inr(j.invoice.total_tax)}</td>
                        <td className="num">
                          <strong>{inr(j.invoice.total)}</strong>
                        </td>
                        {charge !== "all" && <td className="num">{inr(amountOf(j))}</td>}
                        <td>
                          <span className={`badge ${tone}`}>{label}</span>
                          {j.payment.utr && <span className="fin-sub">UTR {j.payment.utr}</span>}
                        </td>
                        <td>
                          {deposit ? <span className={`badge ${deposit[0]}`}>{deposit[1]}</span> : <span className="fin-sub">-</span>}
                          {j.deposit.amount && deposit && <span className="fin-sub">{inr(j.deposit.amount)}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {waiting.length > 0 && (
            <p className="fin-note fin-waiting">
              Not invoiced yet: {waiting.map((j) => j.job_no.split("/").pop()).join(" · ")}. CMA CGM hasn't released
              their DO invoices on ODeX, or the agent hasn't collected them.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
