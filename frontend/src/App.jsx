import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import Board from "./components/Board.jsx";
import Detail from "./components/Detail.jsx";
import TopBar from "./components/TopBar.jsx";
import { indexFolder } from "./lib/folder.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Polls the shipment until `done` says the workflow has finished, or time runs out.
async function waitFor(id, done, onUpdate, { every, limit }) {
  const deadline = Date.now() + limit;
  let current = await api.shipment(id);
  onUpdate(current);
  while (!done(current) && Date.now() < deadline) {
    await sleep(every);
    current = await api.shipment(id);
    onUpdate(current);
  }
  return current;
}

const doRequestTask = (s) => s.tasks.find((t) => t.key === "odex_do_request");
const requested = (s) => s.status === "do_requested" || s.status === "edo_received";

function download(doc) {
  const link = document.createElement("a");
  link.href = doc.url;
  link.download = doc.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export default function App() {
  const [folder, setFolder] = useState(null);
  const [profile, setProfile] = useState(null);
  const [shipments, setShipments] = useState([]);
  const [load, setLoad] = useState({ state: "loading" });
  const [openId, setOpenId] = useState(null);
  const [section, setSection] = useState("shipment");
  const [focusKey, setFocusKey] = useState(null);
  const [focusSlot, setFocusSlot] = useState(null);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState(null);
  const [boardFocus, setBoardFocus] = useState(null);
  const [building, setBuilding] = useState(false);
  const [approving, setApproving] = useState(false);

  const say = useCallback((msg) => setToast({ msg, at: Date.now() }), []);

  const loadAll = useCallback(async () => {
    try {
      const [rawFolder, profileData, list] = await Promise.all([api.folder(), api.profile(), api.shipments()]);
      setFolder(indexFolder(rawFolder));
      setProfile(profileData);
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
    const timer = setTimeout(() => setToast(null), 4800);
    return () => clearTimeout(timer);
  }, [toast]);

  const replaceShipment = useCallback((updated) => {
    setShipments((list) => list.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  // The fixed details feed every shipment's letters, so the board is reloaded.
  const onProfileSaved = useCallback(async (updated) => {
    setProfile(updated);
    setShipments(await api.shipments());
  }, []);

  const open = useCallback(
    (id) => {
      const s = shipments.find((x) => x.id === id);
      if (!s) return;
      const detailsDone = folder.shipmentFields.every((f) => f.optional || s.field_status[f.key] === "ok");
      setSection(!detailsDone ? "shipment" : s.ready ? "review" : "documents");
      setOpenId(id);
    },
    [shipments, folder],
  );

  // A file attached in one job can raise or clear a duplicate flag in another.
  const back = useCallback(async () => {
    setBoardFocus(openId);
    setOpenId(null);
    try {
      setShipments(await api.shipments());
    } catch (err) {
      say(err.message);
    }
  }, [openId, say]);

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

  const build = useCallback(
    async (id) => {
      const before = shipments.find((s) => s.id === id);
      setBuilding(true);
      try {
        replaceShipment(await api.build(id));
        // The build workflow makes the PDF; wait for it, then download.
        const updated = await waitFor(id, (s) => !s.building, replaceShipment, { every: 1000, limit: 60000 });
        const review = updated.tasks.find((t) => t.key === "folder_review");
        if (updated.building) {
          say("Still building. Check that the workflow engine is running: make dev starts it.");
          return false;
        }
        if (review && review.status === "failed") {
          say(`The DO folder wasn't built. ${review.note}`);
          return false;
        }
        if (updated.folder) download(updated.folder);
        if (before && before.status !== updated.status) {
          say("DO folder built and downloaded. The job moved to DO folder ready.");
        } else {
          say("DO folder built and downloaded.");
        }
        return true;
      } catch (err) {
        say(err.message);
        return false;
      } finally {
        setBuilding(false);
      }
    },
    [shipments, replaceShipment, say],
  );

  const approveDoRequest = useCallback(
    async (id) => {
      setApproving(true);
      try {
        replaceShipment(await api.completeTask(id, "odex_do_request"));
        // A browser run on ODeX takes minutes; a request raised by hand is recorded at once.
        const updated = await waitFor(
          id,
          (s) => requested(s) || doRequestTask(s)?.status === "failed",
          replaceShipment,
          { every: 2000, limit: 15 * 60 * 1000 },
        );
        const task = doRequestTask(updated);
        if (task && task.status === "failed") say(`The DO request didn't go through. ${task.note}`);
        else if (requested(updated)) say("DO request recorded. The job moved to DO requested.");
        else say("The DO request is still running. Open the job again later to see how it went.");
      } catch (err) {
        say(err.message);
      } finally {
        setApproving(false);
      }
    },
    [replaceShipment, say],
  );

  const newShipment = useCallback(async () => {
    try {
      const created = await api.createShipment();
      setShipments((list) => [created, ...list]);
      setQuery("");
      setSection("shipment");
      setOpenId(created.id);
      setFocusKey("mbl_no");
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
      <TopBar query={query} setQuery={setQuery} showSearch={!current} onNew={newShipment} onReset={reset} />
      {current ? (
        <Detail
          key={current.id}
          folder={folder}
          profile={profile}
          shipment={current}
          section={section}
          setSection={setSection}
          focusKey={focusKey}
          setFocusKey={setFocusKey}
          focusSlot={focusSlot}
          setFocusSlot={setFocusSlot}
          onBack={back}
          onMove={moveTo}
          onBuild={build}
          building={building}
          onApproveDoRequest={approveDoRequest}
          approving={approving}
          onShipmentSaved={replaceShipment}
          onProfileSaved={onProfileSaved}
          say={say}
        />
      ) : (
        <Board
          stages={folder.stages}
          shipments={shipments}
          query={query}
          focusId={boardFocus}
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
