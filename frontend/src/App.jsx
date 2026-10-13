import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import Board from "./components/Board.jsx";
import Detail from "./components/Detail.jsx";
import TopBar from "./components/TopBar.jsx";
import { indexDoSet } from "./lib/doSet.js";

function download(doc) {
  const link = document.createElement("a");
  link.href = doc.url;
  link.download = doc.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export default function App() {
  const [doSet, setDoSet] = useState(null);
  const [client, setClient] = useState(null);
  const [shipments, setShipments] = useState([]);
  const [load, setLoad] = useState({ state: "loading" });
  const [openId, setOpenId] = useState(null);
  const [section, setSection] = useState("shipment");
  const [focusKey, setFocusKey] = useState(null);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState(null);
  const [boardFocus, setBoardFocus] = useState(null);
  const [flashId, setFlashId] = useState(null);

  const say = useCallback((msg) => setToast({ msg, at: Date.now() }), []);

  const loadAll = useCallback(async () => {
    try {
      const [rawDoSet, clientData, list] = await Promise.all([api.doSet(), api.client(), api.shipments()]);
      setDoSet(indexDoSet(rawDoSet));
      setClient(clientData);
      setShipments(list);
      setLoad({ state: "ready" });
    } catch (err) {
      setLoad({ state: "error", message: err.message });
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!flashId) return undefined;
    const timer = setTimeout(() => setFlashId(null), 1800);
    return () => clearTimeout(timer);
  }, [flashId]);

  const replaceShipment = useCallback((updated) => {
    setShipments((list) => list.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  // A client profile change affects every shipment's completion, so the board is reloaded.
  const onClientSaved = useCallback(async (updated) => {
    setClient(updated);
    setShipments(await api.shipments());
  }, []);

  const open = useCallback(
    (id) => {
      const s = shipments.find((x) => x.id === id);
      if (!s) return;
      const detailsDone = doSet.shipmentFields.every((f) => s.field_status[f.key] === "ok");
      const firstOpen = s.pages.findIndex((p) => p.status !== "done");
      setSection(!detailsDone || firstOpen === -1 ? "shipment" : s.pages[firstOpen].id);
      setOpenId(id);
    },
    [shipments, doSet],
  );

  const back = useCallback(() => {
    setBoardFocus(openId);
    setOpenId(null);
  }, [openId]);

  const moveTo = useCallback(
    async (id, to) => {
      try {
        replaceShipment(await api.move(id, to));
      } catch (err) {
        say(err.message);
      }
    },
    [replaceShipment, say],
  );

  const generate = useCallback(
    async (id) => {
      const before = shipments.find((s) => s.id === id);
      try {
        const updated = await api.generate(id);
        replaceShipment(updated);
        if (updated.document) download(updated.document);
        if (before && before.status === "pending" && updated.status === "ready") {
          setOpenId(null);
          setBoardFocus(id);
          setFlashId(id);
          say("DO set generated and downloaded. The shipment moved to Docs ready.");
        } else {
          say("DO set generated and downloaded.");
        }
      } catch (err) {
        say(err.message);
      }
    },
    [shipments, replaceShipment, say],
  );

  const newShipment = useCallback(async () => {
    try {
      const created = await api.createShipment();
      setShipments((list) => [created, ...list]);
      setQuery("");
      setSection("shipment");
      setOpenId(created.id);
      setFocusKey("bl_no");
    } catch (err) {
      say(err.message);
    }
  }, [say]);

  const reset = useCallback(async () => {
    try {
      await api.resetDemo();
      setOpenId(null);
      setQuery("");
      await loadAll();
      say("Demo data reset.");
    } catch (err) {
      say(err.message);
    }
  }, [loadAll, say]);

  if (load.state === "loading") return <p className="boot">Loading the board…</p>;
  if (load.state === "error") {
    return (
      <div className="boot-panel" role="alert">
        <h2>The board can't load</h2>
        <p>
          {load.message} Start the API with <code>make api</code> or <code>make dev</code>, then try again.
        </p>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            setLoad({ state: "loading" });
            loadAll();
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  const current = openId ? shipments.find((s) => s.id === openId) : null;
  return (
    <div className="app">
      <TopBar
        clientName={client.name}
        query={query}
        setQuery={setQuery}
        showSearch={!current}
        onNew={newShipment}
        onReset={reset}
      />
      {current ? (
        <Detail
          key={current.id}
          doSet={doSet}
          client={client}
          shipment={current}
          section={section}
          setSection={setSection}
          focusKey={focusKey}
          setFocusKey={setFocusKey}
          onBack={back}
          onMove={moveTo}
          onGenerate={generate}
          onShipmentSaved={replaceShipment}
          onClientSaved={onClientSaved}
          say={say}
        />
      ) : (
        <Board
          stages={doSet.stages}
          shipments={shipments}
          clientName={client.name}
          query={query}
          focusId={boardFocus}
          flashId={flashId}
          onOpen={open}
          onMove={moveTo}
        />
      )}
      <div className="toast-wrap" role="status" aria-live="polite">
        {toast && (
          <div className="toast" key={toast.at}>
            {toast.msg}
          </div>
        )}
      </div>
    </div>
  );
}
