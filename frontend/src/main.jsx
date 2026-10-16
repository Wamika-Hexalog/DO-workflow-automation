import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
// The brand face is Helvetica, which every Mac has. Geist Mono is bundled for codes, so the demo runs offline.
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/500.css";
import "@fontsource/geist-mono/600.css";
import "./styles.css";
import { api } from "./api.js";
import App from "./App.jsx";
import Login from "./components/Login.jsx";

// Browser history is login -> home (the board) -> a job or the FAQ. Back from a job or the
// FAQ comes home (App.jsx); back from home comes here to the login page and signs out.
function Root() {
  const [user, setUser] = useState(undefined); // undefined: still asking the API

  const signOut = useCallback(() => {
    api.logout().catch(() => {});
    setUser(null);
  }, []);

  useEffect(() => {
    api.me().then(
      (me) => {
        const view = window.history.state?.view;
        if (view === "login") return signOut(); // reloaded on the login page
        if (!view) {
          window.history.replaceState({ view: "login" }, "");
          window.history.pushState({ view: "home" }, "");
        } else if (view !== "home") {
          window.history.back(); // reloaded on a job or the FAQ: the entry before is home
        }
        setUser(me.username);
      },
      () => {
        window.history.replaceState({ view: "login" }, "");
        setUser(null);
      },
    );
    const onPop = (event) => {
      if (event.state?.view === "login") signOut();
    };
    const onExpired = () => {
      setUser(null);
      window.history.pushState({ view: "login" }, "");
    };
    window.addEventListener("popstate", onPop);
    window.addEventListener("signed-out", onExpired);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("signed-out", onExpired);
    };
  }, [signOut]);

  if (user === undefined) return <p className="boot">Loading…</p>;
  if (user === null) {
    return (
      <Login
        onSignedIn={(name) => {
          window.history.pushState({ view: "home" }, "");
          setUser(name);
        }}
      />
    );
  }
  return (
    <App
      user={user}
      onSignOut={() => {
        signOut();
        window.history.pushState({ view: "login" }, "");
      }}
    />
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
