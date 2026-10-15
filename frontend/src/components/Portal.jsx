import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtMoment } from "../lib/format.js";

const money = (n) => (n == null || n === "" ? "" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`);

function tone(item) {
  if (item.status === "Rejected") return "error";
  return item.waiting ? "missing" : "ready";
}

function Files({ ids, files, shipmentId }) {
  const shown = (ids || []).filter((id) => files[id]);
  if (!shown.length) return null;
  return (
    <p className="p-files">
      {shown.map((id) => (
        <a key={id} className="linkbtn" href={`/api/shipments/${shipmentId}/portal/files/${id}`} target="_blank" rel="noreferrer">
          {files[id]}
        </a>
      ))}
    </p>
  );
}

function Step({ n, title, empty, items, render }) {
  const latest = items[0];
  return (
    <li className={`p-step${latest ? ` ${tone(latest)}` : " none"}`}>
      <div className="p-head">
        <span className="p-num">{n}</span>
        <h4>{title}</h4>
        {latest && <span className={`badge ${tone(latest)}`}>{latest.status}</span>}
      </div>
      {items.length === 0 ? (
        <p className="p-empty">{empty}</p>
      ) : (
        items.map((item) => (
          <div key={item.ref} className="p-item">
            <p className="p-ref">
              <span className="mono">{item.ref}</span> · {item.bl_no} · {fmtMoment(item.created_at)}
            </p>
            <div className="p-facts">{render(item)}</div>
            {item.reason && <p className="problems">{item.reason}</p>}
          </div>
        ))
      )}
    </li>
  );
}

const Fact = ({ label, value, code }) =>
  value ? (
    <div>
      <span className="k">{label}</span>
      <span className={code ? "v mono" : "v"}>{value}</span>
    </div>
  ) : null;

// What the local portals hold for this job: CMA CGM's answers on ODeX and the RTGS site.
export default function Portal({ shipment }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      api
        .portal(shipment.id)
        .then((d) => live && (setData(d), setError(null)))
        .catch((err) => live && setError(err.message));
    load();
    // Autoplay on the control panel answers within seconds, so the page follows along.
    const timer = setInterval(load, 4000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [shipment.id]);

  const files = (data && data.files) || {};
  return (
    <section>
      <h3 className="section-title">CMA CGM and ODeX</h3>
      <p className="section-sub">
        What this job's B/L numbers show on ODeX and the CMA CGM RTGS site: the invoice, the payments, the security
        deposit and the DO request. It updates by itself.
      </p>
      {error && <p className="problems">{error}</p>}
      {data === null && !error && <p className="hint">Loading…</p>}
      {data && !data.available && (
        <div className="banner warn">
          <div>
            <strong>The local portals are not running</strong>
            <p>
              Start them with <code>make portals</code>. This page picks them up on its own.
            </p>
          </div>
        </div>
      )}
      {data && data.available && (
        <ol className="portal">
          <Step
            n="1"
            title="DO invoice"
            empty="No invoice requested on ODeX yet."
            items={data.invoice_requests}
            render={(i) => (
              <>
                <Fact label="Invoice no." value={i.invoice_no} code />
                <Fact label="Amount" value={money(i.amount)} />
                <Files ids={[i.invoice_file]} files={files} shipmentId={shipment.id} />
              </>
            )}
          />
          <Step
            n="2"
            title="Payments (RTGS)"
            empty="No payment confirmed on the RTGS site yet."
            items={data.rtgs_requests}
            render={(p) => (
              <>
                <Fact
                  label="For"
                  value={p.invoices.map((r) => (r.security_deposit ? "Security deposit" : `Invoice ${r.invoice_no}`)).join(", ")}
                />
                <Fact label="UTR" value={p.utr} code />
                <Fact label="Amount" value={money(p.amount)} />
                <Fact label="Transferred" value={p.transfer_date} />
                <Files ids={p.receipts} files={files} shipmentId={shipment.id} />
              </>
            )}
          />
          <Step
            n="3"
            title="Security deposit"
            empty="No deposit raised on ODeX yet."
            items={data.deposits}
            render={(d) => (
              <>
                <Fact label="Amount" value={money(d.amount)} />
                <Fact label="UTR" value={d.utr} code />
                <Fact label="Bank" value={d.bank_name} />
                <Fact label="Refund to" value={d.refund_to} />
                <Files ids={[d.slip_file]} files={files} shipmentId={shipment.id} />
              </>
            )}
          />
          <Step
            n="4"
            title="DO request"
            empty="No DO request raised on ODeX yet."
            items={data.do_requests}
            render={(r) => (
              <>
                <Fact label="eDO no." value={r.edo_no} code />
                <Files ids={[r.edo_file]} files={files} shipmentId={shipment.id} />
              </>
            )}
          />
        </ol>
      )}
    </section>
  );
}
